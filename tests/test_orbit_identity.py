"""Ticket-store lease behavior at the Orbit subprocess boundary."""
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from kinetic import orbit_store


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.st = {'run_id': 'r', 'run_dir': tmp.name}
        self.operations = []
        self.issue = {'status': 'running', 'assignee': 'kinetic-r-implementer-orb-a', 'updated_at': 'v1'}
        self.store = orbit_store.make({'anchor': '/board'})
        patcher = patch('subprocess.run', side_effect=self.command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, args, **kwargs):
        self.requests.append(args)
        if args[3] == 'capabilities':
            data = {'contract': '1.0.0', 'board': {'contract': '1.0.0', 'compatible': True},
                    'capabilities': sorted(orbit_store.REQUIRED_CAPABILITIES)}
        elif args[3] == 'show':
            data = self.issue
        elif args[3] == 'batch':
            self.operations.extend(json.loads(kwargs['input'])['operations'])
            data = {}
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({'ok': True, 'data': data}), '')

    def test_long_ticket_renews_the_exact_active_assignee(self):
        self.store.touch('/run', self.st, 'orb-a')
        self.store.touch('/run', self.st, 'orb-a')
        heartbeats = [op for op in self.operations if op['op'] == 'heartbeat']
        self.assertEqual(len(heartbeats), 2)
        self.assertTrue(all(op['by'] == 'kinetic-r-implementer-orb-a' and op['if_version'] == 'v1'
                            for op in heartbeats))

    def test_reviewer_never_renews_or_steals_author_lease(self):
        self.issue['status'] = 'in_review'
        self.issue['assignee'] = 'kinetic-r-spec-reviewer-orb-a'
        self.store.touch('/run', self.st, 'orb-a')
        self.assertFalse(any(args[3] in ('heartbeat', 'claim', 'assign') for args in self.requests))

    def test_different_active_assignee_is_left_alone(self):
        self.issue['assignee'] = 'human'
        self.store.touch('/run', self.st, 'orb-a')
        self.assertFalse(any(args[3] == 'heartbeat' for args in self.requests))
