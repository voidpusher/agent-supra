"""Real regressions: the example agent must not treat a broken test runner as a fix."""
import subprocess

import pytest

from agent_supra.demo import run_tests

PASS_XML = '<testsuites><testsuite><testcase classname="shop" name="test_ok"/></testsuite></testsuites>'
FAIL_XML = '<testsuites><testsuite><testcase classname="shop" name="test_bad"><failure/></testcase></testsuite></testsuites>'


def fake_runner(monkeypatch, repo, code, report=None):
    def run(*args, **kwargs):
        if report is not None:
            (repo / '.junit.xml').write_text(report)
        return subprocess.CompletedProcess(args[0], code, stdout='', stderr='runner fixture')
    monkeypatch.setattr('agent_supra.demo.subprocess.run', run)


def test_runner_error_must_not_reuse_old_passing_report(tmp_path, monkeypatch):
    (tmp_path / '.junit.xml').write_text(PASS_XML)
    fake_runner(monkeypatch, tmp_path, 4)
    with pytest.raises(RuntimeError, match='pytest runner'):
        run_tests(tmp_path)


def test_success_exit_must_not_reuse_old_report(tmp_path, monkeypatch):
    (tmp_path / '.junit.xml').write_text(PASS_XML)
    fake_runner(monkeypatch, tmp_path, 0)
    with pytest.raises(RuntimeError, match='report'):
        run_tests(tmp_path)


def test_missing_report_has_actionable_error(tmp_path, monkeypatch):
    fake_runner(monkeypatch, tmp_path, 1)
    with pytest.raises(RuntimeError, match='report'):
        run_tests(tmp_path)


def test_malformed_report_has_actionable_error(tmp_path, monkeypatch):
    fake_runner(monkeypatch, tmp_path, 0, '<broken')
    with pytest.raises(RuntimeError, match='report'):
        run_tests(tmp_path)


def test_runner_crash_is_not_an_ordinary_failing_test(tmp_path, monkeypatch):
    fake_runner(monkeypatch, tmp_path, 3, FAIL_XML)
    with pytest.raises(RuntimeError, match='pytest runner'):
        run_tests(tmp_path)


def test_empty_suite_is_not_a_successful_repair(tmp_path, monkeypatch):
    fake_runner(monkeypatch, tmp_path, 0, '<testsuites/>')
    with pytest.raises(RuntimeError, match='No tests'):
        run_tests(tmp_path)


@pytest.mark.parametrize('code,xml,failing', [(0, PASS_XML, []), (1, FAIL_XML, ['shop::test_bad'])])
def test_valid_test_results_are_preserved(tmp_path, monkeypatch, code, xml, failing):
    fake_runner(monkeypatch, tmp_path, code, xml)
    result = run_tests(tmp_path)
    assert result.failing == failing and len(result.suite) == 1
