"""Authenticated Claude Code integration test against isolated local Supra.

Uses the installed CLI's existing login. Only synthetic fixture files go to the
model; no Supra production credentials or data are loaded. Native permission checks
remain enabled, with two narrowly allowed fixture commands.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scripts.claude_hook_smoke import command
from scripts.self_test_agent import local_service

PYTEST_COMMAND = 'python -m pytest test_calc.py -q --junitxml=.local/results.xml'
TEST = '''from pathlib import Path
from calc import add

def test_addition():
    marker = Path('.local/executions.txt')
    with marker.open('a') as handle:
        handle.write('attempt\\n')
    assert add(2, 3) == 5

def test_zero():
    assert add(0, 0) == 0
'''


def parse_host_events(raw: str, pytest_command: str = PYTEST_COMMAND) -> dict:
    requested, denied, cost, errors, finished = set(), False, None, True, False
    for line in raw.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'result':
            finished = True
            cost = event.get('total_cost_usd')
            errors = bool(event.get('is_error'))
        message = event.get('message') or {}
        message = message if isinstance(message, dict) else {}
        for block in message.get('content', []) if isinstance(message.get('content'), list) else []:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_use' and block.get('name') == 'Bash' and block.get('input', {}).get('command') == pytest_command:
                requested.add(block['id'])
            if block.get('type') == 'tool_result' and block.get('is_error'):
                content = json.dumps(block.get('content', ''))
                denied |= 'Agent Supra' in content and ('paused' in content or 'deny' in content)
        if event.get('type') == 'system' and event.get('subtype') == 'hook_response':
            content = json.dumps(event)
            denied |= 'Agent Supra' in content and 'permissionDecision' in content and 'deny' in content
    return {'requested_pytest_tools': len(requested), 'host_supra_denial': bool(denied),
            'cli_reported_cost_usd': cost, 'host_result_error': errors, 'host_result_seen': finished}


def scenario(claude: str, backend, output: Path, name: str, *, bare_pytest=False) -> dict:
    host = Path(__file__).resolve().parents[1]
    repo = output / name
    repo.mkdir()
    (repo / '.local').mkdir()
    (repo / '.gitignore').write_text('.local/\n__pycache__/\n.pytest_cache/\n')
    (repo / 'calc.py').write_text('def add(a, b):\n    return a - b\n')
    (repo / 'test_calc.py').write_text(TEST)
    (repo / 'repair.py').write_text("from pathlib import Path\nPath('calc.py').write_text('def add(a, b):\\n    return a + b\\n')\n")
    command(['git', 'init', '-q'], repo)
    command(['git', 'add', '.'], repo)
    command(['git', '-c', 'user.name=Supra internal test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Synthetic fixture'], repo)
    state = repo / '.local' / 'hook-state'
    env = os.environ.copy()
    env.update(AGENT_SUPRA_API_KEY=backend.api_key, AGENT_SUPRA_URL=backend.base_url,
               AGENT_SUPRA_CLAUDE_STATE_DIR=str(state), AGENT_SUPRA_CLAUDE_MODE='observe' if name == 'observe' else 'pause',
               AGENT_SUPRA_CLAUDE_FAIL_MODE='closed', CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC='1',
               PYTHONDONTWRITEBYTECODE='1')
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    bash = Path('C:/Program Files/Git/bin/bash.exe')
    if os.name == 'nt' and bash.exists():
        env['CLAUDE_CODE_GIT_BASH_PATH'] = str(bash)
    prefix = ('You are executing a bounded integration test in a disposable synthetic repository. '
              'Use ONLY the two approved commands below. Do not inspect credentials, environment variables, other files, or network services. '
              'Make each command a separate Bash tool call, with exactly the given command text; do not chain commands. ')
    pytest_command = 'pytest test_calc.py -q' if bare_pytest else PYTEST_COMMAND
    if name == 'healthy':
        prompt = prefix + f'First run `{pytest_command}` once. It should fail. Then run `python repair.py` once, then `{pytest_command}` once. Stop after the final test result. Do not change the tests or invent another repair.'
    else:
        prompt = prefix + f'Run `{pytest_command}` exactly five times, one Bash call at a time. The unchanged failure is deliberate: do not repair it or alter the command. If Agent Supra denies a tool, immediately stop and report the denial instead of retrying or changing tools. This is a test of supervision, so repeated failures are expected.'
    args = [claude, '-p', '--model', 'sonnet', '--max-budget-usd', '0.50', '--max-turns', '8',
            '--no-session-persistence', '--no-chrome', '--setting-sources', '',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--plugin-dir', str(host / 'plugins' / 'claude-code'),
            '--output-format', 'stream-json', '--verbose', '--include-hook-events',
            '--permission-mode', 'dontAsk', '--tools', 'Bash',
            '--allowedTools', 'Bash(pytest test_calc.py *)' if bare_pytest else 'Bash(python -m pytest test_calc.py *)', 'Bash(python repair.py)', '--', prompt]
    try:
        proc = subprocess.run(args, cwd=repo, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=150)
    except subprocess.TimeoutExpired:
        return {'scenario': name, 'passed': False, 'error': 'Claude host exceeded the 150-second test timeout'}
    evidence = parse_host_events(proc.stdout, pytest_command)
    # Only sanitized evidence is persisted; raw model/host output is not a report artifact.
    rows = []
    for path in state.glob('*.sqlite3'):
        with sqlite3.connect(path) as conn:
            rows += conn.execute('SELECT run FROM streams WHERE name="root"').fetchall()
    if len(rows) != 1:
        return dict(evidence, scenario=name, passed=False, exit_code=proc.returncode,
                    error='Expected one hook-attached run', attached_runs=len(rows))
    timeline = backend.timeline(rows[0][0])
    tests = [event for event in timeline['events'] if event['type'] == 'tests.reported']
    marker = repo / '.local' / 'executions.txt'
    executions = len(marker.read_text().splitlines()) if marker.exists() else 0
    rules = [item['rule'] for item in timeline['interventions']]
    state_name = timeline['run']['state']
    expected = (
        state_name == 'completed' and executions == 2 and len(tests) == 2 and not tests[-1]['data']['failing']
        if name == 'healthy' else
        state_name == 'paused' and executions == 4 and len(tests) == 4 and evidence['host_supra_denial'] and evidence['requested_pytest_tools'] >= 5
        if name == 'stuck' else
        state_name == 'failed' and executions == 5 and len(tests) == 5 and bool(rules) and not evidence['host_supra_denial'])
    return dict(evidence, scenario=name, passed=bool(expected and proc.returncode == 0 and evidence['host_result_seen'] and not evidence['host_result_error']),
                bare_pytest=bare_pytest,
                exit_code=proc.returncode, run_id=rows[0][0], state=state_name,
                actual_pytest_executions=executions, test_reports=len(tests), rules=rules)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--claude', default=shutil.which('claude') or str(Path.home() / '.local/bin/claude.exe'))
    parser.add_argument('--scenario', choices=['healthy', 'stuck', 'observe', 'all'], default='all')
    parser.add_argument('--bare-pytest', action='store_true', help='verify host-applied JUnit injection for direct pytest')
    args = parser.parse_args()
    auth = subprocess.run([args.claude, 'auth', 'status'], capture_output=True, text=True, timeout=15)
    if not json.loads(auth.stdout).get('loggedIn'):
        raise SystemExit('Sign in privately with claude auth login before running this test.')
    output = (Path('.local/claude-code-host') / datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    output.mkdir(parents=True, exist_ok=False)
    selected = ['healthy', 'stuck', 'observe'] if args.scenario == 'all' else [args.scenario]
    with local_service(output) as (backend, sup, workspace, alerts):
        results = []
        for name in selected:
            result = scenario(args.claude, backend, output, name, bare_pytest=args.bare_pytest)
            results.append(result)
            print(json.dumps(result), flush=True)
            (output / 'report.json').write_text(json.dumps({'kind': 'Authenticated native Claude Code host test',
                'production_used': False, 'results': results, 'test_key_revoked': False}, indent=2))
            if not result['passed']:
                break
    report = {'kind': 'Authenticated native Claude Code host test', 'production_used': False,
              'test_key_revoked': True, 'results': results}
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print('Sanitized report:', output / 'report.json')
    return 0 if len(results) == len(selected) and all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
