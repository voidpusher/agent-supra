"""Claude Code hook bridge. Standard library only; never reads transcripts.

One private SQLite outbox per session/account/endpoint serializes parallel hook
processes. Only sanitized events are durable. API keys remain in the environment.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import logging
import os
import re
import secrets
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .client import Client, SupervisorAPIError, SupervisorUnavailable
from .sdk import error_fingerprint, fingerprint

EVENTS = {'SessionStart', 'SessionEnd', 'PreToolUse', 'PostToolUse', 'PostToolUseFailure',
          'SubagentStart', 'SubagentStop'}
TOOLS = {'Edit', 'Write', 'Bash'}
MAX_INPUT = 2_000_000
MAX_PENDING = 500
TEST_GUIDANCE = (
    'Agent Supra tracks test progress from fresh pytest JUnit reports, not terminal text. '
    'For Python tests use a direct command: python -m pytest --junitxml=.local/supra-tests.xml '
    '(preserve the project test selection and flags). Keep .local/ ignored by Git. '
    'Direct pytest commands without a report flag receive a per-tool JUnit argument automatically. '
    'Do not chain test commands with cd, &&, pipes or shell substitutions; run them separately. '
    'Jest and Go test progress is not supported. Never claim test supervision from tool activity alone. '
    'Do not change tests or the project test framework just to satisfy monitoring. '
    'These hooks do not measure model spend.'
)


def pytest_args(command: str) -> list[str] | None:
    """Recognize a single direct command; never parse a shell program for rewriting."""
    if any(char in command for char in '\r\n`$#'):
        return None
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>()')
        lexer.whitespace_split = True
        lexer.commenters = ''
        args = list(lexer)
    except ValueError:
        return None
    if not args or any(t and all(c in ';&|<>()' for c in t) for t in args):
        return None
    executable = args[0].replace('\\', '/').rsplit('/', 1)[-1].lower().removesuffix('.exe')
    direct = executable in {'pytest', 'py.test'}
    module = (re.fullmatch(r'python(?:\d+(?:\.\d+)*)?|py', executable)
              and len(args) > 2 and args[1:3] == ['-m', 'pytest'])
    if not (direct or module):
        return None
    return args


def junit_path(command: str, cwd: str) -> Path | None:
    """Recognize direct pytest commands with an explicit JUnit report argument."""
    args = pytest_args(command)
    if args is None:
        return None
    report = None
    for index, arg in enumerate(args):
        if arg == '--':
            break
        if arg in {'--junitxml', '--junit-xml'} and index + 1 < len(args):
            report = args[index + 1]
        elif arg.startswith(('--junitxml=', '--junit-xml=')):
            report = arg.split('=', 1)[1]
    if report:
        root = Path(cwd).resolve()
        path = (root / report).resolve()
        if path.is_relative_to(root):
            return path
    return None


def prepare_pytest(command: str, cwd: str, identity: str) -> tuple[Path | None, str | None]:
    """Add only a JUnit argument; keep all original shell text and test options intact."""
    args = pytest_args(command)
    if args is None:
        return None, None
    if any(arg.startswith(('--junitxml', '--junit-xml')) for arg in args):
        return junit_path(command, cwd), None  # Never replace an invalid/user-owned destination.
    if any(arg in {'--', '--help', '-h', '--version', '-V', '--collect-only', '--co'} for arg in args):
        return None, None
    name = hashlib.sha256(identity.encode()).hexdigest()[:24]
    relative = f'.local/supra-tests-{name}.xml'
    report = (Path(cwd).resolve() / relative).resolve()
    if not report.is_relative_to(Path(cwd).resolve()):
        return None, None  # Includes a .local symlink pointing outside the project.
    return report, command.rstrip() + ' --junitxml=' + relative


def report_marker(path: Path | None) -> str:
    if path is None:
        return 'none'
    try:
        stat = path.stat()
        return f'{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}:{stat.st_ino}'
    except OSError:
        return 'missing'


def read_junit(path: Path, salt: bytes) -> dict | None:
    """Keep identities, exclude skipped tests, and discard all names/output locally."""
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT or b'<!DOCTYPE' in raw.upper():
            return None
        root = ET.fromstring(raw)
        if root.tag not in {'testsuite', 'testsuites'}:
            return None
        suite, failing = [], []
        for case in root.iter('testcase'):
            if case.find('skipped') is not None:
                continue
            if not case.get('name'):
                return None
            test_id = fingerprint((case.get('classname', ''), case.get('name')), salt)
            if test_id in suite or len(suite) >= 5000:
                return None
            suite.append(test_id)
            if case.find('failure') is not None or case.find('error') is not None:
                failing.append(test_id)
        return {'suite': suite, 'failing': failing} if suite else None
    except (OSError, ET.ParseError):
        return None


def git_revision(cwd: str) -> str | None:
    try:
        head = subprocess.run(['git', '-C', cwd, 'rev-parse', 'HEAD'], capture_output=True, timeout=2)
        diff = subprocess.run(['git', '-C', cwd, 'diff', '--no-ext-diff', '--no-textconv', 'HEAD'],
                              capture_output=True, timeout=2)
        if head.returncode or diff.returncode:
            return None
        untracked = subprocess.run(['git', '-C', cwd, 'ls-files', '--others', '--exclude-standard', '-z'], capture_output=True, timeout=2)
        if untracked.returncode:
            return None
        digest = hashlib.sha256(head.stdout + diff.stdout)
        root = Path(cwd).resolve()
        total = 0
        for name in sorted(untracked.stdout.split(b'\0')):
            if not name:
                continue
            path = (root / os.fsdecode(name)).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                return None
            with path.open('rb') as handle:
                raw = handle.read(5_000_001)
            total += len(raw)
            if total > 5_000_000:
                return None
            digest.update(name + b'\0' + hashlib.sha256(raw).digest())
        return digest.hexdigest()[:20]
    except (OSError, subprocess.TimeoutExpired):
        return None


def validate_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname
        or (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1', '::1'}))):
        raise ValueError('use HTTPS, or HTTP on loopback, without embedded credentials or query parameters')
    return url.rstrip('/')


class HookClient(Client):
    """The hook's durable outbox owns retries; never silently drop a rejected batch."""
    def create_task(self, objective, policy=None):
        return self._call('POST', '/v1/tasks', {'objective': objective, 'policy': policy})['task_id']

    def create_run(self, task_id, parent_run_id=None):
        return self._call('POST', f'/v1/tasks/{task_id}/runs', {'parent_run_id': parent_run_id})['run_id']

    def ingest(self, run_id, event):
        return self._call('POST', f'/v1/runs/{run_id}/events', {'events': [event]})

    def decide(self, run_id):
        return self._call('GET', f'/v1/runs/{run_id}/decision')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_client(key: str, url: str) -> Client:
    """Never forward the authorization header through an HTTP redirect."""
    opener = urllib.request.build_opener(NoRedirect())

    def transport(method, path, body, params):
        target = url + path + ('?' + urllib.parse.urlencode(params) if params else '')
        request = urllib.request.Request(target, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
        try:
            with opener.open(request, timeout=2) as response:
                return response.status, json.loads(response.read() or b'{}')
        except urllib.error.HTTPError as exc:
            if 300 <= exc.code < 400:
                return 400, {}  # Configuration failure, not a successful attachment.
            try:
                payload = json.loads(exc.read() or b'{}')
            except ValueError:
                payload = {}
            return exc.code, payload

    return HookClient(api_key=key, base_url=url, fail_mode='closed', retries=0, transport=transport)


def denial(state='unavailable') -> dict:
    # Never output "allow": Claude's own permission checks must remain in force.
    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'deny',
        'permissionDecisionReason': f'Agent Supra: supervision is {state}. Review the dashboard or restore supervision before retrying.'}}


class HookBridge:
    def __init__(self, client, state_dir: Path, scope: str, *, mode='observe', fail_mode='open'):
        if mode not in {'observe', 'pause'} or fail_mode not in {'open', 'closed'}:
            raise ValueError('invalid hook mode')
        self.client, self.mode, self.fail_mode = client, mode, fail_mode
        self.state_dir, self.scope = Path(state_dir), scope + '\0' + mode

    def _connect(self, session_id: str):
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        key = hashlib.sha256((self.scope + '\0' + session_id).encode()).hexdigest()
        path = self.state_dir / f'{key}.sqlite3'
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            pass
        conn = sqlite3.connect(path, timeout=8)
        conn.row_factory = sqlite3.Row
        try:
            conn.executescript('''BEGIN IMMEDIATE;
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS streams (name TEXT PRIMARY KEY, task TEXT NOT NULL,
            run TEXT NOT NULL, seq INTEGER NOT NULL DEFAULT 0, closed INTEGER NOT NULL DEFAULT 0,
            failures INTEGER, effective TEXT NOT NULL DEFAULT '[]');
          CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, run TEXT NOT NULL,
            seq INTEGER NOT NULL, body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS preflight (id TEXT PRIMARY KEY, marker TEXT NOT NULL);
            ''')
        except Exception:
            conn.close()
            raise
        conn.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('salt', secrets.token_hex(32)))
        return conn

    def _enqueue(self, conn, stream, type_, data, identity):
        row = conn.execute('SELECT * FROM streams WHERE name=?', (stream,)).fetchone()
        event_id = hashlib.sha256((row['run'] + identity).encode()).hexdigest()
        if conn.execute('SELECT 1 FROM outbox WHERE id=?', (event_id,)).fetchone():
            return
        if conn.execute('SELECT count(*) FROM outbox WHERE sent=0').fetchone()[0] >= MAX_PENDING:
            raise SupervisorUnavailable('local outbox full')
        seq = row['seq'] + 1
        event = {'event_id': event_id, 'seq': seq, 'type': type_, 'ts': time.time(), 'data': data}
        conn.execute('INSERT INTO outbox VALUES (?,?,?,?,0)', (event_id, row['run'], seq, json.dumps(event)))
        conn.execute('UPDATE streams SET seq=? WHERE name=?', (seq, stream))

    def _ensure(self, conn, name, parent=None, restart=False):
        row = conn.execute('SELECT * FROM streams WHERE name=?', (name,)).fetchone()
        if row and not (restart and row['closed']):
            return row
        if row and self.client.decide(row['run'])['state'] in {'paused', 'stopped'}:
            return row  # Restarting Claude must not silently clear an intervention.
        saved = conn.execute('SELECT value FROM meta WHERE key="task"').fetchone()
        if row:
            task = row['task']
        elif parent:
            task = parent['task']
        elif saved:
            task = saved['value']
        else:
            task = self.client.create_task('Claude Code session', policy={'observation_mode': self.mode == 'observe'})
        if task.startswith('pending_'):
            raise SupervisorUnavailable('session creation unavailable')
        conn.execute('INSERT OR IGNORE INTO meta VALUES (?,?)', ('task', task))
        run = self.client.create_run(task, parent['run'] if parent else None)
        if run.startswith('pending_'):
            raise SupervisorUnavailable('run creation unavailable')
        conn.execute('INSERT OR REPLACE INTO streams(name,task,run,seq,closed) VALUES (?,?,?,0,0)', (name, task, run))
        self._enqueue(conn, name, 'run.started', {'parent_run_id': parent['run'] if parent else None}, 'start')
        return conn.execute('SELECT * FROM streams WHERE name=?', (name,)).fetchone()

    def _drain(self, conn):
        rows = conn.execute('SELECT * FROM outbox WHERE sent=0 ORDER BY rowid LIMIT 4').fetchall()
        for row in rows:
            result = self.client.ingest(row['run'], json.loads(row['body']))
            if result.get('degraded'):
                raise SupervisorUnavailable('delivery unavailable')
            conn.execute('UPDATE outbox SET sent=1 WHERE id=?', (row['id'],))
        if conn.execute('SELECT 1 FROM outbox WHERE sent=0').fetchone():
            raise SupervisorUnavailable('events awaiting replay')

    def handle(self, payload: dict) -> dict:
        event = payload.get('hook_event_name')
        if event not in EVENTS:
            return {}
        session = payload.get('session_id')
        if not isinstance(session, str) or not 1 <= len(session) <= 300:
            raise ValueError('session ID is missing')
        conn = self._connect(session)
        try:
            if event == 'SessionStart':
                # Judge delayed evidence before deciding whether a completed session
                # may restart. Otherwise an outage could hide its last intervention.
                self._drain(conn)
            root = self._ensure(conn, 'root', restart=event == 'SessionStart')
            agent = payload.get('agent_id')
            child_name = hashlib.sha256((root['run'] + str(agent)).encode()).hexdigest() if agent else None
            stream = self._ensure(conn, child_name, root, restart=event == 'SubagentStart') if child_name and event not in {'SessionStart', 'SessionEnd'} else root
            name = stream['name']
            data = payload.get('tool_input') or {}
            command = data.get('command', '') if isinstance(data, dict) else ''
            call_id = hashlib.sha256((stream['run'] + str(payload.get('tool_use_id') or '')).encode()).hexdigest()
            report, updated_command = prepare_pytest(str(command), str(payload.get('cwd') or '.'), call_id) if payload.get('tool_name') == 'Bash' and payload.get('tool_use_id') else (None, None)
            salt = conn.execute('SELECT value FROM meta WHERE key="salt"').fetchone()[0].encode()
            if event == 'PreToolUse' and report and payload.get('tool_use_id'):
                conn.execute('INSERT OR IGNORE INTO preflight VALUES (?,?)', (call_id, report_marker(report)))
            delivery_id = hashlib.sha256((stream['run'] + 'tool:' + str(payload.get('tool_use_id'))).encode()).hexdigest()
            if event in {'PostToolUse', 'PostToolUseFailure'} and payload.get('tool_use_id') and conn.execute(
                'SELECT 1 FROM outbox WHERE id=?', (delivery_id,)).fetchone():
                self._drain(conn)
                return {}
            if event in {'PostToolUse', 'PostToolUseFailure'} and payload.get('tool_name') in TOOLS:
                tool = payload['tool_name']
                response = payload.get('tool_response') or {}
                response = response if isinstance(response, dict) else {}
                error = str(payload.get('error') or response.get('stderr') or '')
                transient = bool(payload.get('is_interrupt') or response.get('interrupted') or re.search(
                    r'timed?\s*out|timeout|rate.?limit|connection reset|network unreachable|\b(?:429|502|503)\b', error, re.I))
                ok = event == 'PostToolUse' and not response.get('interrupted')
                revision = git_revision(str(payload.get('cwd') or '.'))
                identity = 'tool:' + str(payload.get('tool_use_id') or uuid.uuid4().hex)
                telemetry = {'tool': tool, 'args_fp': fingerprint(data, salt), 'ok': ok, 'rev': revision}
                if not ok:
                    telemetry.update(error_fp=error_fingerprint(error, salt), error_class='transient' if transient else 'permanent')
                self._enqueue(conn, name, 'tool.finished', telemetry, identity)
                before = conn.execute('SELECT marker FROM preflight WHERE id=?', (call_id,)).fetchone()
                interrupted = payload.get('is_interrupt') or response.get('interrupted')
                tests = read_junit(report, salt) if report and before and before['marker'] != report_marker(report) and not interrupted else None
                if tests:
                    self._enqueue(conn, name, 'tests.reported', dict(tests, rev=revision), identity + ':tests')
                    effective = (set(json.loads(stream['effective'])) - set(tests['suite'])) | set(tests['failing'])
                    conn.execute('UPDATE streams SET failures=?,effective=? WHERE name=?',
                                 (len(effective), json.dumps(sorted(effective)), name))
            elif event in {'SessionEnd', 'SubagentStop'} and not stream['closed']:
                if self.client.decide(stream['run'])['allowed']:
                    outcome = 'unknown' if stream['failures'] is None else 'failed' if stream['failures'] else 'success'
                    self._enqueue(conn, name, 'run.finished', {'outcome': outcome}, f'finish:{stream["seq"] + 1}')
                conn.execute('UPDATE streams SET closed=1 WHERE name=?', (name,))
            self._drain(conn)
            if event == 'PreToolUse':
                for current in (root, stream) if name != 'root' else (root,):
                    decision = self.client.decide(current['run'])
                    if decision.get('degraded'):
                        raise SupervisorUnavailable('supervision unavailable')
                    if not decision['allowed']:
                        return denial(decision['state'])
                    conn.execute('UPDATE streams SET closed=0 WHERE name=?', (current['name'],))
                if updated_command:
                    return {'hookSpecificOutput': {'hookEventName': 'PreToolUse',
                        'updatedInput': {**data, 'command': updated_command},
                        'additionalContext': 'Agent Supra added a local JUnit report argument for test-progress monitoring. Normal tool permissions still apply.'}}
            if event in {'SessionStart', 'SubagentStart'}:
                return {'systemMessage': f'Agent Supra: {self.mode} mode. Tool supervision is attached; test progress requires fresh pytest reports. Model spend is not measured.',
                        'hookSpecificOutput': {'hookEventName': event, 'additionalContext': TEST_GUIDANCE}}
            return {}
        except SupervisorAPIError:
            if event == 'PreToolUse':
                return denial('not authenticated or misconfigured')
            return {'systemMessage': 'Agent Supra: check your API key and server configuration.'}
        except SupervisorUnavailable:
            if event == 'PreToolUse' and self.fail_mode == 'closed':
                return denial()
            return {'systemMessage': 'Agent Supra is unavailable. Events recorded after initial attachment remain queued locally where possible.'}
        finally:
            # Commit even after delivery failure: queued events must survive this process.
            conn.commit()
            conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Receive one Claude Code hook payload on stdin. Configure with AGENT_SUPRA_* environment variables; see docs/claude-code.md.')
    parser.parse_args(argv)
    mode = os.environ.get('AGENT_SUPRA_CLAUDE_MODE', 'observe')
    fail_mode = os.environ.get('AGENT_SUPRA_CLAUDE_FAIL_MODE', 'closed' if mode == 'pause' else 'open')
    event = None
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError('hook input is too large')
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError('hook input must be an object')
        event = payload.get('hook_event_name')
        key = os.environ.get('AGENT_SUPRA_API_KEY')
        if not key:
            raise ValueError('AGENT_SUPRA_API_KEY is missing')
        url = validate_url(os.environ.get('AGENT_SUPRA_URL', 'https://agentsupra.in'))
        logging.getLogger('agent_supra').setLevel(logging.CRITICAL)
        scope = hashlib.sha256((url + '\0' + key).encode()).hexdigest()
        state_dir = Path(os.environ.get('AGENT_SUPRA_CLAUDE_STATE_DIR') or (Path.home() / '.agent-supra' / 'claude-code'))
        result = HookBridge(http_client(key, url), state_dir, scope, mode=mode, fail_mode=fail_mode).handle(payload)
    except Exception:
        # Hook payloads and exception messages may contain credentials or private code.
        print('Agent Supra hook could not attach. Check its environment and private state directory.', file=sys.stderr)
        result = denial() if event == 'PreToolUse' and fail_mode == 'closed' else {}
        if event is None and fail_mode == 'closed':
            return 2
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
