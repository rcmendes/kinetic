"""One sample project's native lifecycle through public kinetic commands."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from kinetic import orbit_store, project, state, status, verify
from kinetic.tracker import ROOT, adapter_defaults
from tests.test_orbit_store import orbit_available


@unittest.skipUnless(orbit_available(), "native Orbit CLI not installed")
class NativeRunTest(unittest.TestCase):
    def test_sample_project_publishes_verifies_reviews_and_merges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, 'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.com',
                   'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@example.com'}
            anchor = project.create(root, 'sample')
            def orbit(*args):
                done = subprocess.run(['orbit', '--dir', str(anchor), *args, '--json'],
                                      cwd=anchor, env=env, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                return json.loads(done.stdout)['data']
            orbit('init', '.')
            orbit('setup', str(ROOT / 'profiles/kinetic.toml'))
            user = orbit('add', 'User request')['id']
            before_user = orbit('show', user)
            repo = anchor / 'svc'
            repo.mkdir()
            (repo / '.kinetic.json').write_text(json.dumps({'verify': 'true'}))
            run = project.run_dir(anchor, 'feature')
            state.init(run, 'feature', user, ticket_store_record={'kind': 'orbit', 'config': {
                'anchor': str(anchor), 'names': adapter_defaults(ROOT / 'trackers/orbit.md')}})
            state.set_repo(run, 'svc', str(repo), 'integration')
            (run / 'spec.md').write_text('# Feature\n\nBuild the sample.')
            spec = state.publish_spec(run, 'Feature')['ref']
            state.record_gate(run, 'spec', True)
            state.set_slices(run, ['core'])
            state.set_ticket(run, 'task', repo='svc', slice='core')
            mapping = state.publish_tickets(run)
            tid, slice_id = mapping['task'], mapping['core']
            state.record_gate(run, 'breakdown', True)
            self.assertEqual((orbit('show', slice_id)['parent'], orbit('show', tid)['parent']),
                             (spec, slice_id))
            state.set_ticket(run, tid, status='running')
            checked = verify.run(run, tid, 'svc', repo, anchor / '.kinetic/config.json', scope='ticket')
            self.assertEqual(checked['exit_code'], 0)
            state.submit_review(run, tid)
            state.record_review(run, tid, 'spec', 'approve')
            state.record_review(run, tid, 'standards', 'approve')
            state.set_ticket(run, tid, status='merged')
            self.assertEqual(orbit('show', spec)['status'], 'running')
            verify.run(run, None, 'svc', repo, anchor / '.kinetic/config.json')
            state.submit_review(run, spec, scope='integration')
            state.record_review(run, spec, 'integration', 'approve')
            self.assertEqual(orbit('show', spec)['status'], 'done')
            after_user = orbit('show', user)
            self.assertEqual({k: v for k, v in after_user.items() if k != 'dependents'},
                             {k: v for k, v in before_user.items() if k != 'dependents'})
            self.assertIn('| ' + tid + ' | agent | svc | merged |', status.render(state.load(run)))
