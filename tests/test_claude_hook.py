import json
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agent_supra.claude_hook import HookBridge, HookClient, NoRedirect, junit_path, read_junit, validate_url, git_revision, prepare_pytest
from agent_supra.client import SupervisorAPIError, SupervisorUnavailable


@pytest.fixture
def hooks(account, tmp_path, monkeypatch):
    server, key = account
    requests = []

    def transport(method, path, body, params):
        requests.append((method, path, body))
        response = server.request(method, path, json=body, params=params, headers={'Authorization': f'Bearer {key}'})
        return response.status_code, response.json()

    def bridge(mode='pause'):
        return HookBridge(HookClient(api_key=key, transport=transport, retries=0), tmp_path / 'private', 'scope',
                          mode=mode, fail_mode='closed' if mode == 'pause' else 'open')

    monkeypatch.setattr('agent_supra.claude_hook.git_revision', lambda cwd: 'revision')
    return bridge, requests, server, tmp_path


def payload(cwd, event, call='tool-1', **kwargs):
    return dict(session_id='session', hook_event_name=event, cwd=str(cwd), tool_name='Bash',
                tool_use_id=call, tool_input={'command': 'python -m pytest --junitxml=results.xml'}, **kwargs)


def report(folder, failures=1, skipped=False):
    (folder / 'results.xml').write_text('<testsuites><testsuite><testcase classname="private.module" name="test_secret">'
        + ('<skipped/>' if skipped else '<failure message="private code"/>' if failures else '')
        + '</testcase><testcase classname="private.module" name="test_other"/></testsuite></testsuites>')


def runs(requests):
    return [path.split('/')[3] for method, path, body in requests if method == 'POST' and path.endswith('/events')]


def test_real_api_stuck_hook_denies_next_tool_and_preserves_pause_on_end(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'SessionStart'))
    for i in range(4):
        assert 'hookSpecificOutput' not in bridge().handle(payload(folder, 'PreToolUse', str(i)))
        report(folder)
        bridge().handle(payload(folder, 'PostToolUseFailure', str(i), error='Exit code 1\n1 failed, 1 passed'))
    denied = bridge().handle(payload(folder, 'PreToolUse', 'blocked'))
    assert denied['hookSpecificOutput']['permissionDecision'] == 'deny'
    bridge().handle(payload(folder, 'SessionEnd'))
    backend = bridge().client
    run = runs(requests)[0]
    assert backend.decide(run)['state'] == 'paused'
    timeline = backend.timeline(run)
    assert timeline['interventions'][0]['rule'] == 'exact_repeat'
    serialized = json.dumps(requests)
    for private in ('private.module', 'private code', 'test_secret', '1 failed', 'python -m pytest'):
        assert private not in serialized
    assert len([e for e in timeline['events'] if e['type'] == 'tests.reported']) == 4


def test_healthy_run_and_stale_report_never_becomes_new_evidence(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'PreToolUse', 'first'))
    report(folder)
    bridge().handle(payload(folder, 'PostToolUseFailure', 'first', error='Exit code 1'))
    bridge().handle(payload(folder, 'PreToolUse', 'stale'))
    bridge().handle(payload(folder, 'PostToolUseFailure', 'stale', error='pytest never started'))
    bridge().handle(payload(folder, 'PreToolUse', 'fixed'))
    report(folder, failures=0)
    bridge().handle(payload(folder, 'PostToolUse', 'fixed', tool_response={'stdout': 'private output'}))
    bridge().handle(payload(folder, 'SessionEnd'))
    timeline = bridge().client.timeline(runs(requests)[0])
    assert timeline['run']['state'] == 'completed'
    tests = [e for e in timeline['events'] if e['type'] == 'tests.reported']
    assert len(tests) == 2 and tests[0]['data']['failing'] and tests[-1]['data']['failing'] == []
    assert tests[0]['data']['suite'] == tests[-1]['data']['suite']


def test_observation_flags_without_denying_and_tool_success_does_not_grant_permission(hooks):
    bridge, requests, server, folder = hooks
    for i in range(5):
        result = bridge('observe').handle(payload(folder, 'PreToolUse', str(i)))
        assert 'hookSpecificOutput' not in result
        report(folder)
        bridge('observe').handle(payload(folder, 'PostToolUseFailure', str(i), error='Exit code 1'))
    timeline = bridge('observe').client.timeline(runs(requests)[0])
    assert timeline['run']['state'] == 'running'
    assert timeline['interventions'][0]['action'] == 'flag'


def test_duplicate_delivery_is_idempotent_and_parallel_processes_preserve_sequences(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'SessionStart'))
    post = payload(folder, 'PostToolUse', 'write', tool_response={})
    post['tool_name'], post['tool_input'] = 'Write', {'content': 'super secret code', 'file_path': 'private.py'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: bridge().handle(post), range(8)))
    timeline = bridge().client.timeline(runs(requests)[0])
    assert [e['seq'] for e in timeline['events']] == [1, 2]
    state = next((folder / 'private').glob('*.sqlite3'))
    assert b'super secret code' not in state.read_bytes()


def test_subagent_parent_pause_and_lifecycle(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'SessionStart'))
    bridge().handle(payload(folder, 'SubagentStart', agent_id='child-private'))
    child = runs(requests)[-1]
    parent = runs(requests)[0]
    assert bridge().client.timeline(child)['run']['parent_run_id'] == parent
    bridge().client.pause(parent, 'review')
    result = bridge().handle(payload(folder, 'PreToolUse', agent_id='child-private'))
    assert result['hookSpecificOutput']['permissionDecision'] == 'deny'


def test_outage_queue_survives_fresh_bridge_and_replays_once(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'SessionStart'))
    broken = bridge()
    broken.client.ingest = lambda *a: (_ for _ in ()).throw(SupervisorUnavailable('private error'))
    broken.handle(payload(folder, 'PostToolUse', 'offline', tool_response={}))
    state = next((folder / 'private').glob('*.sqlite3'))
    with sqlite3.connect(state) as conn:
        assert conn.execute('SELECT count(*) FROM outbox WHERE sent=0').fetchone()[0] == 1
    bridge().handle(payload(folder, 'PreToolUse', 'recovery'))
    timeline = bridge().client.timeline(runs(requests)[0])
    assert len(timeline['events']) == 2
    with sqlite3.connect(state) as conn:
        assert conn.execute('SELECT count(*) FROM outbox WHERE sent=0').fetchone()[0] == 0


@pytest.mark.parametrize('failure,closed,deny', [('outage', True, True), ('outage', False, False), ('auth', False, True)])
def test_preflight_failure_policy(hooks, failure, closed, deny):
    bridge, _, _, folder = hooks
    hook = bridge()
    hook.fail_mode = 'closed' if closed else 'open'
    hook.handle(payload(folder, 'SessionStart'))
    exc = SupervisorAPIError(401, 'private credential error') if failure == 'auth' else SupervisorUnavailable('outage')
    hook.client.decide = lambda *a: (_ for _ in ()).throw(exc)
    result = hook.handle(payload(folder, 'PreToolUse'))
    assert ('hookSpecificOutput' in result) == deny
    assert 'private credential' not in json.dumps(result)


def test_resume_creates_new_run_without_reassigning_old_outbox(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'SessionStart'))
    first = runs(requests)[0]
    bridge().handle(payload(folder, 'SessionEnd'))
    bridge().handle(payload(folder, 'SessionStart', source='resume'))
    second = runs(requests)[-1]
    assert first != second
    assert bridge().client.timeline(first)['run']['state'] == 'completed'
    assert bridge().client.decide(second)['allowed']
    assert [e['seq'] for e in bridge().client.timeline(second)['events']] == [1]


@pytest.mark.parametrize('command,valid', [
    ('python -m pytest --junitxml=results.xml', True), ('pytest --junit-xml results.xml', True),
    ('echo pytest --junitxml=results.xml', False), ('pytest', False),
    ('pytest --junitxml=../outside.xml', False), ('cd foo && pytest --junitxml=results.xml', False),
])
def test_junit_command_scope(tmp_path, command, valid):
    assert bool(junit_path(command, str(tmp_path))) == valid


def test_session_guidance_reaches_model_context_without_granting_permissions(hooks):
    bridge, _, _, folder = hooks
    result = bridge().handle(payload(folder, 'SessionStart'))
    context = result['hookSpecificOutput']
    assert context['hookEventName'] == 'SessionStart'
    assert 'python -m pytest --junitxml=.local/supra-tests.xml' in context['additionalContext']
    assert 'Jest and Go test progress is not supported' in context['additionalContext']
    assert 'permissionDecision' not in context


@pytest.mark.parametrize('command', ['pytest', 'pytest -q tests/', 'python -m pytest -k "my test"', 'py.test -x'])
def test_direct_pytest_gets_report_without_changing_original_arguments(tmp_path, command):
    path, updated = prepare_pytest(command, str(tmp_path), 'call-one')
    assert updated.startswith(command + ' --junitxml=')
    assert path == junit_path(updated, str(tmp_path))
    assert path != prepare_pytest(command, str(tmp_path), 'call-two')[0]


@pytest.mark.parametrize('command', ['pytest;echo unsafe', 'pytest&&echo unsafe', 'pytest | cat',
    'cd foo && pytest', 'pytest > out.txt', 'pytest\necho unsafe', 'pytest $(echo x)',
    'pytest `echo x`', 'pytest # comment', 'pytest -- tests', 'pytest --help',
    'pytest --junitxml=../outside.xml', 'pytest --junitxml=', 'jest', 'go test ./...'])
def test_shell_programs_and_explicit_report_destinations_are_never_rewritten(tmp_path, command):
    assert prepare_pytest(command, str(tmp_path), 'call')[1] is None


@pytest.mark.parametrize('post_uses_original', [False, True])
def test_bare_pytest_reports_real_progress_and_cannot_bypass_pause(hooks, post_uses_original):
    bridge, requests, _, folder = hooks
    for i in range(4):
        pre = payload(folder, 'PreToolUse', str(i))
        pre['tool_input'] = {'command': 'pytest -q', 'timeout': 30000}
        result = bridge().handle(pre)['hookSpecificOutput']
        assert 'permissionDecision' not in result
        assert result['updatedInput']['timeout'] == 30000
        path = junit_path(result['updatedInput']['command'], str(folder))
        path.parent.mkdir(exist_ok=True)
        report(folder)
        path.write_bytes((folder/'results.xml').read_bytes())
        post = dict(pre, hook_event_name='PostToolUseFailure', error='Exit code 1')
        if not post_uses_original:
            post['tool_input'] = result['updatedInput']
        bridge().handle(post)
    denied = bridge().handle(dict(pre, tool_use_id='blocked'))['hookSpecificOutput']
    assert denied['permissionDecision'] == 'deny' and 'updatedInput' not in denied
    timeline = bridge().client.timeline(runs(requests)[0])
    assert len([e for e in timeline['events'] if e['type']=='tests.reported']) == 4
    serialized = json.dumps(requests)
    assert 'supra-tests-' not in serialized and 'pytest -q' not in serialized


def test_auto_report_requires_fresh_evidence_not_a_successful_shell_exit(hooks):
    bridge, requests, _, folder = hooks
    pre = payload(folder, 'PreToolUse')
    pre['tool_input'] = {'command': 'pytest'}
    result = bridge().handle(pre)['hookSpecificOutput']['updatedInput']
    bridge().handle(dict(pre, hook_event_name='PostToolUse', tool_input=result, tool_response={'stdout':'all passed'}))
    timeline = bridge().client.timeline(runs(requests)[0])
    assert not [e for e in timeline['events'] if e['type']=='tests.reported']


def test_junit_skipped_and_malformed_reports_are_not_success(tmp_path):
    report(tmp_path, skipped=True)
    data = read_junit(tmp_path / 'results.xml', b'private-salt')
    assert len(data['suite']) == 1
    for raw in ('broken', '<testsuite/>', '<!DOCTYPE x><testsuite/>', '<testsuite><testcase/></testsuite>'):
        (tmp_path / 'results.xml').write_text(raw)
        assert read_junit(tmp_path / 'results.xml', b'key') is None


@pytest.mark.parametrize('url', ['http://evil.example', 'https://user:password@example.com', 'https://example.com?key=secret', 'file:///etc/passwd'])
def test_endpoint_restrictions(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_redirects_cannot_forward_credentials():
    assert NoRedirect().redirect_request(None, None, 302, 'redirect', {}, 'https://other.example') is None


def test_unknown_revision_is_not_claimed_as_no_code_change(supervisor):
    task = supervisor.create_task('unknown code', policy={'observation_mode': False})
    run = supervisor.create_run(task)
    for i in range(4):
        supervisor.ingest(run, {'event_id': str(i), 'seq': i + 1, 'type': 'tests.reported',
            'data': {'suite': ['a'], 'failing': ['a'], 'rev': None}})
    finding = supervisor.interventions(run)[0]
    assert finding['rule'] == 'no_progress'
    assert 'revisions were unavailable' in finding['message']


def test_hook_cli_help_does_not_read_stdin():
    result = subprocess.run([sys.executable, '-m', 'agent_supra.claude_hook', '--help'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and 'stdin' in result.stdout


def test_skipping_failed_test_does_not_finish_as_success(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'PreToolUse', 'failing'))
    report(folder)
    bridge().handle(payload(folder, 'PostToolUseFailure', 'failing', error='Exit code 1'))
    bridge().handle(payload(folder, 'PreToolUse', 'skipped'))
    report(folder, skipped=True)
    bridge().handle(payload(folder, 'PostToolUse', 'skipped', tool_response={}))
    bridge().handle(payload(folder, 'SessionEnd'))
    assert bridge().client.timeline(runs(requests)[0])['run']['state'] == 'failed'


def test_git_revision_tracks_dirty_and_untracked_code(tmp_path):
    def git(*args):
        subprocess.run(['git', '-C', str(tmp_path), *args], check=True, capture_output=True)
    git('init', '-q')
    code = tmp_path / 'code.py'
    code.write_text('answer = 1\n')
    git('add', '.')
    git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
    first = git_revision(str(tmp_path))
    code.write_text('answer = 2\n')
    second = git_revision(str(tmp_path))
    (tmp_path / 'new.py').write_text('answer = 3\n')
    third = git_revision(str(tmp_path))
    assert first and len({first, second, third}) == 3


def test_cli_failure_never_prints_private_input_or_key(tmp_path):
    import os
    env = os.environ.copy()
    env.update(AGENT_SUPRA_API_KEY='private-key-do-not-log', AGENT_SUPRA_URL='http://forbidden.example',
               AGENT_SUPRA_CLAUDE_MODE='pause', AGENT_SUPRA_CLAUDE_STATE_DIR=str(tmp_path))
    result = subprocess.run([sys.executable, '-m', 'agent_supra.cli', 'claude-hook'], env=env,
        input=json.dumps(dict(session_id='session', hook_event_name='PreToolUse', private='private-code-do-not-log')),
        capture_output=True, text=True, timeout=10)
    assert json.loads(result.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'
    assert 'private-key-do-not-log' not in result.stdout + result.stderr
    assert 'private-code-do-not-log' not in result.stdout + result.stderr


def test_parallel_initial_attachment_creates_one_task_and_run(hooks):
    bridge, requests, server, folder = hooks
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: bridge().handle(payload(folder, 'SessionStart')), range(8)))
    assert len([r for r in requests if r[0] == 'POST' and r[1] == '/v1/tasks']) == 1
    assert len([r for r in requests if r[0] == 'POST' and r[1].endswith('/runs')]) == 1


def test_testcase_timeout_text_does_not_hide_actual_junit_results(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'PreToolUse'))
    report(folder)
    bridge().handle(payload(folder, 'PostToolUseFailure', error='Exit code 1\nAssertionError: timeout'))
    timeline = bridge().client.timeline(runs(requests)[0])
    assert len([e for e in timeline['events'] if e['type'] == 'tests.reported']) == 1


def test_late_finish_cannot_override_pause_and_reviewer_can_resume(hooks):
    bridge, requests, server, folder = hooks
    bridge().handle(payload(folder, 'SessionStart'))
    run = runs(requests)[0]
    bridge().client.pause(run, 'review this run')
    stale = bridge()
    stale.client.decide = lambda _: {'allowed': True, 'state': 'running'}
    stale.handle(payload(folder, 'SessionEnd'))
    assert bridge().client.decide(run)['state'] == 'paused'
    bridge().client.continue_run(run, 'Reviewed and approved continuation')
    assert 'hookSpecificOutput' not in bridge().handle(payload(folder, 'PreToolUse'))
    bridge().handle(payload(folder, 'SessionEnd'))
    assert bridge().client.decide(run)['state'] == 'completed'
