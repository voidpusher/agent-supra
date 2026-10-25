"""Real watch CLI + owned processes + HTTP API, with synthetic JUnit reports."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from scripts.self_test_agent import local_service

WORKER = '''from pathlib import Path
import os, sys, time, subprocess
assert 'AGENT_SUPRA_API_KEY' not in os.environ
folder = Path('.local/results')
folder.mkdir(parents=True)
kind = sys.argv[1]
for i in range(2 if kind == 'healthy' else 5 if kind == 'observe' else 15):
    fail = not (kind == 'healthy' and i == 1)
    if not fail:
        Path('calc.py').write_text('def add(a, b):\\n    return a + b\\n')
    Path('.local/executions').write_text(str(i + 1))
    result = subprocess.run([sys.executable, '-m', 'pytest', 'test_calc.py', '-q', '--junitxml=' + str(folder / (str(i) + '.xml'))], capture_output=True)
    if result.returncode not in (0, 1):
        raise SystemExit(2)
    time.sleep(.4)
raise SystemExit(0 if kind == 'healthy' else 1)
'''


def main():
    output = (Path('.local/watch-smoke') / datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    output.mkdir(parents=True)
    results = []
    with local_service(output) as (backend, sup, workspace, alerts):
        for kind in ('healthy', 'stuck', 'observe'):
            repo = output / kind
            repo.mkdir()
            (repo / 'worker.py').write_text(WORKER)
            (repo / 'calc.py').write_text('def add(a, b):\n    return a - b\n')
            (repo / 'test_calc.py').write_text('from calc import add\ndef test_add():\n    assert add(2, 3) == 5\n')
            env = os.environ.copy()
            env.update(AGENT_SUPRA_API_KEY=backend.api_key, AGENT_SUPRA_URL=backend.base_url, PYTHONDONTWRITEBYTECODE='1')
            cmd = [sys.executable, '-m', 'agent_supra.cli', 'watch', '--cwd', str(repo),
                   '--mode', 'observe' if kind == 'observe' else 'enforce', '--interval', '.1', '--timeout', '20',
                   '--state-dir', str(output / 'private-state' / kind), '--junit', '.local/results/*.xml',
                   '--', sys.executable, 'worker.py', kind]
            proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=35)
            [path] = (output / 'private-state' / kind).glob('*/journal.sqlite3')
            with sqlite3.connect(path) as conn:
                row = conn.execute('SELECT value FROM meta WHERE key="run"').fetchone()
                run = json.loads(row[0]) if row else None
                pending = conn.execute('SELECT count(*) FROM events WHERE sent=0').fetchone()[0]
            timeline = backend.timeline(run) if run else None
            state = timeline['run']['state'] if timeline else 'unattached'
            count = int((repo / '.local/executions').read_text()) if (repo / '.local/executions').exists() else 0
            tests = [e for e in timeline['events'] if e['type'] == 'tests.reported'] if timeline else []
            passed = (proc.returncode == 0 and state == 'completed' and len(tests) == 2 and not tests[-1]['data']['failing']) if kind == 'healthy' else (
                proc.returncode == 75 and state == 'paused' and 4 <= count < 15 and len(tests) >= 4) if kind == 'stuck' else (
                proc.returncode == 1 and state == 'failed' and count == 5 and bool(timeline['interventions']))
            result = dict(scenario=kind, passed=bool(passed and pending == 0), exit_code=proc.returncode,
                          state=state, worker_attempts=count, test_reports=len(tests), pending_events=pending)
            results.append(result)
            print(json.dumps(result), flush=True)
            if not result['passed']:
                break
    report = dict(kind='Native watch CLI + real pytest + owned processes + local HTTP API', production_used=False,
                  temporary_key_revoked=True, platform=sys.platform, results=results)
    (output / 'report.json').write_text(json.dumps(report, indent=2))
    print('Sanitized report:', output / 'report.json')
    return 0 if len(results) == 3 and all(r['passed'] for r in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
