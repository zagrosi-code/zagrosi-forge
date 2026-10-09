"""Context recovery and readable evidence, derived before runtime edits.

Real public CLI cases cover contracts, retries, status and failing postflight.
The diagnostic-volume case supplies a payload at build_context's boundary to
exercise existing errors/quality schemas without fabricating a real gate result.
Detached authority is exercised only on supported POSIX hosts, never faked.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re

import pytest

from forge_test_helpers import ROOT, load_zagrosi_module, run_process, write_lean_plan_fixture
from test_compact_plan import SECTION, make_plan
from test_context_packets import SECTION as MAPPED_SECTION, mapped_source_plan


@pytest.fixture
def forge():
    return load_zagrosi_module()


def invoke(forge, capsys, *args):
    code = forge.entrypoint.main(list(args))
    captured = capsys.readouterr()
    assert not captured.err, captured.err
    return code, captured.out


def invoke_json(forge, capsys, *args):
    code, output = invoke(forge, capsys, *args)
    return code, json.loads(output)


def file_bytes(directory):
    return {path.relative_to(directory): path.read_bytes()
            for path in directory.rglob('*') if path.is_file()}


@pytest.mark.parametrize('full_output', [False, True])
@pytest.mark.parametrize('defect', ['review', 'mapping'])
def test_actual_context_failures_keep_actionable_details(forge, tmp_path, capsys, defect, full_output):
    if defect == 'review':
        planning = make_plan(tmp_path / 'plan with spaces')
        path = planning / 'sections' / f'{SECTION}.md'
        path.write_text(path.read_text().replace('Verdict: pass', 'Verdict: blocked'))
        section = SECTION
    else:
        planning = mapped_source_plan(tmp_path / 'plan with spaces')
        path = planning / 'codex-plan.md'
        path.write_text(path.read_text().replace('spec.md#L1-L2', 'spec.md#L1-L999'))
        section = MAPPED_SECTION
    before = file_bytes(planning)
    args = ['context-brief', '--planning-dir', str(planning), '--section', section]
    if full_output:
        args.append('--full-output')
    code, payload = invoke_json(forge, capsys, *args)
    assert code == 1 and payload['success'] is False
    assert payload['findings'], payload
    expected_code = 'compact-review-incomplete' if defect == 'review' else 'invalid-requirement-contract'
    assert expected_code in {item['code'] for item in payload['findings']}
    assert 'retry_context' not in payload.get('commands', {})
    pretty_code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert pretty_code == code
    assert pretty.count(payload['error']) == 1
    for finding in payload['findings']:
        assert finding['code'] in pretty
        assert pretty.count(finding['message']) == 1
        assert finding['path'] in pretty
        if finding.get('recommendation'):
            assert finding['recommendation'] in pretty
    assert invoke_json(forge, capsys, *args) == (code, payload)
    assert file_bytes(planning) == before


@pytest.mark.parametrize('selected', [False, True], ids=['whole-plan', 'selected-section'])
@pytest.mark.parametrize('saved', [False, True], ids=['inline', 'saved-output'])
def test_brief_budget_retry_preserves_scope_options_and_original_cwd(
        forge, tmp_path, capsys, monkeypatch, selected, saved):
    origin = tmp_path / 'original project'
    origin.mkdir()
    planning = mapped_source_plan(origin / 'plan with spaces')
    required = 'Preserve the full public contract. ' * 180 + 'REQUIRED-CONTEXT-END'
    source = planning / 'spec.md'
    source.write_text(source.read_text().replace('Preserve callable signatures and defaults.', required))
    destination = origin / 'reports with spaces' / 'brief.md'
    args = ['context-brief', '--planning-dir', 'plan with spaces',
            '--lines-per-artifact', '3', '--max-words', '1']
    if selected:
        args += ['--section', MAPPED_SECTION]
    if saved:
        args += ['--output', 'reports with spaces/brief.md']
    monkeypatch.chdir(origin)
    before = file_bytes(origin)
    code, failed = invoke_json(forge, capsys, *args)
    assert code == 1 and failed['required_words'] > failed['max_words'] == 1
    assert set(failed) == {'success', 'error', 'required_words', 'max_words', 'commands'}
    assert set(failed['commands']) == {'retry_context'}
    retry = failed['commands']['retry_context']
    assert all(isinstance(part, str) for part in retry)
    assert retry[2] == 'context-brief' and retry.count('--max-words') == 1
    parsed = forge.cli.build_parser().parse_args(retry[2:])
    assert parsed.planning_dir == str(planning.resolve())
    assert parsed.section == (MAPPED_SECTION if selected else None)
    assert parsed.lines_per_artifact == 3 and parsed.max_words == failed['required_words']
    assert parsed.output == (str(destination.resolve()) if saved else None)
    pretty_code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert pretty_code == 1 and f"--max-words {failed['required_words']}" in pretty
    budget_lines = '\n'.join(line for line in pretty.splitlines()
                             if '--planning-dir' not in line and any(word in line.lower() for word in ('word', 'budget')))
    assert re.search(rf"\b{failed['required_words']}\b", budget_lines)
    assert re.search(r'\b1\b', budget_lines)
    assert file_bytes(origin) == before and not destination.parent.exists()
    elsewhere = tmp_path / 'retry elsewhere'
    elsewhere.mkdir()
    completed = run_process(retry, cwd=elsewhere, timeout=30)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    result = json.loads(completed.stdout)
    content = Path(result['output']).read_text() if saved else result['content']
    assert result['section'] == (MAPPED_SECTION if selected else None)
    assert result['max_words'] == failed['required_words']
    assert result['word_count'] <= result['max_words']
    assert content.count(required) == 1
    if selected:
        assert (planning / 'sections' / f'{MAPPED_SECTION}.md').read_text().rstrip() in content
        assert 'Add unrelated billing functionality.' not in content
    if saved:
        assert result['content'] is None and Path(result['output']) == destination.resolve()
    else:
        assert result['output'] is None
    assert not list(elsewhere.iterdir())


@pytest.mark.parametrize('explicit_output', [False, True], ids=['default-output', 'explicit-output'])
def test_packet_budget_retry_preserves_section_and_destination(
        forge, tmp_path, capsys, monkeypatch, explicit_output):
    origin = tmp_path / 'original project'
    origin.mkdir()
    planning = make_plan(origin / 'plan with spaces')
    section = planning / 'sections' / f'{SECTION}.md'
    required = 'Required linked behavior. ' * 180 + 'LINKED-CONTRACT-END'
    (planning / 'linked contract.md').write_text('# Required contract\n\n' + required + '\n')
    section.write_text(section.read_text() + '\n[Required](<../linked contract.md>)\n')
    args = ['implementation-packet', '--planning-dir', 'plan with spaces',
            '--section', SECTION, '--max-words', '1']
    if explicit_output:
        args += ['--output-dir', 'generated packets']
    output_dir = origin / 'generated packets' if explicit_output else planning / '.forge' / 'packets'
    monkeypatch.chdir(origin)
    before = file_bytes(origin)
    code, failed = invoke_json(forge, capsys, *args)
    assert code == 1 and failed['required_words'] > failed['max_words'] == 1
    assert set(failed) == {'success', 'error', 'required_words', 'max_words', 'commands'}
    retry = failed['commands']['retry_context']
    assert retry[2] == 'implementation-packet' and retry.count('--max-words') == 1
    parsed = forge.cli.build_parser().parse_args(retry[2:])
    assert parsed.planning_dir == str(planning.resolve()) and parsed.section == SECTION
    assert parsed.max_words == failed['required_words'] and parsed.implementation_root is None
    assert parsed.output_dir == (str(output_dir.resolve()) if explicit_output else None)
    assert file_bytes(origin) == before and not output_dir.exists()
    elsewhere = tmp_path / 'retry elsewhere'
    elsewhere.mkdir()
    completed = run_process(retry, cwd=elsewhere, timeout=30)
    assert completed.returncode == 0, completed.stderr + completed.stdout
    result = json.loads(completed.stdout)
    output = Path(result['output'])
    assert output == output_dir.resolve() / f'{SECTION}-packet.md'
    assert result['section'] == SECTION and result['requirements'] == ['REQ-001']
    assert result['word_count'] <= result['max_words'] == failed['required_words']
    assert output.read_text().count(required) == 1
    assert section.read_text().rstrip() in output.read_text()
    assert not list(elsewhere.iterdir())


@pytest.mark.parametrize('command', ['context-brief', 'implementation-packet'])
@pytest.mark.parametrize('defect', ['mapping', 'broken-link', 'invalid-budget'])
def test_non_budget_context_failures_never_offer_retry(forge, tmp_path, capsys, command, defect):
    planning = mapped_source_plan(tmp_path / 'plan')
    if defect == 'mapping':
        path = planning / 'codex-plan.md'
        path.write_text(path.read_text().replace('spec.md#L1-L2', 'spec.md#L3-L1'))
    elif defect == 'broken-link':
        path = planning / 'sections' / f'{MAPPED_SECTION}.md'
        path.write_text(path.read_text() + '\n[Missing contract](../missing.md)\n')
    output = tmp_path / 'never written'
    args = [command, '--planning-dir', str(planning), '--section', MAPPED_SECTION,
            '--output' if command == 'context-brief' else '--output-dir', str(output)]
    if defect == 'invalid-budget':
        args += ['--max-words', '0']
    before = file_bytes(planning)
    code, result = invoke_json(forge, capsys, *args)
    assert code == 1 and result['success'] is False
    assert 'retry_context' not in result.get('commands', {})
    assert 'content' not in result and not output.exists()
    assert file_bytes(planning) == before


def test_packet_coverage_failure_is_not_a_budget_retry(forge, tmp_path, capsys):
    planning = write_lean_plan_fixture(tmp_path / 'plan')
    (planning / 'spec.md').write_text('REQ-002: Unrelated source requirement.\n')
    output = tmp_path / 'never written'
    code, result = invoke_json(forge, capsys, 'implementation-packet', '--planning-dir', str(planning),
                               '--section', 'section-01-lean-default', '--output-dir', str(output))
    assert code == 1 and result['coverage_gaps'], result
    assert 'retry_context' not in result.get('commands', {}) and not output.exists()


@pytest.mark.parametrize('saved', [False, True])
def test_successful_brief_displays_requested_inline_content_or_saved_path(forge, tmp_path, capsys, saved):
    planning = make_plan(tmp_path / 'plan with spaces')
    args = ['context-brief', '--planning-dir', str(planning), '--section', SECTION]
    code, inline = invoke_json(forge, capsys, *args)
    assert code == 0 and inline['content'] and inline['output'] is None
    if saved:
        output = tmp_path / 'saved brief.md'
        args += ['--output', str(output)]
    code, payload = invoke_json(forge, capsys, *args)
    pretty_code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert code == pretty_code == 0
    if saved:
        assert payload['content'] is None and Path(payload['output']).read_text() == inline['content']
        assert pretty.count(payload['output']) == 1
        assert inline['content'].strip() not in pretty
    else:
        assert payload == inline and pretty.count(inline['content'].strip()) == 1
    assert invoke_json(forge, capsys, *args) == (code, payload)


@pytest.mark.parametrize('kind', ['context', 'quality'])
@pytest.mark.parametrize('view', ['inline', 'bounded-report', 'full-output'])
def test_diagnostics_preserve_every_detail_once_and_leave_payload_unchanged(
        forge, tmp_path, capsys, monkeypatch, kind, view):
    findings = [{'severity': 'high', 'code': f'contract-{number:02d}',
                 'message': f'Repair unique contract number {number:02d}.',
                 'path': f'src/contract-{number:02d}.py', 'recommendation': f'Action {number:02d}.'}
                for number in range(12)]
    errors = [f'Unique source mapping error number {number:02d}.' for number in range(12)]
    summary = 'The requested context cannot be assembled.'
    payload = {'success': False, 'error': summary, 'findings': findings,
               'errors': [*errors, errors[0], summary], 'diagnostics': deepcopy(findings[:2]),
               'packet': {'content': 'NESTED-CONTEXT-MUST-STAY-HIDDEN' * 50},
               'review': 'NESTED-REVIEW-MUST-STAY-HIDDEN' * 50}
    if kind == 'quality':
        payload.update(gate='context-budget', score=0, strict=True)
    if view != 'inline':
        report = tmp_path / 'complete report.json'
        payload['full_report'] = str(report)
        report.write_text(json.dumps(payload))
    before = deepcopy(payload)
    monkeypatch.setattr(forge.context, 'build_context', lambda *args, **kwargs: payload)
    args = ['context-brief', '--planning-dir', str(tmp_path)]
    if view == 'full-output':
        args.append('--full-output')
    assert invoke_json(forge, capsys, *args) == (1, before)
    code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert code == 1 and pretty.count(summary) == 1
    assert pretty.count(findings[0]['message']) == 1
    if view == 'bounded-report':
        assert 'more finding(s)' in pretty and findings[-1]['message'] not in pretty
    else:
        for finding in findings:
            assert pretty.count(finding['message']) == 1
            assert finding['code'] in pretty and finding['path'] in pretty
            assert finding['recommendation'] in pretty
        for error in errors:
            assert pretty.count(error) == 1
    if view != 'inline':
        assert str(report) in pretty and json.loads(report.read_text()) == before
    assert 'NESTED-CONTEXT-MUST-STAY-HIDDEN' not in pretty
    assert 'NESTED-REVIEW-MUST-STAY-HIDDEN' not in pretty
    assert payload == before and invoke_json(forge, capsys, *args) == (1, before)


def test_full_output_postflight_displays_actual_gate_failures_once(forge, tmp_path, capsys):
    planning = write_lean_plan_fixture(tmp_path / 'plan')
    (planning / 'codex-plan-tdd.md').write_text('No verification supplied.\n')
    args = ['postflight', '--phase', 'plan', '--planning-dir', str(planning), '--strict', '--full-output']
    code, payload = invoke_json(forge, capsys, *args)
    assert code == 1 and 'full_report' not in payload
    findings = {json.dumps(item, sort_keys=True): item for gate in payload['gates']
                for item in gate.get('payload', {}).get('findings', [])}
    errors = {error for gate in payload['gates'] for error in [
        gate.get('payload', {}).get('error'), *gate.get('payload', {}).get('errors', [])]
        if isinstance(error, str) and error}
    assert findings, payload
    pretty_code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert pretty_code == code
    for finding in findings.values():
        assert finding['code'] in pretty and finding['message'] in pretty
        if finding.get('path'):
            assert finding['path'] in pretty
        if finding.get('recommendation'):
            assert finding['recommendation'] in pretty
    for message in {item['message'] for item in findings.values()} | errors:
        assert pretty.count(message) == 1
    assert invoke_json(forge, capsys, *args) == (code, payload)


@pytest.mark.parametrize('depth', ['lean', 'standard', 'deep'])
def test_fresh_scaffold_status_marks_draft_and_file_presence(forge, tmp_path, capsys, monkeypatch, depth):
    target = tmp_path / 'target project'
    target.mkdir()
    planning = tmp_path / 'draft plan'
    planning.mkdir()
    spec = planning / 'spec.md'
    spec.write_text('Preserve existing errors while adding preview.\n')
    monkeypatch.chdir(target)
    code, setup = invoke_json(forge, capsys, 'plan-setup', '--file', str(spec), '--plugin-root', str(ROOT),
                              '--target-dir', str(target), '--depth', depth, '--flight', 'off')
    assert code == 0 and setup['scaffold']['unfinished'] is True
    before = file_bytes(planning)
    args = ['status', '--path', str(planning)]
    code, payload = invoke_json(forge, capsys, *args)
    assert code == 0 and payload['success'] is True and payload['scaffold_unfinished'] is True
    assert payload['section_progress']['state'] == 'complete'
    assert payload['next_action'] == 'complete the draft plan: choose section boundaries, fill the contract, and record review'
    pretty_code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert pretty_code == code and 'Plan: DRAFT' in pretty
    assert 'Section files: 1/1 present' in pretty and 'Sections: 1/1 (complete)' not in pretty
    assert payload['next_action'] in pretty
    assert invoke_json(forge, capsys, *args) == (code, payload)
    assert file_bytes(planning) == before


@pytest.mark.skipif(os.name != 'posix', reason='Existing detached authority requires POSIX fcntl and descriptor-relative I/O.')
def test_detached_packet_retry_reopens_changed_authority_without_writing(forge, tmp_path, capsys, monkeypatch):
    from detached_test_support import canonical_json_bytes_for_test, make_detached_record_fixture, planning_tree_snapshot

    origin = tmp_path.resolve() / 'detached project'
    fixture = make_detached_record_fixture(origin)
    expected_planning = planning_tree_snapshot(fixture.planning)
    output = fixture.implementation_root / 'code_review' / 'new packet directory'
    monkeypatch.chdir(origin)
    code, failed = invoke_json(forge, capsys, 'implementation-packet', '--planning-dir', 'planning',
                               '--section', fixture.section, '--max-words', '1',
                               '--implementation-root', 'detached-implementation',
                               '--output-dir', 'detached-implementation/code_review/new packet directory')
    assert code == 1 and failed['required_words'] > failed['max_words'] == 1, failed
    retry = failed['commands']['retry_context']
    parsed = forge.cli.build_parser().parse_args(retry[2:])
    assert parsed.planning_dir == str(fixture.planning)
    assert parsed.implementation_root == str(fixture.implementation_root)
    assert parsed.output_dir == str(output) and parsed.section == fixture.section
    assert parsed.max_words == failed['required_words'] and not output.exists()
    assert planning_tree_snapshot(fixture.planning) == expected_planning
    changed = json.loads(fixture.admission_pinner.read_text())
    changed['o_sha256'] = 'sha256:' + '44' * 32
    fixture.admission_pinner.write_bytes(canonical_json_bytes_for_test(changed))
    elsewhere = tmp_path / 'different retry cwd'
    elsewhere.mkdir()
    completed = run_process(retry, cwd=elsewhere, timeout=30)
    result = json.loads(completed.stdout)
    assert completed.returncode == 1 and result['error_code'] == 'admission-pinner-drift', result
    assert 'retry_context' not in result.get('commands', {})
    assert not output.exists() and planning_tree_snapshot(fixture.planning) == expected_planning


def test_summarized_action_error_preserves_its_diagnostic_metadata(forge):
    payload = {'success': False, 'error': 'Restore the missing authorization evidence.',
               'error_code': 'authority-missing', 'path': 'src/auth.py',
               'recommendation': 'Reopen the authority file before retrying.',
               'next_command': ['forge', 'check-auth', '--strict'],
               'gates': [{'name': 'admission', 'success': False,
                          'payload': {'error': 'A required gate is blocked.'}}]}
    summary = forge.output.failure_summary(payload)
    try:
        before = deepcopy(summary)
        pretty = forge.output.format_pretty(summary)
        for detail in (payload['error_code'], payload['path'], payload['recommendation']):
            assert detail in pretty
        assert pretty.count(payload['error']) == 1
        assert pretty.count('check-auth --strict') == 1
        assert summary == before
    finally:
        Path(summary['full_report']).unlink()


def test_full_output_preserves_nested_diagnostics_and_distinct_error_contexts(forge, tmp_path, capsys, monkeypatch):
    message = 'Restore this missing authorization evidence.'
    finding = {'severity': 'high', 'code': 'nested-contract',
               'message': 'Retain the nested contract.', 'path': 'src/contract.py'}
    payload = {'success': False, 'phase': 'plan', 'stage': 'postflight', 'gates': [
        {'name': 'admission', 'success': False, 'payload': {'nested': [
            {'error': message, 'path': 'src/first.py', 'recommendation': 'Repair the first authority.'},
            {'errors': [message], 'path': 'src/second.py', 'recommendation': 'Repair the second authority.'},
            {'findings': [finding], 'content': 'NESTED-BODY-MUST-STAY-HIDDEN'},
        ]}},
    ]}
    before = deepcopy(payload)
    monkeypatch.setattr(forge.context, 'build_context', lambda *args, **kwargs: payload)
    args = ['context-brief', '--planning-dir', str(tmp_path), '--full-output']
    assert invoke_json(forge, capsys, *args) == (1, before)
    code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert code == 1
    for detail in ('src/first.py', 'src/second.py', 'Repair the first authority.',
                   'Repair the second authority.', finding['code'], finding['message'], finding['path']):
        assert pretty.count(detail) == 1
    assert 'NESTED-BODY-MUST-STAY-HIDDEN' not in pretty
    assert payload == before


def test_direct_quality_formatter_keeps_action_error_finding(forge):
    message = 'Restore the authorization evidence.'
    finding = {'severity': 'high', 'code': 'gate-error', 'message': message}
    payload = {'success': False, 'gate': 'authority', 'error': message, 'findings': [finding]}
    before = deepcopy(payload)
    pretty = '\n'.join(forge.output.format_quality(payload))
    assert pretty.count(message) == 1 and 'gate-error' in pretty
    assert payload == before


@pytest.mark.parametrize('command', ['context-brief', 'implementation-packet'])
@pytest.mark.parametrize('error_type', [RuntimeError, OSError])
def test_budget_failure_survives_unresolvable_retry_destination(
        forge, tmp_path, capsys, monkeypatch, command, error_type):
    planning = make_plan(tmp_path / 'plan with spaces')
    destination = str(tmp_path / 'unresolvable destination' / 'output')
    expected = forge.context.build_context(planning, SECTION, 1)
    assert expected['success'] is False and expected['required_words'] > 1
    original_resolve = forge.storage.resolve_path

    def resolve_path(raw):
        if raw == destination:
            raise error_type('Cannot resolve the requested output destination.')
        return original_resolve(raw)

    monkeypatch.setattr(forge.storage, 'resolve_path', resolve_path)
    before = file_bytes(tmp_path)
    option = '--output' if command == 'context-brief' else '--output-dir'
    code, result = invoke_json(forge, capsys, command, '--planning-dir', str(planning),
                               '--section', SECTION, '--max-words', '1', option, destination)
    assert code == 1 and result == expected
    assert 'retry_context' not in result.get('commands', {})
    assert file_bytes(tmp_path) == before and not Path(destination).parent.exists()


@pytest.mark.parametrize('nested_entry', [False, True], ids=['packet', 'entry-packet'])
@pytest.mark.parametrize('summarized', [False, True], ids=['inline', 'summarized'])
def test_packet_error_keeps_unrendered_recovery_actions(forge, nested_entry, summarized):
    packet = {'success': False, 'error': 'The packet authority needs repair.',
              'next_action': 'Restore the missing packet evidence.',
              'next_command': ['forge', 'repair-packet', '--strict'],
              'commands': {'retry': ['forge', 'packet-retry', '--all']}}
    payload = {'success': False, 'packet': packet}
    if nested_entry:
        payload = {'success': False, 'entry': payload}
    original = deepcopy(payload)
    view = forge.output.failure_summary(payload) if summarized else payload
    try:
        before = deepcopy(view)
        pretty = forge.output.format_pretty(view)
        for detail in (packet['error'], packet['next_action'], 'repair-packet', '--strict',
                       'packet-retry', '--all'):
            assert pretty.count(detail) == 1
        assert view == before and payload == original
        if summarized:
            assert json.loads(Path(view['full_report']).read_text()) == original
    finally:
        if summarized:
            Path(view['full_report']).unlink()


def test_eval_suite_pretty_accepts_numeric_finding_counts(forge, tmp_path, capsys):
    planning = write_lean_plan_fixture(tmp_path / 'plan')
    args = ['eval-suite', '--examples-dir', str(tmp_path)]
    code, payload = invoke_json(forge, capsys, *args)
    assert code == 0 and payload['success'] is True
    assert len(payload['rows']) == 1
    row = payload['rows'][0]
    assert Path(row['planning_dir']) == planning
    assert isinstance(row['findings'], int) and row['findings'] > 0
    pretty_code, pretty = invoke(forge, capsys, *args, '--pretty')
    assert pretty_code == code and 'Status: PASS' in pretty
    assert invoke_json(forge, capsys, *args) == (code, payload)
