from .base import Base
from .output import Output
from sqlalchemy import Column, UniqueConstraint
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import backref, relationship
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.orm.session import object_session
import logging
import os
import simplejson


__all__ = ['Operation', 'InputHolderOperation']


LOG = logging.getLogger(__file__)


class Operation(Base):
    __tablename__ = 'operation'
    __table_args__ = (
        UniqueConstraint('parent_id', 'name'),
    )

    id        = Column(Integer, primary_key=True)
    parent_id = Column(Integer, ForeignKey('operation.id'), nullable=True)
    name      = Column(Text, nullable=False)
    type      = Column(Text, nullable=False)
    status = Column(Text)

    children = relationship('Operation',
            backref=backref('parent', uselist=False, remote_side=[id]),
            collection_class=attribute_mapped_collection('name'),
            cascade='all, delete-orphan')

    __mapper_args__ = {
        'polymorphic_on': 'type',
    }

    @classmethod
    def from_dict(cls, type, **kwargs):
        subclass = cls.subclass_for(type)
        return subclass(**kwargs)

    @classmethod
    def subclass_for(cls, type):
        mapper = inspect(cls)
        return mapper.polymorphic_map[type].class_

    @property
    def to_dict(self):
        result = self._as_dict_data
        result['type'] = self.type
        return result
    as_dict = to_dict

    @property
    def _as_dict_data(self):
        return {}

    @property
    def unique_name(self):
        return '-'.join(['op', str(self.id), self.name.replace(' ', '_')])

    @property
    def success_place_name(self):
        return '%s-success' % self.unique_name

    def success_place_pair_name(self, op):
        return '%s-success-for-%s' % (self.unique_name, op.unique_name)

    @property
    def ready_place_name(self):
        return '%s-ready' % self.unique_name

    @property
    def response_wait_place_name(self):
        return '%s-response-wait' % self.unique_name

    @property
    def response_callback_place_name(self):
        return '%s-response-callback' % self.unique_name

    def notify_callback_url(self, event):
        return 'http://%s:%d/v1/callbacks/operations/%d/events/%s' % (
            os.environ.get('PTERO_WORKFLOW_HOST', 'localhost'),
            int(os.environ.get('PTERO_WORKFLOW_PORT', 80)),
            self.id,
            event,
        )

    def get_petri_transitions(self):
        result = []

        # wait for all input ops
        result.append({
            'inputs': [o.success_place_pair_name(self) for o in self.input_ops],
            'outputs': [self.ready_place_name],
        })

        # send notification
        result.append({
            'inputs': [self.ready_place_name],
            'outputs': [self.response_wait_place_name],
            'action': {
                'type': 'notify',
                'url': self.notify_callback_url('execute'),
                'response_places': {
                    'success': self.response_callback_place_name,
                },
            }
        })

        # wait for response
        result.append({
            'inputs': [self.response_wait_place_name,
                self.response_callback_place_name],
            'outputs': [self.success_place_name],
        })

        success_outputs = [self.success_place_pair_name(o) for o in self.output_ops]
        success_outputs.append(self.success_place_pair_name(self.parent))
        result.append({
            'inputs': [self.success_place_name],
            'outputs': success_outputs,
        })

        return result

    @property
    def input_ops(self):
        source_ids = set([l.source_id for l in self.input_links])
        if source_ids:
            s = object_session(self)
            return s.query(Operation).filter(Operation.id.in_(source_ids)).all()
        else:
            return []

    @property
    def output_ops(self):
        destination_ids = set([l.destination_id for l in self.output_links])
        if destination_ids:
            s = object_session(self)
            return s.query(Operation).filter(
                    Operation.id.in_(destination_ids)).all()
        else:
            return []

    @property
    def real_child_ops(self):
        data = dict(self.children)
        del data['input connector']
        del data['output connector']
        return data.values()

    def get_output(self, name):
        return self.get_outputs().get(name)

    def get_outputs(self):
        return {o.name: o.value for o in self.outputs}

    def set_outputs(self, outputs):
        s = object_session(self)
        for name, value in outputs.iteritems():
            o = Output(name=name, operation=self,
                    serialized_value=simplejson.dumps(value))

    def get_inputs(self):
        result = {}
        for link in self.input_links:
            result[link.destination_property] =\
                    link.source_operation.get_output(link.source_property)

        return result

    def get_input(self, name):
        return self.get_inputs()[name]

    def execute(self, inputs):
        pass


class InputHolderOperation(Operation):
    __tablename__ = 'operation_input_holder'

    id = Column(Integer, ForeignKey('operation.id'), primary_key=True)

    __mapper_args__ = {
        'polymorphic_identity': '__input_holder',
    }

    def get_inputs(self):
        raise RuntimeError()

    def get_input(self, name):
        raise RuntimeError()

    def get_petri_transitions(self):
        return []


class InputConnectorOperation(Operation):
    __tablename__ = 'operation_input_connector'

    id = Column(Integer, ForeignKey('operation.id'), primary_key=True)

    __mapper_args__ = {
        'polymorphic_identity': 'input connector',
    }

    def get_output(self, name):
        return self.get_inputs().get(name)

    def get_outputs(self):
        return self.get_inputs()

    def get_inputs(self):
        return self.parent.get_inputs()

    def get_input(self, name):
        return self.parent.get_input(name)

    def get_petri_transitions(self):
        return [
            {
                'inputs': [self.parent.ready_place_name],
                'outputs': [self.success_place_name],
            },
            {
                'inputs': [self.success_place_name],
                'outputs': [self.success_place_pair_name(o) for o in self.output_ops],
            }
        ]


class OutputConnectorOperation(Operation):
    __tablename__ = 'operation_output_connector'

    id = Column(Integer, ForeignKey('operation.id'), primary_key=True)

    __mapper_args__ = {
        'polymorphic_identity': 'output connector',
    }

    def get_output(self, name):
        return self.get_input(name)

    def get_outputs(self):
        return self.get_inputs()

    def get_petri_transitions(self):
        return []


class ModelOperation(Operation):
    __tablename__ = 'operation_model'

    id = Column(Integer, ForeignKey('operation.id'), primary_key=True)

    __mapper_args__ = {
        'polymorphic_identity': 'model',
    }

    def get_output(self, name):
        return self.children['output connector'].get_output(name)

    def get_outputs(self):
        return self.children['output connector'].get_outputs()

    def get_petri_transitions(self):
        result = []

        if self.input_ops:
            result.append({
                'inputs': [o.success_place_pair_name(self) for o in self.input_ops],
                'outputs': [self.ready_place_name],
            })

        if self.output_ops:
            success_outputs = [self.success_place_pair_name(o) for o in self.output_ops]
            if self.parent:
                success_outputs.append(self.success_place_pair_name(self.parent))
            result.append({
                'inputs': [self.success_place_name],
                'outputs': success_outputs,
            })

        result.append({
            'inputs': [o.success_place_pair_name(self)
                for o in self.real_child_ops],
            'outputs': [self.success_place_name],
            'action': {
                'type': 'notify',
                'url': self.notify_callback_url('done'),
            },
        })

        for child in self.children.itervalues():
            result.extend(child.get_petri_transitions())

        return result


class CommandOperation(Operation):
    __tablename__ = 'operation_command'

    id = Column(Integer, ForeignKey('operation.id'), primary_key=True)

    __mapper_args__ = {
        'polymorphic_identity': 'command',
    }


class PassThroughOperation(Operation):
    __tablename__ = 'operation_pass_through'

    id = Column(Integer, ForeignKey('operation.id'), primary_key=True)

    __mapper_args__ = {
        'polymorphic_identity': 'pass-through',
    }

    def execute(self, inputs):
        self.set_outputs(inputs)
