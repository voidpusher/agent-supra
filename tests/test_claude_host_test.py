import json

from scripts.claude_host_test import PYTEST_COMMAND, parse_host_events


def stream(*events):
    return '\n'.join(json.dumps(event) for event in events)


def requested(id='one', command=PYTEST_COMMAND):
    return {'type': 'assistant', 'message': {'content': [
        {'type': 'tool_use', 'name': 'Bash', 'id': id, 'input': {'command': command}}]}}


def test_native_host_evidence_counts_only_exact_pytest_calls():
    result = parse_host_events(stream(requested(), requested('repair', 'python repair.py'),
        {'type': 'result', 'is_error': False, 'total_cost_usd': .1}))
    assert result == {'requested_pytest_tools': 1, 'host_supra_denial': False,
        'cli_reported_cost_usd': .1, 'host_result_error': False, 'host_result_seen': True}


def test_repeated_stream_messages_do_not_double_count_tools():
    assert parse_host_events(stream(requested(), requested()))['requested_pytest_tools'] == 1


def test_native_tool_denial_needs_error_result_and_supra_reason():
    block = {'type': 'tool_result', 'is_error': False, 'content': 'Agent Supra: supervision is paused'}
    assert not parse_host_events(stream({'message': {'content': [block]}}))['host_supra_denial']
    block['is_error'] = True
    assert parse_host_events(stream({'message': {'content': [block]}}))['host_supra_denial']


def test_other_permission_failures_do_not_count_as_supra_denials():
    event = {'message': {'content': [{'type': 'tool_result', 'is_error': True, 'content': 'Permission denied by Claude Code'}]}}
    assert not parse_host_events(stream(event))['host_supra_denial']


def test_native_hook_denial_is_recognized():
    event = {'type': 'system', 'subtype': 'hook_response', 'stdout': json.dumps({
        'hookSpecificOutput': {'permissionDecision': 'deny', 'permissionDecisionReason': 'Agent Supra: paused'}})}
    assert parse_host_events(stream(event))['host_supra_denial']


def test_incomplete_or_invalid_host_output_cannot_claim_a_clean_result():
    result = parse_host_events('not json\n' + stream(None, [], {'message': 'not an object'}, {'message': {'content': ['not a block']}}))
    assert not result['host_result_seen'] and result['host_result_error']


def test_error_result_remains_an_error_even_when_tools_were_requested():
    result = parse_host_events(stream(requested(), {'type': 'result', 'is_error': True}))
    assert result['host_result_seen'] and result['host_result_error']
