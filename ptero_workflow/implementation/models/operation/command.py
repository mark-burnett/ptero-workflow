from ..base import Base
from ..job import Job, ResponseLink
from .operation_base import Operation
from .mixins.command import OperationPetriMixin
from .mixins.parallel import ParallelPetriMixin
from sqlalchemy import Column, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import backref, relationship
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.orm.session import object_session
import os
import requests
import simplejson


__all__ = ['CommandOperation', 'Method']


class Method(Base):
    __tablename__ = 'operation_command_method'

    __table_args__ = (
        UniqueConstraint('operation_id', 'name'),
    )

    id = Column(Integer, primary_key=True)

    operation_id = Column(Integer, ForeignKey('operation.id'))
    name = Column(Text)

    index = Column(Integer, nullable=False, index=True)

    serialized_command_line = Column(Text, nullable=False)

    @property
    def command_line(self):
        return simplejson.loads(self.serialized_command_line)

    @command_line.setter
    def command_line(self, new_value):
        self.serialized_command_line = simplejson.dumps(new_value)


class CommandOperation(OperationPetriMixin, Operation):
    __tablename__ = 'operation_command'

    id = Column(Integer, ForeignKey('operation.id'), primary_key=True)

    methods = relationship('Method', backref='operation',
            collection_class=attribute_mapped_collection('name'),
            cascade='all, delete-orphan')

    method_list = relationship('Method', order_by=Method.index)

    __mapper_args__ = {
        'polymorphic_identity': 'command',
    }

    VALID_EVENT_TYPES = Operation.VALID_EVENT_TYPES.union(['execute', 'ended'])

    def execute(self, body_data, query_string_data):
        color = body_data['color']
        group = body_data['group']
        response_links = body_data['response_links']

        method_name = query_string_data['method']
        method = self.methods[method_name]

        job_id = self._submit_to_fork(color, method.command_line)

        job = Job(operation=self, method=method, color=color, job_id=job_id)
        s = object_session(self)
        for name, url in response_links.iteritems():
            link = ResponseLink(job=job, url=url, name=name)
            job.response_links[name] = link

        s.add(job)
        s.commit()

    def ended(self, body_data, query_string_data):
        job_id = body_data.pop('job_id')

        s = object_session(self)
        job = s.query(Job).filter_by(operation=self, job_id=job_id).one()

        if body_data['exit_code'] == 0:
            outputs = simplejson.loads(body_data['stdout'])
            self.set_outputs(outputs, job.color)
            s.commit()
            return requests.put(job.response_links['success'].url)

        else:
            return requests.put(job.response_links['failure'].url)

    def _submit_to_fork(self, color, command_line):
        body_data = self._fork_submit_data(color, command_line)
        response = requests.post(self._fork_submit_url,
                data=simplejson.dumps(body_data),
                headers={'Content-Type': 'application/json'})
        return response.json()['job_id']

    @property
    def _fork_submit_url(self):
        return 'http://%s:%d/v1/jobs' % (
            os.environ.get('PTERO_FORK_HOST', 'localhost'),
            int(os.environ.get('PTERO_FORK_PORT', 80)),
        )

    def _fork_submit_data(self, color, command_line):
        return {
            'command_line': command_line,
            'user': os.environ.get('USER'),
            'stdin': simplejson.dumps(self.get_inputs(color)),
            'callbacks': {
                'ended': self.event_url('ended'),
            },
        }
