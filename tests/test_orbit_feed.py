"""Public ticket-store behavior at the Orbit subprocess boundary."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kinetic import orbit_store, state
from kinetic.tracker import ROOT, adapter_defaults


class FeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name)
        self.issue = {'id': 'orb-a', 'updated_at': 'v1', 'status': 'running',
                      'labels': ['kinetic-run-r'],
                      'properties': {'kinetic': {'repo': 'api', 'kind': 'agent'}}}
        self.requests = []
        self.fail_batch = False
        self.conflict = False
        self.events = []
        self.reject = False
        self.store = orbit_store.make({'anchor': str(self.run), 'published': True,
                                      'names': adapter_defaults(ROOT / 'trackers/orbit.md')})
        self.st = {'run_id': 'r', 'run_dir': str(self.run), 'slices': []}
        patcher = patch('subprocess.run', side_effect=self.command)
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, args, **kw):
        command = args[3]
        self.requests.append((args, kw))
        if command == 'capabilities':
            data = {'contract': '1.0.0', 'board': {'contract': '1.0.0', 'compatible': True},
                    'capabilities': sorted(orbit_store.REQUIRED_CAPABILITIES), 'modules': []}
        elif command == 'changes':
            data = {'cursor': 'head', 'events': self.events}

        elif command == 'list':
            data = {'issues': [self.issue]}
        elif command == 'show':
            data = self.issue
        elif command == 'batch':
            if self.reject:
                self.reject = False
                return subprocess.CompletedProcess(args, 1, json.dumps({'ok': False, 'error': {
                    'code': 'POLICY_REJECTED', 'message': 'missing verify evidence'}}), '')
            if self.fail_batch:
                raise OSError('lost connection')
            if self.conflict:
                self.conflict = False
                self.issue['updated_at'] = 'v2'
                return subprocess.CompletedProcess(args, 1, json.dumps({'ok': False, 'error': {
                    'code': 'VERSION_CONFLICT', 'message': 'stale'}}), '')
            data = {'keys': {}, 'results': []}
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, 0, json.dumps({'ok': True, 'data': data}), '')

    def test_repeated_reads_resume_feed_without_listing_unchanged_board(self):
        self.assertIn('orb-a', self.store.read(self.run, self.st))
        self.requests.clear()
        self.assertIn('orb-a', self.store.read(self.run, self.st))
        commands = [args[3] for args, _ in self.requests]
        self.assertEqual(commands, ['changes'])
        self.assertIn('--since', self.requests[0][0])

    def test_lost_write_reuses_idempotency_key_and_versions(self):
        before = self.store.read(self.run, self.st)
        after = {'orb-a': dict(before['orb-a'], worktree='/work')}
        self.fail_batch = True
        with self.assertRaises(OSError):
            self.store.write(self.run, self.st, before, after)
        self.fail_batch = False
        self.store.write(self.run, self.st, before, after)
        batches = [(a, k) for a, k in self.requests if a[3] == 'batch']
        self.assertEqual(len(batches), 2)
        keys = [a[a.index('--idempotency-key') + 1] for a, _ in batches]
        self.assertEqual(keys[0], keys[1])
        for _, kw in batches:
            self.assertTrue(all(o['if_version'] == 'v1' for o in json.loads(kw['input'])['operations']))

    def test_conflicting_write_refreshes_version_and_retries(self):
        before = self.store.read(self.run, self.st)
        self.conflict = True
        self.store.write(self.run, self.st, before, {'orb-a': dict(before['orb-a'], worktree='/work')})
        batches = [k for a, k in self.requests if a[3] == 'batch']
        self.assertEqual(len(batches), 2)
        self.assertEqual(json.loads(batches[-1]['input'])['operations'][0]['if_version'], 'v2')

    def test_new_store_instance_resumes_durable_snapshot_and_fetches_changes(self):
        self.store.read(self.run, self.st)
        self.issue['properties']['kinetic']['worktree'] = '/new'
        self.events = [{'kind': 'issue.updated', 'issue': 'orb-a'}]
        restored = orbit_store.make(dict(self.store.config))
        self.assertEqual(restored.read(self.run, self.st)['orb-a']['worktree'], '/new')
        self.issue['labels'] = []
        self.assertEqual(restored.read(self.run, self.st), {})

    def test_policy_rejection_allows_a_corrected_write(self):
        before = self.store.read(self.run, self.st)
        self.reject = True
        with self.assertRaises(orbit_store.OrbitStoreError):
            self.store.write(self.run, self.st, before, {'orb-a': dict(before['orb-a'], worktree='/bad')})
        self.store.write(self.run, self.st, before, {'orb-a': dict(before['orb-a'], worktree='/corrected')})
        batches = [args for args, _ in self.requests if args[3] == 'batch']
        self.assertEqual(len(batches), 2)
