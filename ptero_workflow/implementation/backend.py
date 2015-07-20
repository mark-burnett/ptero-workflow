from . import models
from .models.execution.execution_base import Execution
from . import tasks
from . import translator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm import joinedload
from ptero_workflow.implementation import exceptions
from ptero_workflow.implementation.validators import validate_unique_links
import os
import logging
import re

LOG = logging.getLogger(__name__)


_TASK_BASE = 'ptero_workflow.implementation.celery_tasks.'


class Backend(object):
    def __init__(self, session, celery_app):
        self.session = session
        self.celery_app = celery_app

    @property
    def submit_net_task(self):
        return self.celery_app.tasks[_TASK_BASE + 'submit_net.SubmitNet']

    @property
    def http_task(self):
        return self.celery_app.tasks['ptero_common.celery.http.HTTP']

    def create_workflow(self, workflow_data):
        try:
            workflow = self._save_workflow(workflow_data)
        except IntegrityError as e:
            if 'name' in workflow_data:
                sqlite_error = 'UNIQUE constraint failed: workflow.name' == e.orig.message
                postgres_error = re.search(
                        "Key.*%s.*already exists" % workflow_data['name'],
                        e.orig.message) is not None
                if sqlite_error or postgres_error:
                    raise exceptions.NonUniqueNameError(
                        "Workflow with name '%s' already exists" % workflow_data['name'])
                else:
                    raise exceptions.UnknownIntegrityError('Unknown IntegrityError: %s' % e.message)
            else:
                raise e
        self.submit_net_task.delay(workflow.id)
        return workflow.id, workflow.as_dict(detailed=False)

    def submit_net(self, workflow_id):
        workflow = self.session.query(models.Workflow).get(workflow_id)
        petri_data = translator.build_petri_net(workflow)
        self.http_task.delay('PUT', self._petri_submit_url(workflow.net_key), **petri_data)

    def _petri_submit_url(self, net_key):
        return 'http://%s:%d/v1/nets/%s' % (
            os.environ.get('PTERO_PETRI_HOST', 'localhost'),
            int(os.environ.get('PTERO_PETRI_PORT', 80)),
            net_key,
        )

    def _save_workflow(self, workflow_data):
        self._ensure_required_inputs(workflow_data)

        workflow = models.Workflow(name=workflow_data.get('name'))

        root_data = {
            'methods': [
                {
                    'name': 'root',
                    'parameters': {
                        'tasks': workflow_data['tasks'],
                        'links': workflow_data['links'],
                        'webhooks': workflow_data.get('webhooks', {}),
                    },
                    'service': 'workflow',
                },
            ],
        }

        workflow.root_task = tasks.build_task('root', root_data, workflow)
        workflow.root_task.topological_index=-1

        models.TaskExecution(task=workflow.root_task, color=0, parent_color=None,
                colors=[0], begins=[], workflow=workflow, data={})

        tasks.create_input_holder(workflow.root_task, workflow, workflow_data['inputs'],
                color=workflow.color, parent_color=workflow.parent_color)

        dummy_output_task = models.InputHolder(name='dummy output task',
                workflow=workflow)
        self.session.add(dummy_output_task)

        validate_unique_links(workflow_data['links'])
        link = models.Link(source_task=workflow.root_task,
            destination_task=dummy_output_task)
        self.session.add(link)
        for link_data in workflow_data['links']:
            if 'output connector' == link_data['destination']:
                for source_property, destination_part in link_data['dataFlow'].items():
                    if isinstance(destination_part, basestring):
                        self.session.add(models.DataFlowEntry(source_property=destination_part,
                            destination_property=destination_part,
                            link=link))
                    else:
                        for destination_property in destination_part:
                            self.session.add(models.DataFlowEntry(source_property=destination_property,
                                destination_property=destination_property,
                                link=link))

        self.session.add(workflow)
        self.session.commit()

        workflow.root_task.create_input_sources(self.session, [])

        self.session.commit()

        return workflow

    @staticmethod
    def _ensure_required_inputs(workflow_data):
        required_inputs = set()
        for link in workflow_data['links']:
            if link['source'] == 'input connector':
                required_inputs.update(link['dataFlow'].keys())

        supplied_inputs = set(workflow_data['inputs'].keys())
        missing_inputs = required_inputs - supplied_inputs
        if missing_inputs:
            raise exceptions.MissingInputsError("Missing required inputs: %s" %
                    ', '.join(sorted(missing_inputs)))

    def _get_workflow_eagerly(self, workflow_id):
        workflow = self._get_workflow(workflow_id)

        # Here we load entities and eagerly-load some of their properties.
        # Later, when those entities are iterated through we won't have to issue
        # SQL per-entity.
        m = models
        method_lists = self.session.query(m.MethodList).\
                options(
                        joinedload(m.MethodList.method_list),
                        joinedload(m.MethodList.webhooks),
                ).filter_by(workflow_id=workflow_id).all()

        dags = self.session.query(m.DAG).\
                options(
                        joinedload(m.DAG.children),
                        joinedload(m.DAG.webhooks),
                ).filter_by(workflow_id=workflow_id).all()

        shell_commands = self.session.query(m.ShellCommand).\
                options(joinedload(m.ShellCommand.webhooks)).\
                filter_by(workflow_id=workflow_id).all()


        return workflow

    def get_workflow_id_from_execution_id(self, execution_id):
        execution = self.session.query(Execution).get(execution_id)
        if execution is not None:
            return execution.workflow_id
        else:
            raise exceptions.NoSuchEntityError(
                    "Execution with id %s was not found." % execution_id)

    def get_workflow_id_from_task_id(self, task_id):
        task = self.session.query(models.Task).get(task_id)
        if task is not None:
            return task.workflow_id
        else:
            raise exceptions.NoSuchEntityError(
                    "Task with id %s was not found." % task_id)

    def get_workflow_id_from_method_id(self, method_id):
        method = self.session.query(models.Method).get(method_id)
        if method is not None:
            return method.workflow_id
        else:
            raise exceptions.NoSuchEntityError(
                    "Method with id %s was not found." % method_id)

    def _get_workflow(self, workflow_id):
        workflow = self.session.query(models.Workflow).get(workflow_id)
        if workflow is not None:
            return workflow
        else:
            raise exceptions.NoSuchEntityError(
                    "Workflow with id %s was not found." % workflow_id)

    def get_workflow(self, workflow_id):
        workflow = self._get_workflow(workflow_id)
        return {
                'name': workflow.name,
                'status': workflow.status,
        }

    def get_workflow_submission_data(self, workflow_id):
        return self._get_workflow_eagerly(workflow_id).as_dict(detailed=False)

    def get_workflow_by_name(self, workflow_name):
        try:
            workflow = self.session.query(models.Workflow).filter_by(
                name=workflow_name).one()
            return workflow.id, {
                    'name': workflow.name,
                    'status': workflow.status,
            }
        except NoResultFound:
            raise exceptions.NoSuchEntityError(
                    "Workflow with name %s was not found." % workflow_name)

    def cancel_workflow(self, workflow_id):
        self._get_workflow_eagerly(workflow_id).cancel()
        self.session.commit()

    def get_workflow_status(self, workflow_id):
        return self._get_workflow(workflow_id).status

    def get_workflow_details(self, workflow_id):
        m = models
        method_lists = self.session.query(m.MethodList).\
                options(joinedload(m.MethodList.executions)).\
                filter_by(workflow_id=workflow_id).all()

        dags = self.session.query(m.DAG).\
                options(joinedload(m.DAG.executions)).\
                filter_by(workflow_id=workflow_id).all()

        shell_commands = self.session.query(m.ShellCommand).\
                options(joinedload(m.ShellCommand.executions)).\
                filter_by(workflow_id=workflow_id).all()
        return self._get_workflow_eagerly(workflow_id).as_dict(detailed=True)

    def get_workflow_skeleton(self, workflow_id):
        workflow = self._get_workflow(workflow_id)

        # Here we load entities and eagerly-load some of their properties.
        # Later, when those entities are iterated through we won't have to issue
        # SQL per-entity.
        m = models
        method_lists = self.session.query(m.MethodList).\
                options(
                        joinedload(m.MethodList.method_list),
                ).filter_by(workflow_id=workflow_id).all()

        dags = self.session.query(m.DAG).\
                options(
                        joinedload(m.DAG.children),
                ).filter_by(workflow_id=workflow_id).all()

        return workflow.as_skeleton_dict()

    def get_workflow_outputs(self, workflow_id):
        return self._get_workflow(workflow_id).get_outputs()

    def get_workflow_executions(self, workflow_id, since=None):
        query = self.session.query(models.Execution)

        if since is not None:
            query = query.join(models.ExecutionStatusHistory).\
                    filter(models.Execution.workflow_id == workflow_id,
                            models.ExecutionStatusHistory.timestamp > since)
        else:
            query = query.filter(models.Execution.workflow_id == workflow_id)

        executions = query.all()

        if executions:
            timestamp = max([e.update_timestamp for e in executions])
            return [e.as_dict_for_executions_report() for e in executions], timestamp
        else:
            return [], None



    def _get_execution(self, execution_id):
        execution = self.session.query(Execution).get(execution_id)
        if execution is not None:
            return execution
        else:
            raise exceptions.NoSuchEntityError(
                    "Execution with id %s was not found." % execution_id)

    def get_execution(self, execution_id):
        return self._get_execution(execution_id).as_dict(detailed=False)

    def update_execution(self, execution_id, update_data):
        execution = self._get_execution(execution_id)

        try:
            execution.update(update_data)
        except exceptions.UpdateError:
            self.session.rollback()
            raise

        self.session.commit()
        return execution.as_dict(detailed=False)

    def handle_task_callback(self, task_id, callback_type, body_data,
            query_string_data):
        task = self.session.query(models.Task
                ).filter_by(id=task_id).one()
        task.handle_callback(callback_type, body_data, query_string_data)

    def handle_method_callback(self, method_id, callback_type, body_data,
            query_string_data):
        method = self.session.query(models.Method
                ).filter_by(id=method_id).one()
        method.handle_callback(callback_type, body_data, query_string_data)

    def cleanup(self):
        self.session.rollback()
