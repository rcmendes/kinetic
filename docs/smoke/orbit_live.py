"""Live kinetic run against a real Orbit 0.11 Board, driven through kinetic's public API.

Usage: PYTHONPATH=scripts python3.11 docs/smoke/orbit_live.py <empty scratch dir>
Needs `orbit` 0.11.0 or later on PATH. It plays implementer and reviewers itself, so it
checks the store and the state machine, not an agent harness.
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

from kinetic import frontier, orbit_store, project, state, status, verify
from kinetic.tracker import ROOT, adapter_defaults

root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
env = {**os.environ, 'GIT_AUTHOR_NAME': 'T', 'GIT_AUTHOR_EMAIL': 't@example.com',
       'GIT_COMMITTER_NAME': 'T', 'GIT_COMMITTER_EMAIL': 't@example.com'}
os.environ.update(env)
log = []
def step(msg): log.append(msg); print('STEP', msg, flush=True)

anchor = project.create(root, 'live')
def orbit(*a):
    d = subprocess.run(['orbit', '--dir', str(anchor), *a, '--json'], cwd=anchor, env=env, capture_output=True, text=True)
    assert d.returncode == 0, d.stdout + d.stderr
    return json.loads(d.stdout)['data']

orbit('init', '.'); orbit('setup', str(ROOT / 'profiles/kinetic.toml'))
user = orbit('add', 'User request: add health endpoints')['id']
before_user = orbit('show', user)
repos = {}
for name in ('svc', 'api'):
    p = anchor / name; p.mkdir()
    (p / '.kinetic.json').write_text(json.dumps({'verify': 'true'}))
    repos[name] = p
run = project.run_dir(anchor, 'live-1')
state.init(run, 'feature', user, ticket_store_record={'kind': 'orbit', 'config': {
    'anchor': str(anchor), 'names': adapter_defaults(ROOT / 'trackers/orbit.md')}})
for n, p in repos.items():
    state.set_repo(run, n, str(p), 'integration')
(run / 'spec.md').write_text('# Health endpoints\n\nAdd health endpoints to svc and api.')
spec = state.publish_spec(run, 'Health endpoints')['ref']
step(f'spec published {spec}; status={orbit("show", spec)["status"]}')
state.record_gate(run, 'spec', True)
step(f'spec gate approved; status={orbit("show", spec)["status"]}')

state.set_slices(run, ['core'])
state.set_ticket(run, 'a', repo='svc', slice='core')
state.set_ticket(run, 'b', repo='api', slice='core', blocked_by=[{'id': 'a', 'type': 'merge'}])
state.set_ticket(run, 'h', repo='svc', slice='core', kind='human')
m = state.publish_tickets(run)
a, b, h, sl = m['a'], m['b'], m['h'], m['core']
state.record_gate(run, 'breakdown', True)
step(f'published tickets a={a} b={b} h={h} slice={sl}')
tree = subprocess.run(['orbit', '--dir', str(anchor), 'tree', spec], cwd=anchor, capture_output=True, text=True).stdout
print(tree)
assert orbit('show', a)['parent'] == sl and orbit('show', sl)['parent'] == spec
assert orbit('show', b)['blocking'] == [a] or any(l['target'] == a for l in orbit('show', b)['links'])

st = state.load(run)
ready = frontier.ready(st)
step(f'frontier ready after breakdown: {ready}')
assert a in ready and b not in ready

# ticket a: dispatch, checkpoint, heartbeat, verify, submit, changes requested, fix, approve
state.set_ticket(run, a, status='running')
ia = orbit('show', a)
step(f'a running: status={ia["status"]} assignee={ia["assignee"]}')
state.record_checkpoint(run, a, 'endpoint skeleton written', next='add tests')
step(f'last checkpoint: {state.last_checkpoint(run, a)}')
cfg = anchor / '.kinetic/config.json'
verify.run(run, a, 'svc', repos['svc'], cfg, scope='ticket')
state.submit_review(run, a)
step(f'a submitted: status={orbit("show", a)["status"]}')
state.record_review(run, a, 'spec', 'approve')
state.record_review(run, a, 'standards', 'request-changes', 'missing test for 500 path')
step(f'a after request-changes: status={orbit("show", a)["status"]}')
verify.run(run, a, 'svc', repos['svc'], cfg, scope='ticket')
state.submit_review(run, a)
state.record_review(run, a, 'spec', 'approve')
state.record_review(run, a, 'standards', 'approve')
step(f'a approved: status={orbit("show", a)["status"]}')
state.set_ticket(run, a, status='merged')

ready = frontier.ready(state.load(run))
step(f'frontier ready after a merged: {ready}')
assert b in ready

state.set_ticket(run, b, status='running')
verify.run(run, b, 'api', repos['api'], cfg, scope='ticket')
state.submit_review(run, b)
state.record_review(run, b, 'spec', 'approve'); state.record_review(run, b, 'standards', 'approve')
state.set_ticket(run, b, status='merged')

state.human_done(run, h, 'checked by hand')
step(f'human ticket: status={orbit("show", h)["status"]}')
step(f'slice status={orbit("show", sl)["status"]}; spec status={orbit("show", spec)["status"]}')

for n, p in repos.items():
    verify.run(run, None, n, p, cfg)
state.submit_review(run, spec, scope='integration')
state.record_review(run, spec, 'integration', 'approve')
step(f'spec final: {orbit("show", spec)["status"]}')

after_user = orbit('show', user)
drop = lambda d: {k: v for k, v in d.items() if k != 'dependents'}
assert drop(after_user) == drop(before_user), 'user Issue was modified'
step('user Issue untouched')
print(status.render(state.load(run)))
print(subprocess.run(['orbit', '--dir', str(anchor), 'tree', spec], cwd=anchor, capture_output=True, text=True).stdout)
print('DONE')
