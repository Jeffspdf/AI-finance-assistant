"""Actual Tk controls, hidden windows, synthetic ledgers and no real provider calls."""
from datetime import date
from pathlib import Path
import threading
import time
import tkinter as tk

import pytest

from finance.desktop_agent import SavingsPlannerWindow, prefill_values
from finance.agent_planner import plan_savings_rules
from finance.agent_runtime import plan_savings_agent
from finance.storage import Store
from finance.ui_scale import init_scale
from finance.ui_theme import apply_theme
from test_agent_savings import history, inputs, TODAY
from test_agent_runtime import ScriptedModel


def pump(root, predicate, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('UI state did not complete within the bounded wait')


@pytest.fixture(scope='module')
def tk_root():
    root = tk.Tk(); root.withdraw(); init_scale(root, 1.5); apply_theme(root)
    yield root
    root.destroy()


@pytest.fixture
def ui(tmp_path, tk_root):
    root = tk_root
    for widget in root.winfo_children(): widget.destroy()
    root.store = history(tmp_path); root.mode = 'demo'; root.model_client = None
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args))
    root.calculate_calls = 0
    def show(page):
        assert page == 'plan'
        root.inputs = {name: tk.Entry(root) for name in ('目标金额 / 元', '已有储蓄 / 元', '每月收入 / 元',
                                                       '方案 A 月预算 / 元', '方案 B 月预算 / 元', '截止日期 YYYY-MM-DD')}
    root.show = show
    root.calculate = lambda: setattr(root, 'calculate_calls', root.calculate_calls + 1)
    window = SavingsPlannerWindow(root, today=TODAY, hidden=True)
    try:
        yield root, window, errors
    finally:
        if window.winfo_exists(): window.close()
        root.update_idletasks()
        assert not errors, errors


def generate(root, window):
    window.confirm_var.set(True); window.generate_button.invoke()
    pump(root, lambda: not window.busy)


def test_confirmation_default_and_local_cards_are_read_only(ui):
    root, window, _ = ui
    before = Path(root.store.path).read_bytes()
    assert not window.allow_model.get() and not window.confirm_var.get()
    window.generate_button.invoke()
    assert not window.busy and '勾选确认' in window.status.cget('text')
    generate(root, window)
    assert window.last_result['planner'] == 'local_rules'
    assert '本地规则规划' in window.status.cget('text')
    assert '¥2,100.00' in window.card_labels['A'].cget('text')
    assert '¥3,120.00' in window.card_labels['B'].cget('text')
    assert Path(root.store.path).read_bytes() == before


def test_model_requires_checkbox_and_verified_cards_use_same_amounts(ui):
    root, window, _ = ui
    model = ScriptedModel(); root.model_client = model
    generate(root, window)
    assert not model.payloads and window.last_result['model_status'] == 'consent_required'
    window.allow_model.set(True)
    generate(root, window)
    assert window.last_result['model_status'] == 'verified'
    assert '模型理解 + 代码计算' in window.status.cget('text')
    assert '¥3,120.00' in window.card_labels['B'].cget('text')
    assert model.payloads


def test_explicit_switch_and_assumption_recalculation(ui, tmp_path):
    root, window, _ = ui
    empty = Store(tmp_path / 'empty.sqlite3'); root.store = empty
    window.origin_path = str(Path(empty.path).resolve())
    generate(root, window)
    assert window.last_result['can_use_assumptions'] and window.mode_var.get() == '历史账单'
    assert window.switch_button.winfo_manager() == 'pack'
    window.switch_button.invoke()
    assert window.mode_var.get() == '输入假设' and not window.confirm_var.get()
    window.values['income_cents'].set('5200'); window.values['budget_cents'].set('4850')
    before = Path(empty.path).read_bytes()
    generate(root, window)
    assert window.last_result['mode'] == 'assumption'
    assert '缺少分类预算' in window.card_labels['B'].cget('text')
    window.values['budget_cents'].set('4700')
    assert window.last_result is None and not window.confirm_var.get()
    generate(root, window)
    assert '¥3,000.00' in window.card_labels['A'].cget('text')
    assert Path(empty.path).read_bytes() == before


def test_prefill_only_changes_original_form_no_saved_goal(ui):
    root, window, _ = ui
    generate(root, window)
    before = Path(root.store.path).read_bytes()
    window.selected_scenario.set('C'); window.prefill_button.invoke()
    assert root.inputs['目标金额 / 元'].get() == '6000.00'
    assert root.inputs['方案 B 月预算 / 元'].get() == '4200.00'
    assert root.inputs['截止日期 YYYY-MM-DD'].get() == '2027-05-09'
    assert root.calculate_calls == 1 and '尚未保存' in window.status.cget('text')
    assert Path(root.store.path).read_bytes() == before


def test_details_show_local_claims_and_tools_in_readonly_controls(ui):
    root, window, _ = ui
    generate(root, window)
    detail = window.details('evidence')
    box = next(w for w in detail.winfo_children() if isinstance(w, tk.Text))
    assert box.cget('state') == 'disabled'
    assert 'evidence_ids' in box.get('1.0', 'end') and 'savings_target' in box.get('1.0', 'end')
    detail.destroy()
    trace = window.details('trace')
    box = next(w for w in trace.winfo_children() if isinstance(w, tk.Text))
    assert 'scenario_simulate_batch' in box.get('1.0', 'end')
    trace.destroy()


def test_changed_ledger_invalidates_result_before_prefill(ui):
    root, window, _ = ui
    generate(root, window)
    with root.store.connect() as db: db.execute("UPDATE transactions SET amount_cents=12345 WHERE kind='支出'")
    window.prefill_button.invoke()
    assert window.last_result is None and '已变化' in window.status.cget('text')
    assert root.calculate_calls == 0


def test_cancel_discards_late_response_without_tk_calls_from_worker(ui):
    root, window, _ = ui
    released = threading.Event(); started = threading.Event()
    def delayed(*args, **kwargs):
        started.set(); assert released.wait(3)
        return plan_savings_agent(*args, **kwargs)
    window.runner = delayed
    window.confirm_var.set(True); window.generate_button.invoke()
    pump(root, started.is_set)
    assert window.busy and window.generate_button.cget('state') == 'disabled'
    window.cancel_button.invoke(); released.set()
    pump(root, lambda: not window.busy)
    assert window.last_result is None and '已取消' in window.status.cget('text')
    assert all('已取消' in label.cget('text') for label in window.card_labels.values())


def test_input_change_while_busy_discards_captured_old_inputs(ui):
    root, window, _ = ui
    released = threading.Event(); started = threading.Event()
    def delayed(*args, **kwargs):
        started.set(); assert released.wait(3)
        return plan_savings_agent(*args, **kwargs)
    window.runner = delayed; window.confirm_var.set(True); window.generate_button.invoke()
    pump(root, started.is_set)
    window.values['target_cents'].set('8000'); released.set()
    pump(root, lambda: not window.busy)
    assert not window.confirm_var.get() and window.last_result is None
    assert window.prefill_button.cget('state') == 'disabled'


def test_model_settings_change_revokes_active_authorization(ui):
    root, window, _ = ui
    released = threading.Event(); started = threading.Event()
    def blocking(client, payload): started.set(); assert released.wait(3)
    root.model_client = ScriptedModel(on_call=blocking)
    window.allow_model.set(True); window.confirm_var.set(True); window.generate_button.invoke()
    pump(root, started.is_set)
    root.model_client = ScriptedModel()
    pump(root, lambda: not window.consent.allowed)
    released.set(); pump(root, lambda: not window.busy)
    assert not window.allow_model.get() and window.last_result is None


def test_revoking_consent_mid_request_returns_local_result(ui):
    root, window, _ = ui
    started = threading.Event(); released = threading.Event()
    def wait_first(client, payload):
        started.set(); assert released.wait(3)
    model = ScriptedModel(on_call=wait_first); root.model_client = model
    window.allow_model.set(True); window.confirm_var.set(True); window.generate_button.invoke()
    pump(root, started.is_set)
    window.allow_model.set(False); window.change_consent(); released.set()
    pump(root, lambda: not window.busy)
    assert window.last_result['model_status'] == 'consent_revoked' and len(model.payloads) == 1
    assert '本地规则规划' in window.status.cget('text')


def test_space_change_closes_window_and_revokes_job(ui, tmp_path):
    root, window, _ = ui
    root.store = Store(tmp_path / 'other-space.sqlite3')
    pump(root, lambda: not window.winfo_exists())


def test_ambiguous_duration_is_inline_validation_not_a_model_call(ui):
    root, window, _ = ui
    root.model_client = ScriptedModel(); window.allow_model.set(True)
    window.values['months'].set('年底'); window.confirm_var.set(True); window.generate_button.invoke()
    assert not window.busy and not root.model_client.payloads
    assert '整数个月' in window.status.cget('text')


@pytest.mark.parametrize('failure,status_text', [('invalid_action', '工具计划无效'), ('invalid_claims', '未通过校验'),
                                               ('timed_out', '超时'), ('round_limit', '轮数上限'), ('model_failure', '调用失败')])
def test_failure_labels_always_show_local_fallback(ui, failure, status_text):
    root, window, _ = ui
    generate(root, window)
    result = window.last_result.copy(); result['model_status'] = failure
    window.render_result(result)
    assert '本地规则规划' in window.status.cget('text') and status_text in window.status.cget('text')


def test_prefill_deadline_short_month_and_unavailable_scenario():
    result = plan_savings_rules(mode='assumption', today=date(2028, 1, 31), inputs=inputs(months=1, income_cents=1, budget_cents=0))
    assert prefill_values(result, 'A')['截止日期 YYYY-MM-DD'] == '2028-02-29'
    with pytest.raises(ValueError): prefill_values(result, 'B')


def test_negative_cashflow_cannot_be_prefilled_into_legacy_clamped_calculator():
    plan = plan_savings_agent(mode='assumption', question='按输入规划', today=TODAY,
        inputs=inputs(income_cents=100000, budget_cents=200000))
    assert plan['simulation']['facts']['scenarios'][0]['monthly_available_cents'] == -100000
    with pytest.raises(ValueError, match='负现金流'): prefill_values(plan, 'A')
