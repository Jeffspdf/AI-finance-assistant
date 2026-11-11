"""Exercise the Tk home using synthetic ledgers on the isolated test desktop."""

from datetime import date
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import time
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch

from finance.desktop_chat_home import DINING_QUESTION, _read_answer
from finance.feedback_acceptance import descendants
from finance.qa import month_shift
from finance.storage import Store


class HeldModel:
    def __init__(self):
        self.started = Event()
        self.release = Event()

    def parse_question(self, _question):
        self.started.set()
        if not self.release.wait(8):
            raise TimeoutError('Test did not release the model response')
        return {'metric': 'expense', 'category': '餐饮', 'period': 'last_month'}


def _labels(widget):
    return ' '.join(w.cget('text') for w in descendants(widget) if isinstance(w, tk.Label))


def _button(widget, text):
    return next(w for w in descendants(widget) if isinstance(w, (tk.Button, ttk.Button)) and w.cget('text') == text)


def _submit(view, question):
    view.question.insert(0, question)
    view.send_button.invoke()


def verify_packaged_entry(app):
    """Quick entry check inside the existing packaged desktop self-test."""
    assert app.page == 'chat_home'
    view = app.chat_home_view
    ledger = Path(app.store.path)
    before = ledger.read_bytes()
    view.dining_button.invoke()
    deadline = time.monotonic() + 8
    while view.cancelled is not None and time.monotonic() < deadline:
        app.update()
        time.sleep(.01)
    assert view.cancelled is None and view.history[-1][0] == 'assistant'
    result = view.history[-1][1]
    assert result['status'] == 'ok' and result['evidence']
    _button(view.messages, '查看依据').invoke()
    evidence = next(w for w in view.winfo_children() if isinstance(w, tk.Toplevel))
    evidence.destroy()
    _submit(view, '把餐饮预算设为 1200')
    assert _button(view.messages, '前往预算与提醒').winfo_exists()
    view.planner_button.invoke()
    planner = app._savings_agent_window
    assert planner.winfo_exists() and set(planner.card_labels) == {'A', 'B', 'C'}
    planner.close()
    assert app.page == 'chat_home' and ledger.read_bytes() == before
    view.clear()
    return dict(default_home=True, dining_answer=True, evidence=True, write_guidance=True,
                existing_abc=True, ledger_bytes_unchanged=True)


def run(output, wait):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='chat-home-', dir=output) as directory:
        data = Path(directory)
        os.environ['FINANCE_DESKTOP_DATA_DIR'] = directory
        os.environ['FINANCE_UI_SCALE'] = '1.5'
        demo = Store(data / 'demo.sqlite3')
        for offset in (-3, -2, -1):
            when = month_shift(date.today(), offset) + '-12'
            demo.add_manual_transaction(when, '合成食堂', 12345, '支出', '餐饮')
            demo.add_manual_transaction(when, '合成地铁', 2500, '支出', '交通')
            demo.add_manual_transaction(when, '合成工资', 520000, '收入', '工资')
        personal = Store(data / 'personal.sqlite3')
        personal.add_manual_transaction(month_shift(date.today(), -1) + '-15', '个人空间合成商户', 8765, '支出', '餐饮')

        from desktop import Desktop
        with patch('finance.ui_scale.work_area', return_value=(0, 0, 1920, 1020)):
            app = Desktop()
            try:
                report = exercise(app, data, wait)
            finally:
                app.destroy()
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


def exercise(app, data, wait):
    errors = []
    app.report_callback_exception = lambda *args: errors.append(str(args))
    app.update()
    assert app.page == 'chat_home'
    view = app.chat_home_view
    assert view.question.winfo_ismapped()
    assert '今天想看看哪笔账' in _labels(view.messages)
    db_paths = [data / 'demo.sqlite3', data / 'personal.sqlite3']
    before = [path.read_bytes() for path in db_paths]

    view.dining_button.invoke()
    wait(app, lambda: view.cancelled is None)
    assert '123.45 元，共 1 笔' in _labels(view.messages)
    _button(view.messages, '查看依据').invoke()
    app.update()
    evidence = next(w for w in view.winfo_children() if isinstance(w, tk.Toplevel))
    source_table = next(w for w in descendants(evidence) if isinstance(w, ttk.Treeview))
    assert '合成食堂' in source_table.item(source_table.get_children()[0], 'values')
    details = next(w for w in descendants(evidence) if isinstance(w, tk.Text))
    assert details.cget('state') == 'disabled'
    evidence.destroy()

    view.planner_button.invoke()
    planner = app._savings_agent_window
    assert planner.winfo_exists() and app.page == 'chat_home'
    planner.confirm_var.set(True)
    planner.generate_button.invoke()
    wait(app, lambda: not planner.busy)
    assert planner.last_result['status'] == 'ok'
    assert all('期末预计' in label.cget('text') for label in planner.card_labels.values())
    planner.close()
    assert app.chat_home_view is view

    with patch('finance.desktop_chat_home._read_answer', side_effect=AssertionError('Write requests must use guidance')) as query:
        _submit(view, '把餐饮预算设为 1200')
        _submit(view, '删除那笔交易')
        assert not query.called
    _button(view.messages, '前往预算与提醒').invoke()
    assert app.page == 'budget_page'
    app.nav['chat_home'].invoke()
    view = app.chat_home_view
    assert '删除那笔交易' in _labels(view.messages)
    assert _labels(view.messages).count('请到') == 2

    # Make the message area overflow, then verify scrolling leaves the input fixed.
    for _ in range(5):
        _submit(view, '把餐饮预算设为 1200')
    app.update()
    assert view.canvas.yview()[1] - view.canvas.yview()[0] < 1
    composer_y = view.composer.winfo_rooty()
    view.canvas.yview_moveto(0)
    app.update()
    assert view.composer.winfo_ismapped() and view.composer.winfo_rooty() == composer_y

    stopped = _check_late_responses(app, wait)
    assert [path.read_bytes() for path in db_paths] == before
    assert not list(data.glob('**/assistant_sessions*.json'))
    assert not errors, errors
    return dict(default_home=True, amount_and_evidence=True, existing_abc=True, write_guidance=True,
                fixed_composer=True, late_results_rejected=stopped, isolated_histories=True,
                ledger_bytes_unchanged=True, callback_errors=errors)


def _check_late_responses(app, wait):
    checked = []
    for action in ('stop', 'clear', 'switch_space'):
        view = app.chat_home_view
        model = HeldModel()
        app.model_client = model
        completed = Event()
        finished_results = []

        def observe(*args):
            try:
                result = _read_answer(*args)
                finished_results.append(result)
                return result
            finally:
                completed.set()

        with patch('finance.desktop_chat_home._read_answer', side_effect=observe):
            _submit(view, '上一个月餐饮花销多少？')
            try:
                wait(app, model.started.is_set)
                if action == 'stop':
                    view.stop_button.invoke()
                elif action == 'clear':
                    _button(view, '新对话').invoke()
                else:
                    app.space_picker.set('我的账本')
                    app.space_picker.event_generate('<<ComboboxSelected>>')
                    app.update()
                    assert app.mode == 'personal'
                    assert not app.chat_home_view.history
                expected = {space: list(history) for space, history in app.chat_home_histories.items()}
            finally:
                model.release.set()
            wait(app, completed.is_set)
            assert len(finished_results) == 1 and finished_results[0]['status'] == 'ok'
            app.update()
            assert app.chat_home_histories == expected
            assert app.chat_home_view.cancelled is None and app.chat_home_view.poll_id is None
            checked.append(action)

    app.model_client = None
    personal = app.chat_home_view
    personal.dining_button.invoke()
    wait(app, lambda: personal.cancelled is None)
    assert '87.65 元，共 1 笔' in _labels(personal.messages)
    assert '123.45 元' not in _labels(personal.messages)
    app.switch_space()
    assert app.mode == 'demo'
    assert '87.65 元' not in _labels(app.chat_home_view.messages)
    assert [role for role, _ in app.chat_home_view.history] == ['user']
    return checked
