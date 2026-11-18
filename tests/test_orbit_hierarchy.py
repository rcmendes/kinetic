"""Spec publication at the Orbit CLI boundary."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kinetic import orbit_store
from kinetic.tracker import ROOT, adapter_defaults


class HierarchyTest(unittest.TestCase):
    def test_existing_issue_is_linked_without_being_moved(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            store = orbit_store.make({'anchor': tmp, 'names': adapter_defaults(ROOT / 'trackers/orbit.md')})
            st = {'run_id': 'r', 'run_dir': tmp, 'ref': 'orb-user', 'slices': []}
            writes = []

            def command(args, **kwargs):
                name = args[3]
                if name == 'capabilities':
                    data = {'contract': '1.0.0', 'capabilities': list(orbit_store.REQUIRED_CAPABILITIES),
                            'board': {'compatible': True, 'contract': '1.0.0'}}
                elif name == 'list':
                    data = {'issues': []}
                elif name == 'show':
                    data = {'id': 'orb-user', 'updated_at': 'v1'}
                elif name == 'batch':
                    writes.extend(json.loads(kwargs['input'])['operations'])
                    data = {'keys': {'kinetic:r:spec': 'orb-spec'}}
                else:
                    raise AssertionError(args)
                return subprocess.CompletedProcess(args, 0, json.dumps({'ok': True, 'data': data}), '')

            with patch('subprocess.run', side_effect=command):
                result = store.publish_spec(run, st, 'Spec', 'Body')
            self.assertEqual(result['ref'], 'orb-spec')
            spec = next(op for op in writes if op['op'] == 'add')
            self.assertEqual(spec['type'], 'epic')
            self.assertNotIn('parent', spec)
            self.assertTrue(any(op['op'] == 'link' and op['to'] == 'orb-user'
                                and op['blocking'] is False for op in writes))
            self.assertFalse(any(op.get('id') == 'orb-user' for op in writes))
            self.assertFalse(any(op['op'] == 'annotate' for op in writes))

    def test_native_spec_gate_and_user_issue_stay_independent(self):
        from tests.test_orbit_store import make_orbit_run, orbit_json
        from kinetic import state
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            self.assertTrue(orbit_json(board, 'setup', str(ROOT / 'profiles/kinetic.toml')))
            user = orbit_json(board, 'add', 'User request')['id']
            raw = state.load(run, tickets=False)
            raw['ref'] = user
            state.save(run, raw)
            (run / 'spec.md').write_text('# Feature\n\nImplement the request.')
            result = state.publish_spec(run, 'Feature')
            spec = orbit_json(board, 'show', result['ref'])
            self.assertEqual((spec['type'], spec['parent'], spec['status']),
                             ('epic', None, 'needs_decision'))
            self.assertTrue(any(link.get('target') == user and not link['blocking'] for link in spec['links']))
            self.assertEqual(orbit_json(board, 'show', user)['status'], 'todo')

    def test_native_three_level_hierarchy_keeps_spec_open_after_slice_finishes(self):
        from tests.test_orbit_store import make_orbit_run, orbit_json
        from kinetic import state
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            orbit_json(board, 'setup', str(ROOT / 'profiles/kinetic.toml'))
            (run / 'spec.md').write_text('# Feature\n\nImplement the request.')
            spec = state.publish_spec(run, 'Feature')['ref']
            state.record_gate(run, 'spec', True)
            state.set_slices(run, ['slice'])
            state.set_ticket(run, 'human', repo='api', slice='slice', kind='human')
            ids = state.publish_tickets(run)
            shown_slice = orbit_json(board, 'show', ids['slice'])
            shown_ticket = orbit_json(board, 'show', ids['human'])
            self.assertEqual((shown_slice['type'], shown_slice['parent']), ('task', spec))
            self.assertEqual((shown_ticket['type'], shown_ticket['parent']),
                             ('sub-task', ids['slice']))
            state.human_done(run, ids['human'])
            self.assertEqual(orbit_json(board, 'show', ids['slice'])['status'], 'done')
            self.assertEqual(orbit_json(board, 'show', spec)['status'], 'todo')
