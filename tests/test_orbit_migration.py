"""Legacy run migration through the CLI and a real temporary Orbit Board."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

from kinetic import orbit_store, state, status
from kinetic.tracker import ROOT, adapter_defaults
from tests.test_orbit_store import orbit_available


NEEDS_TOMLLIB = unittest.skipIf(sys.version_info < (3, 11), "applying a migration needs tomllib (Python 3.11+)")


class PythonVersionTest(unittest.TestCase):
    def test_apply_without_tomllib_names_the_python_requirement(self):
        with patch.dict(sys.modules, {'tomllib': None}):
            with self.assertRaisesRegex(orbit_store.OrbitStoreError, 'PYTHON_TOO_OLD'):
                orbit_store._migration_profile({'statuses': [], 'transitions': []}, temporary=False)


@unittest.skipUnless(orbit_available(), "native Orbit CLI not installed")
class MigrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.board = self.root / 'board'
        self.board.mkdir()
        self.env = {**os.environ, 'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.com',
                    'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@example.com'}
        self.command('init', '.')
        self.run = self.root / 'run'
        state.init(self.run, 'feature', 'user-request', ticket_store_record={
            'kind': 'orbit', 'config': {'anchor': str(self.board),
                                       'names': adapter_defaults(ROOT / 'trackers/orbit.md')}})
        st = state.load(self.run, tickets=False)
        self.run_id = st['run_id']
        label = 'kinetic-run-' + self.run_id
        self.user = self.command('add', 'User request')['id']
        self.a = self.command('add', 'API task', '--label', label)['id']
        self.b = self.command('add', 'Web task', '--label', label)['id']
        self.command('claim', self.a, '--by', 'kinetic-' + self.run_id)
        for tid, fields in ((self.a, {'repo': 'api', 'status': 'running'}),
                            (self.b, {'repo': 'web', 'status': 'todo',
                                      'blocked_by': [{'id': self.a, 'type': 'snapshot'}],
                                      'after': [self.a], 'ships_with': 'api-web'})):
            self.command('property', 'set', tid, 'kinetic', '--value', json.dumps(fields))
        self.before_user = self.command('show', self.user)

    def command(self, *args):
        done = subprocess.run(['orbit', '--dir', str(self.board), *args, '--json'],
                              cwd=self.board, capture_output=True, text=True, env=self.env)
        envelope = json.loads(done.stdout)
        self.assertTrue(envelope['ok'], envelope)
        return envelope['data']

    def migrate(self, apply=False):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = orbit_store.main(['migrate', '--run', str(self.run), *(['--apply'] if apply else [])])
        self.assertEqual(code, 0)
        return json.loads(out.getvalue())

    def test_old_run_reports_before_migration(self):
        rendered = status.render(state.load(self.run))
        self.assertIn('| ' + self.a + ' | agent | api | running |', rendered)
        self.assertIn(self.a + ' (snapshot)', rendered)

    def test_old_run_requires_migration_before_native_writes(self):
        before = self.command('show', self.a)
        with self.assertRaisesRegex(orbit_store.OrbitStoreError, 'MIGRATION_REQUIRED'):
            state.set_ticket(self.run, self.a, worktree='/work')
        self.assertEqual(self.command('show', self.a), before)

    @NEEDS_TOMLLIB
    def test_preview_is_read_only_and_apply_preserves_user_issue(self):
        before = self.command('show', self.b)
        preview = self.migrate()
        self.assertFalse(preview['applied'])
        self.assertEqual(self.command('show', self.b), before)
        self.assertEqual(self.command('capabilities')['workflow']['template'], 'standard')
        result = self.migrate(apply=True)
        self.assertTrue(result['applied'])
        user = self.command('show', self.user)
        # Available transitions/readiness reflect the explicitly changed Board workflow.
        self.assertEqual({k: v for k, v in user.items() if k not in ('allowed_transitions', 'ready')},
                         {k: v for k, v in self.before_user.items() if k not in ('allowed_transitions', 'ready')})
        a, b = self.command('show', self.a), self.command('show', self.b)
        self.assertEqual(a['status'], 'running')
        self.assertEqual(a['assignee'], orbit_store.holder(self.run_id, self.a, 'implementer'))
        self.assertNotIn('status', a['properties']['kinetic'])
        self.assertNotIn('blocked_by', b['properties']['kinetic'])
        self.assertTrue(any(link['type'] == 'blocks' and link['attributes']['edge'] == 'snapshot'
                            for link in b['links']))
        self.assertEqual(state.load(self.run)['tickets'][self.b]['ships_with'], 'api-web')
        self.assertEqual(self.migrate(apply=True)['migrated'], [])

    @NEEDS_TOMLLIB
    def test_migration_recovers_after_committed_batch_response_is_lost(self):
        unlink = Path.unlink
        def crash(path, *args, **kwargs):
            if path.name == 'orbit-write.json':
                raise OSError('lost response after migration commit')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', crash):
            with self.assertRaisesRegex(OSError, 'lost response'):
                orbit_store.migrate(self.run, apply=True)
        workflow = self.command('capabilities')['workflow']
        self.assertFalse(any(t['name'].startswith('kinetic_migrate_') for t in workflow['transitions']))
        self.assertTrue(self.migrate(apply=True)['applied'])
        self.assertFalse((self.run / '.kinetic/orbit-write.json').exists())
        self.assertEqual(state.load(self.run)['tickets'][self.b]['blocked_by'],
                         [{'id': self.a, 'type': 'snapshot'}])
