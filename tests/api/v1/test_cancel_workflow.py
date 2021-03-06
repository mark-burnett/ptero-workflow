from ..base import BaseAPITest
import logging
from pprint import pformat
from tests import util

LOG = logging.getLogger(__name__)


class TestCancelWorkflow(BaseAPITest):
    def setUp(self):
        super(TestCancelWorkflow, self).setUp()
        self.canceled_listener = self.create_webhook_server([200])
        self.running_listener = self.create_webhook_server([200])

    @property
    def post_data(self):
        return {
                'tasks': {
                    'A': {
                        'methods': [
                            {
                                'name': 'execute',
                                'service': 'job',
                                'serviceUrl': util.shell_command_url(),
                                'parameters': {
                                    'commandLine': ['true'],
                                    "user": util.user(),
                                    "workingDirectory": util.working_directory(),
                                    "environment": util.environment_dict(),
                                    'webhooks': {
                                        'canceled': self.canceled_listener.url,
                                        }
                                    },
                                'webhooks': {
                                    'running': self.running_listener.url,
                                    }
                                }
                            ]
                        },
                    },
                'links': [
                    {
                        'source': 'input connector',
                        'destination': 'A',
                        'dataFlow': {
                            'in_a': 'param'
                            }
                        },
                    {
                        'source': 'A',
                        'destination': 'output connector',
                        'dataFlow': {
                            'result': 'out_a'
                            }
                        },
                    ],
                'inputs': {
                    'in_a': 'kittens',
                    },
                }


    def test_can_cancel(self):
        post_response = self.post(self.post_url, self.post_data)

        self.assertEqual(201, post_response.status_code)

        workflow_url = post_response.headers['Location']
        self.patch(workflow_url, data={'is_canceled':True})

        details_url = post_response.json()['reports']['workflow-details']
        details_response = self.get(details_url)
        self.assertEqual(200, details_response.status_code)
        LOG.warning(pformat(details_response.json()))

        status_url = post_response.json()['reports']['workflow-status']
        status_response = self.get(status_url)
        self.assertEqual(200, status_response.status_code)
        self.assertEqual(status_response.json()['status'], 'canceled')

        delete_response = self.delete(workflow_url)
        self.assertEqual(200, delete_response.status_code)

    def test_can_cancel_by_name(self):
        post_response = self.post(self.post_url, self.post_data)

        self.assertEqual(201, post_response.status_code)

        workflow_url = "%s?name=%s" % (self.post_url,
                post_response.json()['name'])
        self.patch(workflow_url, data={'is_canceled':True})

        details_url = post_response.json()['reports']['workflow-details']
        details_response = self.get(details_url)
        self.assertEqual(200, details_response.status_code)
        LOG.warning(pformat(details_response.json()))

        status_url = post_response.json()['reports']['workflow-status']
        status_response = self.get(status_url)
        self.assertEqual(200, status_response.status_code)
        self.assertEqual(status_response.json()['status'], 'canceled')

        delete_response = self.delete(workflow_url)
        self.assertEqual(200, delete_response.status_code)

    def test_jobs_canceled(self):
        post_response = self.post(self.post_url, self.post_data)

        self.assertEqual(201, post_response.status_code)

        self.running_listener.stop()

        workflow_url = post_response.headers['Location']
        self.patch(workflow_url, data={'is_canceled':True})

        self.canceled_listener.stop()

        delete_response = self.delete(workflow_url)
        self.assertEqual(200, delete_response.status_code)


class TestCancelSpawnedWorkflow(BaseAPITest):
    def post_data(self, running_webhook_url, canceled_webhook_url):
        return {
            "tasks": {
                "Spawner": {
                    "methods": [
                        {
                            "name": "execute",
                            "service": "job",
                            "serviceUrl": util.shell_command_url(),
                            "parameters": {
                                "commandLine": ["./spawn_workflow_command"],
                                "user": util.user(),
                                "workingDirectory": util.working_directory(),
                                "environment": util.environment_dict(),
                                }
                            }
                        ]
                    }
                },

            "links": [
                {
                    "source": "input connector",
                    "destination": "Spawner",
                    "dataFlow": {
                        "workflow_data": "workflow_data"
                        }
                    },
                {
                    "source": "Spawner",
                    "destination": "output connector"
                    }
                ],

            "inputs": {
                "workflow_data": self.sleeper_workflow(running_webhook_url,
                    canceled_webhook_url),
                }
            }

    def sleeper_workflow(self, running_webhook_url, canceled_webhook_url):
        return {
            "tasks": {
                "Sleeper": {
                    "methods": [
                        {
                            "name": "execute",
                            "service": "job",
                            "serviceUrl": util.shell_command_url(),
                            "parameters": {
                                "commandLine": ["sleep", "12345"],
                                "user": util.user(),
                                "workingDirectory": util.working_directory(),
                                "environment": {},
                            },
                            "webhooks": {
                                "running": running_webhook_url,
                                "canceled": canceled_webhook_url,
                                }
                            }
                        ]
                    }
                },
            "links": [
                {
                    "source": "input connector",
                    "destination": "Sleeper"
                    },
                {
                    "source": "Sleeper",
                    "destination": "output connector"
                    }
                ],
            "inputs": {
                },
            }


    def test_can_cancel(self):
        running_listener = self.create_webhook_server([200])
        canceled_listener = self.create_webhook_server([200])

        post_data = self.post_data(running_listener.url,
                canceled_listener.url)
        post_response = self.post(self.post_url, post_data)

        self.assertEqual(201, post_response.status_code)
        workflow_url = post_response.headers['Location']

        running_data = running_listener.stop()

        self.patch(workflow_url, data={'is_canceled':True})

        canceled_data = canceled_listener.stop()

        delete_response = self.delete(workflow_url)
        self.assertEqual(200, delete_response.status_code)
