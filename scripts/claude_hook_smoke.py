"""Exercise the actual hook CLI, loopback HTTP API and pytest, without an LLM.

Run from the checkout with its dev extras installed. Everything is internal;
no production credentials, provider requests, or external alerts are involved.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scripts.self_test_agent import local_service


def command(args, cwd):
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError('fixture setup failed')


def scenario(backend, output, name, *, repair=False, observe=False):
    repo = output / name
    repo.mkdir()
    (repo / '.local').mkdir()
    (repo / '.gitignore').write_text('.local/\n__pycache__/\n.pytest_cache/\n')
    test = repo / 'test_calc.py'
    test.write_text('def test_addition():\n    assert 2 + 2 == 5\n')
    command(['git', 'init', '-q'], repo)
    command(['git', 'add', '.'], repo)
    command(['git', '-c', 'user.name=Supra internal test', '-c', 'user.email=test@example.invalid',
             'commit', '-qm', 'Fixture'], repo)
    env = os.environ.copy()
    env.update(AGENT_SUPRA_API_KEY=backend.api_key, AGENT_SUPRA_URL=backend.base_url,
               AGENT_SUPRA_CLAUDE_STATE_DIR=str(output / 'private-state'),
               AGENT_SUPRA_CLAUDE_MODE='observe' if observe else 'pause')
    cli = [sys.executable, '-m', 'agent_supra.cli', 'claude-hook']
    host = Path(__file__).resolve().parents[1]
    timings = []

    def hook(event, call=None, **extra):
        payload = dict(session_id='smoke-' + name, hook_event_name=event, cwd=str(repo))
        if call:
            payload.update(tool_name='Bash', tool_use_id=call,
                tool_input={'command': 'python -m pytest test_calc.py -q --junitxml=.local/results.xml'})
        payload.update(extra)
        import time
        start = time.monotonic()
        result = subprocess.run(cli, input=json.dumps(payload), cwd=host, env=env,
                                capture_output=True, text=True, timeout=30)
        timings.append(round(time.monotonic() - start, 3))
        if result.returncode:
            raise RuntimeError('hook command failed; no private payload logged')
        return json.loads(result.stdout)

    hook('SessionStart')
    attempts, denied = 0, False
    for index in range(5):
        call = f'test-{index}'
        result = hook('PreToolUse', call)
        if result.get('hookSpecificOutput', {}).get('permissionDecision') == 'deny':
            denied = True
            break  # The actual tool is deliberately never started.
        proc = subprocess.run([sys.executable, '-m', 'pytest', 'test_calc.py', '-q', '--junitxml=.local/results.xml'],
                              cwd=repo, capture_output=True, text=True, timeout=30)
        if proc.returncode not in (0, 1):
            raise RuntimeError('fixture test runner failed')
        attempts += 1
        hook('PostToolUse' if proc.returncode == 0 else 'PostToolUseFailure', call,
             **({'tool_response': {'stdout': proc.stdout}} if proc.returncode == 0
                else {'error': 'Exit code 1\n' + proc.stdout}))
        if proc.returncode == 0:
            break
        if repair:
            edit = dict(tool_name='Write', tool_use_id='repair', tool_input={'file_path': str(test), 'content': 'fixture repair'})
            assert 'hookSpecificOutput' not in hook('PreToolUse', **edit)
            test.write_text('def test_addition():\n    assert 2 + 2 == 4\n')
            hook('PostToolUse', tool_response={}, **edit)
    hook('SessionEnd')
    import sqlite3
    records = []
    for file in (output / 'private-state').glob('*.sqlite3'):
        with sqlite3.connect(file) as conn:
            records += conn.execute('SELECT run FROM streams WHERE name="root"').fetchall()
    timelines = [backend.timeline(row[0]) for row in records]
    # Match the correct internal task by the newly created run, not by timestamps.
    candidates = [t for t in timelines if t['run']['id'] not in scenario.seen]
    assert len(candidates) == 1
    timeline = candidates[0]
    scenario.seen.add(timeline['run']['id'])
    state = timeline['run']['state']
    rules = [i['rule'] for i in timeline['interventions']]
    tests = [e for e in timeline['events'] if e['type'] == 'tests.reported']
    expected = (state == 'completed' and attempts == 2 and not denied and len(tests[-1]['data']['failing']) == 0) if repair else (
        state == 'failed' and attempts == 5 and not denied and bool(rules)) if observe else (
        state == 'paused' and attempts == 4 and denied and bool(rules))
    assert expected, f'{name} did not match the expected supervision result'
    return {'scenario': name, 'state': state, 'pytest_executions': attempts, 'test_reports': len(tests),
            'blocked_before_tool': denied, 'rules': rules, 'passed': expected,
            'max_hook_seconds': max(timings), 'run_id': timeline['run']['id']}


scenario.seen = set()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    output = (args.output or Path('.local/claude-code-smoke') / datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    output.mkdir(parents=True, exist_ok=False)
    scenario.seen.clear()
    with local_service(output) as (backend, sup, workspace, alerts):
        results = [scenario(backend, output, 'healthy', repair=True),
                   scenario(backend, output, 'stuck'),
                   scenario(backend, output, 'observe', observe=True)]
    report = {'kind': 'Internal Claude hook protocol smoke test; no Claude conversation or model calls',
              'production_used': False, 'test_key_revoked': True, 'results': results}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    for result in results:
        print(f"{result['scenario']}: {result['state']}, {result['pytest_executions']} real pytest executions, passed={result['passed']}")
    print('Sanitized report:', output / 'report.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
