import ast
from pathlib import Path

import pytest

from scripts.repository_agent_test import FIXES, apply_plan, parse_plan


@pytest.mark.parametrize('raw', [
    '{"fixes":["check_report"],"path":".env"}',
    '{"fixes":["execute_command"]}', '{"fixes":[{}]}',
    '{"fixes":"fresh_report"}', '[]', '{"fixes":[]}' * 300,
])
def test_repository_agent_rejects_unreviewed_edit_instructions(raw):
    with pytest.raises(ValueError):
        parse_plan(raw)


def test_repository_agent_accepts_a_plan_without_executing_model_code():
    assert parse_plan('```json\n{"fixes":["fresh_report","check_report"]}\n```') == {
        'fresh_report', 'check_report'}


def test_reviewed_edits_change_only_the_approved_function(tmp_path):
    source = Path('agent_supra/demo.py').read_text(encoding='utf-8')
    target = tmp_path / 'agent_supra/demo.py'
    target.parent.mkdir()
    target.write_text(source, encoding='utf-8')
    apply_plan(tmp_path, FIXES)
    updated = target.read_text(encoding='utf-8')
    before = ast.parse(source)
    after = ast.parse(updated)
    for module in (before, after):
        module.body = [n for n in module.body if not isinstance(n, ast.FunctionDef) or n.name != 'run_tests']
    assert ast.dump(before) == ast.dump(after)
    compile(updated, str(target), 'exec')
    with pytest.raises(ValueError):
        apply_plan(tmp_path, {'read_private_file'})
    assert target.read_text(encoding='utf-8') == updated
