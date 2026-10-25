import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_supra.claude_hook import HookClient
from agent_supra.client import SupervisorUnavailable, SupervisorAPIError
from agent_supra.watch import Journal, Reports, watch, replay


@pytest.fixture
def backend(account):
    server, key = account
    def transport(method, path, body, params):
        result = server.request(method, path, json=body, params=params, headers={'Authorization': f'Bearer {key}'})
        return result.status_code, result.json()
    return HookClient(api_key=key, retries=0, transport=transport)


def args(folder, command, **options):
    values = dict(cwd=folder, command=command, junit=['results.xml'], mode='enforce', fail_mode=None, interval=.1, timeout=15)
    values.update(options)
    return SimpleNamespace(**values)


def write_report(path, failing=True, skipped=False):
    path.write_text('<testsuite><testcase classname="private.code" name="private_test">'
        + ('<skipped/>' if skipped else '<failure message="private trace"/>' if failing else '') + '</testcase></testsuite>')


def test_report_reader_ignores_stale_malformed_and_tracks_recovery(tmp_path):
    report = tmp_path / 'results.xml'
    write_report(report)
    reader = Reports(tmp_path, ['results.xml'], b'private-salt')
    assert list(reader.scan()) == []
    report.write_text('partial')
    assert list(reader.scan()) == []
    write_report(report, False)
    [data] = reader.scan()
    assert data['failing'] == [] and len(data['suite']) == 1
    assert 'private' not in json.dumps(data)


def test_journal_replays_with_stable_ids_and_sequences(backend, tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope')
    journal.tests({'suite': ['a'], 'failing': ['a']})
    journal.finish(1)
    original = [row[0] for row in journal.conn.execute('SELECT body FROM events ORDER BY seq')]
    broken = SimpleNamespace(create_task=lambda *a, **kw: (_ for _ in ()).throw(SupervisorUnavailable('offline')))
    assert replay(journal, broken) == 69
    journal.close()
    reopened = Journal(tmp_path / 'journal', 'scope', replay=True)
    assert replay(reopened, backend) == 0
    run = reopened.get('run')
    timeline = backend.timeline(run)
    assert [event['seq'] for event in timeline['events']] == [1, 2, 3]
    assert timeline['run']['state'] == 'failed'
    assert replay(reopened, backend) == 0
    assert len(backend.timeline(run)['events']) == 3
    assert original == [row[0] for row in reopened.conn.execute('SELECT body FROM events ORDER BY seq')]
    reopened.close()


def test_journal_rejects_other_account_and_concurrent_replay(tmp_path):
    path = tmp_path / 'journal'
    journal = Journal(path, 'scope')
    with pytest.raises(OSError):
        Journal(path, 'scope', replay=True)
    journal.close()
    with pytest.raises(ValueError, match='original'):
        Journal(path, 'another-account', replay=True)


def test_removed_failure_is_not_success_and_late_finish_preserves_pause(backend, tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope', mode='enforce')
    for _ in range(4):
        journal.tests({'suite': ['a'], 'failing': ['a']})
    journal.tests({'suite': ['b'], 'failing': []})
    journal.finish(0)
    assert replay(journal, backend) == 0
    assert backend.decide(journal.get('run'))['state'] == 'paused'
    finish = json.loads(journal.conn.execute('SELECT body FROM events ORDER BY seq DESC LIMIT 1').fetchone()[0])
    assert finish['data']['outcome'] == 'failed'
    journal.close()


def test_initial_closed_outage_never_launches_process(tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope', mode='enforce')
    backend = SimpleNamespace(create_task=lambda *a, **kw: (_ for _ in ()).throw(SupervisorUnavailable('offline')))
    def forbidden(*a, **kw):
        raise AssertionError('must not launch')
    assert watch(args(tmp_path, ['unused']), backend, journal, tree_factory=forbidden) == 69
    assert journal.get('finished')
    journal.close()


def test_initial_auth_error_never_launches_even_fail_open(tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope')
    backend = SimpleNamespace(create_task=lambda *a, **kw: (_ for _ in ()).throw(SupervisorAPIError(401, 'private response')))
    def forbidden(*a, **kw):
        raise AssertionError('must not launch')
    assert watch(args(tmp_path, ['unused'], mode='observe'), backend, journal, tree_factory=forbidden) == 78
    journal.close()


def test_real_child_exit_and_no_watch_key_in_child_environment(backend, tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_SUPRA_API_KEY', 'private-sentinel')
    code = "import os; from pathlib import Path; Path('key-status').write_text('present' if 'AGENT_SUPRA_API_KEY' in os.environ else 'removed'); print('private child output')"
    journal = Journal(tmp_path / 'journal', 'scope')
    assert watch(args(tmp_path, [sys.executable, '-c', code], junit=[], mode='observe'), backend, journal) == 0
    assert (tmp_path / 'key-status').read_text() == 'removed'
    timeline = backend.timeline(journal.get('run'))
    assert timeline['run']['state'] == 'completed'
    assert 'private child output' not in json.dumps(timeline)
    journal.close()


@pytest.mark.parametrize('mode,expected', [('enforce', 75), ('observe', 1)])
def test_real_stuck_process_is_cancelled_only_in_enforcement(backend, tmp_path, mode, expected):
    worker = tmp_path / 'worker.py'
    worker.write_text("from pathlib import Path\nimport time\nfor i in range(7):\n Path('results.xml').write_text('<testsuite><testcase classname=\"fixture\" name=\"a\"><failure/></testcase></testsuite>')\n time.sleep(.6)\nraise SystemExit(1)\n")
    journal = Journal(tmp_path / 'journal', 'scope', mode=mode)
    assert watch(args(tmp_path, [sys.executable, str(worker)], mode=mode), backend, journal) == expected
    timeline = backend.timeline(journal.get('run'))
    assert timeline['run']['state'] == ('paused' if mode == 'enforce' else 'failed')
    assert timeline['interventions'][0]['action'] == ('pause' if mode == 'enforce' else 'flag')
    journal.close()


def test_timeout_cancels_owned_grandchild(backend, tmp_path):
    worker = tmp_path / 'worker.py'
    worker.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',\"from pathlib import Path; import time; p=Path('heartbeat'); [(p.write_text(str(i)),time.sleep(.1)) for i in range(200)]\"])\ntime.sleep(30)\n")
    journal = Journal(tmp_path / 'journal', 'scope')
    assert watch(args(tmp_path, [sys.executable, str(worker)], junit=[], timeout=1.5), backend, journal) == 124
    marker = tmp_path / 'heartbeat'
    before = marker.read_text()
    time.sleep(.4)
    assert marker.read_text() == before
    assert backend.decide(journal.get('run'))['state'] == 'stopped'
    journal.close()


def test_dashboard_stop_cancels_the_actual_process(backend, tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope')
    actual_decide = backend.decide
    count = 0
    def decide(run):
        nonlocal count
        count += 1
        if count == 3:
            backend.stop(run, 'test reviewer stop')
        return actual_decide(run)
    backend.decide = decide
    assert watch(args(tmp_path, [sys.executable, '-c', 'import time; time.sleep(30)'], junit=[]), backend, journal) == 125
    assert actual_decide(journal.get('run'))['state'] == 'stopped'
    journal.close()


def test_out_of_scope_report_is_rejected_before_launch(backend, tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope')
    def forbidden(*a, **kw):
        raise AssertionError('must not launch')
    assert watch(args(tmp_path, ['unused'], junit=['../outside.xml']), backend, journal, tree_factory=forbidden) == 78
    journal.close()


def test_journal_capacity_still_records_cancelled_finish(tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope')
    with journal.conn:
        journal.conn.executemany('INSERT INTO events(seq,body) VALUES (?,?)', [(n, '{}') for n in range(2, 5001)])
    with pytest.raises(ValueError, match='full'):
        journal.tests({'suite': ['a'], 'failing': []})
    journal.finish(78, cancelled=True)
    assert journal.get('finished')
    journal.close()


def test_fail_open_attaches_after_initial_outage_and_flushes_reports(backend, tmp_path):
    class Recovery:
        count = 0
        def create_task(self, *a, **kw):
            self.count += 1
            if self.count <= 3:
                raise SupervisorUnavailable('temporary outage')
            return backend.create_task(*a, **kw)
        def __getattr__(self, name):
            return getattr(backend, name)
    worker = tmp_path / 'worker.py'
    worker.write_text("from pathlib import Path\nimport time\ntime.sleep(.5)\nPath('results.xml').write_text('<testsuite><testcase name=\"ok\"/></testsuite>')\ntime.sleep(.5)\n")
    journal = Journal(tmp_path / 'journal', 'scope')
    assert watch(args(tmp_path, [sys.executable, str(worker)], mode='observe', fail_mode='open'), Recovery(), journal) == 0
    assert journal.pending() == 0
    timeline = backend.timeline(journal.get('run'))
    assert timeline['run']['state'] == 'completed'
    assert len([e for e in timeline['events'] if e['type'] == 'tests.reported']) == 1
    journal.close()


def test_nonzero_child_exit_is_preserved(backend, tmp_path):
    journal = Journal(tmp_path / 'journal', 'scope')
    assert watch(args(tmp_path, [sys.executable, '-c', 'raise SystemExit(7)'], junit=[]), backend, journal) == 7
    assert backend.timeline(journal.get('run'))['run']['state'] == 'failed'
    journal.close()


def test_many_atomic_reports_are_all_read_in_order(tmp_path):
    reader = Reports(tmp_path, ['*.xml'], b'salt')
    for i in range(4):
        write_report(tmp_path / f'{i}.xml', failing=i != 3)
    data = list(reader.scan())
    assert len(data) == 4 and data[-1]['failing'] == []
    assert list(reader.scan()) == []


def test_final_report_written_at_exit_is_not_lost(backend, tmp_path):
    class FastExit:
        def __init__(self, *a, **kw):
            pass
        def poll(self):
            write_report(tmp_path / 'results.xml', False)
            return 0
        def close(self):
            pass
    journal = Journal(tmp_path / 'journal', 'scope')
    assert watch(args(tmp_path, ['synthetic']), backend, journal, tree_factory=FastExit) == 0
    timeline = backend.timeline(journal.get('run'))
    assert len([e for e in timeline['events'] if e['type'] == 'tests.reported']) == 1
    assert timeline['run']['outcome'] == 'success'
    journal.close()
