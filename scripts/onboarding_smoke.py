"""Execute the rendered first-run guide with an installed SDK and isolated HTTP API.

python -m scripts.onboarding_smoke --python /path/to/clean/venv/python
No production keys, messages, model calls, or outside adoption are involved.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


def first_run_source(page: str) -> str:
    class Example(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.inside = False
            self.parts = []
        def handle_starttag(self, tag, attrs):
            if tag == 'pre' and dict(attrs).get('id') == 'first-run-example':
                self.inside = True
        def handle_endtag(self, tag):
            if tag == 'pre':
                self.inside = False
        def handle_data(self, data):
            if self.inside:
                self.parts.append(data)
    parser = Example()
    parser.feed(page)
    source = ''.join(parser.parts)
    if not source.strip():
        raise ValueError('First-run example missing from rendered quickstart')
    compile(source, 'first_run.py', 'exec')
    return source


def main(argv=None):
    from scripts.self_test_agent import local_service

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    args = parser.parse_args(argv)
    python = args.python.resolve()
    folder = (Path('.local/onboarding-smoke') / datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    folder.mkdir(parents=True)
    version = subprocess.run([str(python), '-I', '-c',
                              'import agent_supra; print(agent_supra.__version__)'],
                             capture_output=True, text=True, check=True, timeout=30).stdout.strip()
    with local_service(folder) as (backend, _supervisor, _workspace, _alerts):
        with urllib.request.urlopen(backend.base_url + '/docs/quickstart') as response:
            source = first_run_source(response.read().decode())
        script = folder / 'first_run.py'
        script.write_text(source, encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if not k.startswith('AGENT_SUPRA_')
               and k not in {'OPENAI_API_KEY', 'GEMINI_API_KEY', 'ANTHROPIC_API_KEY'}}
        env.update(AGENT_SUPRA_API_KEY=backend.api_key, AGENT_SUPRA_URL=backend.base_url)
        def execute(environment):
            return subprocess.run([str(python), '-I', str(script)], cwd=folder,
                                  env=environment, capture_output=True, text=True, timeout=45)
        good = execute(env)
        assert good.returncode == 0, 'Published SDK connection check failed'
        assert 'Verified completed run: run_' in good.stdout
        run_id = good.stdout.strip().split()[-1]
        assert backend.decide(run_id)['state'] == 'completed'
        timeline = backend.timeline(run_id)
        assert any(e['type'] == 'tests.reported' for e in timeline['events'])
        wrong = execute(dict(env, AGENT_SUPRA_API_KEY='invalid-internal-test-key'))
        assert wrong.returncode != 0 and 'Verified completed run:' not in wrong.stdout
        with socket.socket() as unavailable:
            unavailable.bind(('127.0.0.1', 0))  # reserved, deliberately not listening
            offline = execute(dict(env, AGENT_SUPRA_URL=f'http://127.0.0.1:{unavailable.getsockname()[1]}'))
        assert offline.returncode != 0 and 'Verified completed run:' not in offline.stdout
        for result in (good, wrong, offline):
            assert backend.api_key not in result.stdout + result.stderr
        report = {'kind': 'Internal onboarding check; not outside adoption',
                  'installed_sdk_python': str(python), 'installed_sdk_version': version,
                  'rendered_guide_executes': True,
                  'server_confirmed_completed_run': run_id, 'test_event_received': True,
                  'invalid_key_cannot_claim_success': True, 'offline_cannot_claim_success': True,
                  'model_calls': 0, 'production_data_used': False,
                  'temporary_key_revoked_on_exit': True}
    (folder / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    print('Evidence:', folder / 'report.json')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
