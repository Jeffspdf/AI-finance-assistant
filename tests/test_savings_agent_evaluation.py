"""The evaluation recorder must not manufacture live-model successes or leak prose."""
from copy import deepcopy
import json
import sys
import pytest

from scripts import evaluate_savings_agent as evaluation


def test_default_evaluation_is_dry_run_without_loading_credentials(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError('Dry-run must not load saved credentials')
    monkeypatch.setattr(evaluation.ModelSettings, 'load', forbidden)
    monkeypatch.setattr(sys, 'argv', ['evaluate_savings_agent.py', '--settings-dir', 'must-not-read'])
    evaluation.main()
    report = json.loads(capsys.readouterr().out)
    assert report['live'] is False and len(report['cases']) == 8


def test_recorder_preserves_malformed_action_for_runtime_rejection():
    malformed = dict(first_action=['not', 'an', 'action'], preview_steps=[])
    class MalformedProvider:
        def plan_savings_action(self, payload, *, timeout): return deepcopy(malformed)
    recorder = evaluation.RecordingClient(MalformedProvider())
    assert recorder.plan_savings_action({}, timeout=1) == malformed
    assert recorder.calls[0]['action'] == 'other' and 'error' not in recorder.calls[0]


def test_recorder_flags_ungrounded_money_and_never_saves_free_response_text():
    class BadProvider:
        def plan_savings_action(self, payload, *, timeout):
            return dict(first_action=dict(action='call_tool', tool='scenario_simulate_batch',
                                          args=dict(target_cents=600000)),
                        answer='UNTRUSTED_PRIVATE_PROSE')
    recorder = evaluation.RecordingClient(BadProvider())
    recorder.plan_savings_action({}, timeout=1)
    assert recorder.calls[0]['money_literal'] and recorder.calls[0]['free_text']
    assert 'UNTRUSTED_PRIVATE_PROSE' not in json.dumps(recorder.calls)


def test_missing_input_evaluation_case_makes_zero_provider_calls(tmp_path):
    class ForbiddenProvider:
        def plan_savings_action(self, *args, **kwargs):
            raise AssertionError('A local missing-input gate must not call the provider')
    case = next(c for c in evaluation.CASES if c['id'] == 'G1')
    result = evaluation.run_case(case, ForbiddenProvider(), tmp_path)
    assert result['passed'] and result['kind'] == 'local_gate'
    assert result['http_attempts'] == 0 and result['source_ledger_unchanged']


def test_frozen_holdout_rejects_prompt_change_before_loading_credentials(tmp_path, monkeypatch):
    frozen = tmp_path / 'holdout.json'
    frozen.write_text(json.dumps(dict(prompt_sha256='different', cases=[])), encoding='utf-8')
    def forbidden(*args, **kwargs): raise AssertionError('Hash failure must precede credentials/network')
    monkeypatch.setattr(evaluation.ModelSettings, 'load', forbidden)
    monkeypatch.setattr(sys, 'argv', ['evaluate_savings_agent.py', '--live', '--settings-dir', 'must-not-read', '--case-file', str(frozen)])
    with pytest.raises(SystemExit) as stopped: evaluation.main()
    assert stopped.value.code == 2
