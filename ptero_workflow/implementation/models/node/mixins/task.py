from ...job import Job
from sqlalchemy.orm.session import object_session
import requests
import simplejson


class TaskPetriMixin(object):
    def get_petri_transitions(self):
        transitions = []

        input_deps_place = self._attach_input_deps(transitions)

        split_place = self._attach_split(transitions, input_deps_place)
        action_place = self._attach_action(transitions, split_place)
        join_place = self._attach_join(transitions, action_place)

        self._attach_output_deps(transitions, join_place)

        return transitions

    def _attach_input_deps(self, transitions):
        transitions.append({
            'inputs': [o.success_place_pair_name(self) for o in self.input_nodes],
            'outputs': [self.ready_place_name],
        })

        return self.ready_place_name

    def _attach_output_deps(self, transitions, internal_success_place):
        success_outputs = [self.success_place_pair_name(o) for o in self.output_nodes]
        success_outputs.append(self.success_place_pair_name(self.parent))
        transitions.append({
            'inputs': [internal_success_place],
            'outputs': success_outputs,
        })

    def _attach_split(self, transitions, ready_place):
        return ready_place

    def _attach_join(self, transitions, action_done_place):
        return action_done_place

    def _method_place_name(self, method, kind):
        return '%s-%s-%s' % (self.unique_name, method, kind)

    def _attach_action(self, transitions, action_ready_place):
        input_place_name = action_ready_place
        success_places = []
        for method in self.method_list:
            success_place, failure_place = method._attach(transitions,
                    input_place_name)
            input_place_name = failure_place
            success_places.append(success_place)

        for sp in success_places:
            transitions.append({
                'inputs': [sp],
                'outputs': [self.success_place_name],
            })

        return self.success_place_name

    def execute(self, body_data, query_string_data):
        color = body_data['color']
        group = body_data['group']
        response_links = body_data['response_links']

        method_name = query_string_data['method']
        method = self.methods[method_name]
        method.execute(color, group, response_links)

    def ended(self, body_data, query_string_data):
        job_id = body_data.pop('jobId')

        s = object_session(self)
        job = s.query(Job).filter_by(node=self, job_id=job_id).one()

        if body_data['exitCode'] == 0:
            outputs = simplejson.loads(body_data['stdout'])
            self.set_outputs(outputs, job.color)
            s.commit()
            return requests.put(job.response_links['success'].url)

        else:
            return requests.put(job.response_links['failure'].url)
