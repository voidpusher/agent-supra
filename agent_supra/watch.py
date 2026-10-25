"""Zero-dependency CLI process supervision and private, replayable telemetry."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from .claude_hook import http_client, read_junit, report_marker, validate_url
from .client import SupervisorAPIError, SupervisorUnavailable
from .watch_process import ProcessTree


class Journal:
    def __init__(self, path: Path, scope: str, *, name='Watched command', mode='observe', replay=False, source='cli-watch', check_same_thread=True):
        self.path = Path(path)
        if replay and not (self.path / 'journal.sqlite3').is_file():
            raise ValueError('No watch journal at that location')
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_fd = os.open(self.path / 'watch.lock', os.O_CREAT | os.O_RDWR, 0o600)
        self.lock = os.fdopen(lock_fd, 'r+b')
        try:
            if os.fstat(lock_fd).st_size == 0:
                self.lock.write(b'0')
                self.lock.flush()
            self.lock.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            self.lock.close()
            raise
        db = self.path / 'journal.sqlite3'
        if not replay:
            fd = os.open(db, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        self.conn = sqlite3.connect(db, timeout=3, check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS pending_events ON events(sent,seq);''')
        if replay:
            if self.get('scope') != scope:
                self.conn.close()
                self.lock.close()
                raise ValueError('Replay requires the original API key and endpoint')
            # Never finalize a live or abruptly interrupted command by guessing its outcome.
        else:
            with self.conn:
                for key, value in dict(scope=scope, name=name, mode=mode, salt=secrets.token_hex(32), effective=[]).items():
                    self.set(key, value)
            self.append('run.started', {'source': source})

    def get(self, key, default=None):
        row = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return json.loads(row['value']) if row else default

    def set(self, key, value):
        self.conn.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, json.dumps(value)))

    def _append(self, kind, data):
        if kind != 'run.finished' and self.conn.execute('SELECT count(*) FROM events WHERE sent=0').fetchone()[0] >= 5000:
            raise ValueError('Watch telemetry queue is full; command will be cancelled')
        seq = self.conn.execute('SELECT coalesce(max(seq),0)+1 FROM events').fetchone()[0]
        body = {'seq': seq, 'event_id': uuid.uuid4().hex, 'type': kind, 'ts': time.time(), 'data': data}
        self.conn.execute('INSERT INTO events(seq,body) VALUES (?,?)', (seq, json.dumps(body)))

    def append(self, kind, data):
        with self.conn:
            self._append(kind, data)

    def tests(self, data):
        # Excluded/deleted failing cases must not look fixed at finalization either.
        effective = (set(self.get('effective', [])) - set(data['suite'])) | set(data['failing'])
        with self.conn:
            self._append('tests.reported', dict(data, rev=None))
            self.set('effective', sorted(effective))
            self.set('has_tests', True)

    def finish(self, code, *, cancelled=False):
        if self.get('finished'):
            return
        outcome = 'failed' if cancelled or code or self.get('effective') else 'success' if self.get('has_tests') else 'unknown'
        with self.conn:
            self._append('run.finished', {'outcome': outcome, 'exit_code': code})
            self.set('finished', True)

    def pending(self):
        return self.conn.execute('SELECT count(*) FROM events WHERE sent=0').fetchone()[0]

    def sync(self, client, *, control=True):
        task = self.get('task')
        if not task:
            task = client.create_task(self.get('name'), policy={'observation_mode': self.get('mode') == 'observe'})
            with self.conn:
                self.set('task', task)
        run = self.get('run')
        if not run:
            run = client.create_run(task)
            with self.conn:
                self.set('run', run)
        decision = client.decide(run)
        if control and not decision['allowed']:
            return decision
        for row in self.conn.execute('SELECT * FROM events WHERE sent=0 ORDER BY seq LIMIT 8').fetchall():
            decision = client.ingest(run, json.loads(row['body']))
            with self.conn:
                self.conn.execute('UPDATE events SET sent=1 WHERE seq=?', (row['seq'],))
            if control and not decision['allowed']:
                return decision
        return decision

    def close(self):
        self.conn.close()
        self.lock.close()


class Reports:
    def __init__(self, root: Path, patterns, salt: bytes):
        self.root, self.patterns, self.salt = root.resolve(), [], salt
        self.seen = {}
        for pattern in patterns:
            path = Path(pattern)
            if path.is_absolute():
                path = path.relative_to(self.root)
            if '..' in path.parts or not str(path):
                raise ValueError('JUnit patterns must stay inside the working directory')
            self.patterns.append(str(path))
        for path in self.files():
            self.seen[path] = report_marker(path)

    def files(self):
        found = set()
        for pattern in self.patterns:
            for path in self.root.glob(pattern):
                resolved = path.resolve()
                if not resolved.is_relative_to(self.root):
                    raise ValueError('A JUnit report points outside the working directory')
                if resolved.is_file():
                    found.add(resolved)
                if len(found) > 200:
                    raise ValueError('More than 200 JUnit files matched; narrow the pattern')
        ordered = []
        for path in found:
            try:
                ordered.append((path.stat().st_mtime_ns, str(path), path))
            except FileNotFoundError:
                pass  # The producer may rotate reports between polls.
        return [item[2] for item in sorted(ordered)]

    def scan(self):
        for path in self.files():
            marker = report_marker(path)
            if marker == self.seen.get(path):
                continue
            data = read_junit(path, self.salt)
            if data and marker == report_marker(path):
                self.seen[path] = marker
                yield data


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('must be a finite positive number')
    return number


def replay(journal, client):
    try:
        deadline = time.monotonic() + 60
        while journal.pending() and time.monotonic() < deadline:
            journal.sync(client, control=False)
        return 0 if journal.pending() == 0 else 69
    except (SupervisorUnavailable, SupervisorAPIError):
        print('Replay unavailable; private events remain queued.', file=sys.stderr)
        return 69


def watch(args, client, journal, *, tree_factory=ProcessTree):
    tree = None
    reports = None
    code, cancelled = 1, False
    fail_mode = args.fail_mode or ('closed' if args.mode == 'enforce' else 'open')
    try:
        reports = Reports(args.cwd, args.junit, bytes.fromhex(journal.get('salt')))
        try:
            decision = journal.sync(client)
            if not decision['allowed']:
                cancelled, code = True, 75 if decision['state'] == 'paused' else 125
                return code
        except SupervisorUnavailable:
            if fail_mode == 'closed':
                cancelled, code = True, 69
                return code
            print('Supra unavailable: starting in fail-open mode with private local buffering.', file=sys.stderr)
        env = os.environ.copy()
        env.pop('AGENT_SUPRA_API_KEY', None)
        tree = tree_factory(args.command, cwd=args.cwd, env=env)
        started = time.monotonic()
        warned = False
        while True:
            for data in reports.scan():
                journal.tests(data)
            try:
                decision = journal.sync(client)
                if not decision['allowed']:
                    cancelled = True
                    code = 75 if decision['state'] == 'paused' else 125
                    print(f'Supra {decision["state"]}: cancelling the owned process tree. No automatic restart.', file=sys.stderr)
                    break
                warned = False
            except SupervisorUnavailable:
                if fail_mode == 'closed':
                    cancelled, code = True, 69
                    print('Supervision unavailable: cancelling the owned process tree.', file=sys.stderr)
                    break
                if not warned:
                    print('Supervision unavailable; continuing with private buffered events.', file=sys.stderr)
                    warned = True
            result = tree.poll()
            if result is not None:
                code = result if result >= 0 else 128 + min(-result, 127)
                break
            if args.timeout and time.monotonic() - started >= args.timeout:
                cancelled, code = True, 124
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        cancelled, code = True, 130
    except (OSError, SupervisorAPIError, ValueError):
        cancelled, code = True, 78
        print('Watch could not continue. Check the command, reports, and supervision configuration.', file=sys.stderr)
    finally:
        if tree:
            tree.close()
        # Freeze the owned writers before the final scan: a fast command can
        # produce its last report between the last poll and process exit.
        try:
            for data in reports.scan() if reports else []:
                journal.tests(data)
        except (OSError, ValueError):
            cancelled, code = True, 78
        # Queue the actual exit outcome even if the service is offline. Late finish
        # events cannot clear a pause/stop (the supervisor preserves those states).
        journal.finish(code, cancelled=cancelled)
        try:
            if cancelled and code in {124, 130} and journal.get('run'):
                client.stop(journal.get('run'), 'Watch timeout' if code == 124 else 'Watch interrupted by user')
            for _ in range(3):
                decision = journal.sync(client, control=False)
                if code not in {69, 78, 124, 130}:
                    if decision['state'] == 'paused':
                        code = 75
                    elif decision['state'] == 'stopped':
                        code = 125
                if not journal.pending():
                    break
        except (SupervisorUnavailable, SupervisorAPIError):
            pass
        if journal.get('run'):
            print('Supra run:', journal.get('run'), file=sys.stderr)
        if journal.pending():
            print('Private events remain queued. Replay with: agent-supra watch --replay', journal.path, file=sys.stderr)
    return min(code, 255)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='agent-supra watch', description=__doc__)
    parser.add_argument('--name', default='Watched command', help='public dashboard label (never inferred from command arguments)')
    parser.add_argument('--mode', choices=['observe', 'enforce'], default='observe')
    parser.add_argument('--fail-mode', choices=['open', 'closed'])
    parser.add_argument('--cwd', type=Path, default=Path.cwd())
    parser.add_argument('--junit', action='append', default=[], help='JUnit path/glob inside cwd; repeatable')
    parser.add_argument('--interval', type=positive, default=.5)
    parser.add_argument('--timeout', type=positive, help='local wall-clock timeout in seconds')
    parser.add_argument('--state-dir', type=Path, default=Path.home() / '.agent-supra' / 'watch')
    parser.add_argument('--replay', type=Path, help='replay an existing private journal; never starts a command')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    args.command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if args.replay and args.command:
        parser.error('--replay cannot start a command')
    if not args.replay and not args.command:
        parser.error('provide a command after --')
    args.cwd = args.cwd.resolve()
    if not args.cwd.is_dir() or not 1 <= len(args.name) <= 500 or args.interval < .1 or args.interval > 60:
        parser.error('check cwd, label (1–500 characters), and interval (0.1–60 seconds)')
    journal = None
    try:
        key = os.environ.get('AGENT_SUPRA_API_KEY')
        if not key:
            raise ValueError('API key missing')
        url = validate_url(os.environ.get('AGENT_SUPRA_URL', 'https://agentsupra.in'))
        scope = hashlib.sha256((url + '\0' + key).encode()).hexdigest()
        journal = Journal(args.replay or args.state_dir / uuid.uuid4().hex, scope,
                          name=args.name, mode=args.mode, replay=bool(args.replay))
        client = http_client(key, url)
        client.cooldown = 0  # The watch polling interval already bounds retries.
        if args.replay:
            return replay(journal, client)
        print('Supra private journal:', journal.path, file=sys.stderr)
        print(f'Supra {args.mode} mode. Model usage and spend are not measured.', file=sys.stderr)
        if not args.junit:
            print('No JUnit source: tracking process exit only.', file=sys.stderr)
        return watch(args, client, journal)
    except (OSError, ValueError, sqlite3.Error):
        print('Watch setup failed. Check the key, URL, report paths, and private state directory.', file=sys.stderr)
        return 78
    finally:
        if journal:
            journal.close()


if __name__ == '__main__':
    raise SystemExit(main())
