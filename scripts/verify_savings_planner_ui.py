"""Functional/layout smoke on an owned Windows test desktop, never activated."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import sys
import time
import tkinter as tk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def reveal_test_window(window):
    # Called only in the child process on the owned, never-activated test desktop.
    if '--isolated-child' not in sys.argv:
        raise RuntimeError('Mapped UI smoke requires the isolated test desktop')
    window.deiconify(); window.update()


def wait(root, condition, seconds=8):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        if condition(): return
        time.sleep(.01)
    raise AssertionError('UI smoke exceeded bounded wait')


def run(output):
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    from scripts.evaluate_savings_agent import synthetic_ledger, TODAY
    from finance.desktop_agent import open_savings_planner
    from finance.storage import Store
    from finance.feedback_acceptance import descendants, button
    report = dict(scale=1.5, work_area=[0, 0, 1920, 1020], isolation='owned test desktop; never activated', validation='actual controls and mapped layout; not a screenshot/visual-paint acceptance', checks=[], layouts={}, callback_errors=[])
    with TemporaryDirectory(prefix='.test_agent_ui_', dir=ROOT) as temporary:
        os.environ['FINANCE_DESKTOP_DATA_DIR'] = temporary; os.environ['FINANCE_UI_SCALE'] = '1.5'
        original_init = tk.Tk.__init__
        def hidden_init(self, *args, **kwargs): original_init(self, *args, **kwargs); self.withdraw()
        with patch('finance.ui_scale.work_area', return_value=(0, 0, 1920, 1020)), patch.object(tk.Tk, '__init__', hidden_init):
            from desktop import Desktop
            app = Desktop()
            try:
                app.report_callback_exception = lambda *args: report['callback_errors'].append(str(args))
                store = synthetic_ledger(Path(temporary)); app.store = store; app.model_client = None
                before = Path(store.path).read_bytes()
                app.show('plan'); button(app.main, 'AI 储蓄规划 · A/B/C').invoke()
                window = app._savings_agent_window; window.today = TODAY
                reveal_test_window(window)
                assert not window.allow_model.get() and not window.confirm_var.get()
                window.confirm_var.set(True); window.generate_button.invoke(); wait(app, lambda: not window.busy)
                assert window.last_result['planner'] == 'local_rules'
                assert '¥3,120.00' in window.card_labels['B'].cget('text')
                window.update()
                spill = []
                for widget in descendants(window):
                    if not widget.winfo_ismapped(): continue
                    left = widget.winfo_rootx() - window.winfo_rootx()
                    if left < -3 or left + widget.winfo_width() > window.winfo_width() + 3: spill.append(str(widget))
                report['layouts']['planner'] = dict(size=[window.winfo_width(), window.winfo_height()], horizontal_spill=spill)
                assert not spill, spill
                assert window.generate_button.winfo_height() >= window.generate_button.winfo_reqheight() - 2
                assert window.generate_button.winfo_rooty() + window.generate_button.winfo_height() <= window.winfo_rooty() + window.winfo_height()
                report['checks'].append('actual desktop entry; confirmation/consent defaults; local A/B/C and fixed footer')
                details = window.details('evidence', hidden=True); reveal_test_window(details)
                text = next(w for w in details.winfo_children() if isinstance(w, tk.Text))
                assert text.cget('state') == 'disabled' and 'evidence_ids' in text.get('1.0', 'end')
                details.destroy()
                window.form_canvas.yview_moveto(1); app.update()
                assert window.evidence_button.winfo_rooty() + window.evidence_button.winfo_height() <= window.generate_button.winfo_rooty()
                report['checks'].append('scroll reaches evidence/prefill actions; fixed footer stays visible')
                window.prefill_button.invoke()
                assert app.inputs['方案 B 月预算 / 元'].get() == '4680.00'
                assert Path(store.path).read_bytes() == before
                report['checks'].append('claims evidence is local; prefill reaches existing form without saving')
                window.close()
                empty = Store(Path(temporary) / 'empty.sqlite3'); app.store = empty
                window = open_savings_planner(app); window.today = TODAY
                window.confirm_var.set(True); window.generate_button.invoke(); wait(app, lambda: not window.busy)
                assert window.last_result['can_use_assumptions']
                window.switch_button.invoke(); window.values['income_cents'].set('5200'); window.values['budget_cents'].set('4850')
                window.confirm_var.set(True); window.generate_button.invoke(); wait(app, lambda: not window.busy)
                assert window.last_result['mode'] == 'assumption'
                reveal_test_window(window)
                report['checks'].append('missing history offers explicit switch; assumptions recalculate without history')
                report['source_ledger_unchanged'] = Path(store.path).read_bytes() == before
                window.close()
                from finance.rc2_acceptance import verify_rc2_controls
                report['rc2_controls']=verify_rc2_controls(app,output/'rc2-controls')
                assert not report['callback_errors']
            finally:
                app.destroy()
    (output / 'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def run_live_ui(output, settings_directory):
    """Three UI jobs, real provider, synthetic ledgers; no model/runner doubles."""
    from datetime import date
    from urllib.parse import urlparse
    from finance.llm import ModelClient
    from finance.model_settings import ModelSettings
    from scripts.evaluate_savings_agent import synthetic_ledger
    from finance.feedback_acceptance import button
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=True)
    config = ModelSettings(settings_directory).load()
    if not config.enabled: raise ValueError('Saved model configuration unavailable; settings were not changed')
    client = ModelClient(config.base_url, config.model, config.api_key, enabled=True)
    reference = date(2026, 11, 10)
    report = dict(validation='actual Desktop controls + actual ModelClient/HTTP in one UI session; synthetic finance data only',
                  provider_host=urlparse(config.base_url).hostname, model=config.model, reference_date=reference.isoformat(),
                  cases=[], callback_errors=[], isolation='owned test desktop; never activated')
    with TemporaryDirectory(prefix='.test_agent_live_ui_', dir=ROOT) as temporary:
        os.environ['FINANCE_DESKTOP_DATA_DIR'] = temporary; os.environ['FINANCE_UI_SCALE'] = '1.5'
        original_init = tk.Tk.__init__
        def hidden_init(self, *args, **kwargs): original_init(self, *args, **kwargs); self.withdraw()
        with patch('finance.ui_scale.work_area', return_value=(0, 0, 1920, 1020)), patch.object(tk.Tk, '__init__', hidden_init):
            from desktop import Desktop
            app = Desktop()
            try:
                app.report_callback_exception = lambda *args: report['callback_errors'].append(str(args))
                app.store = synthetic_ledger(Path(temporary)); app.model_client = client
                before = Path(app.store.path).read_bytes()
                app.show('plan'); button(app.main, 'AI 储蓄规划 · A/B/C').invoke()
                window = app._savings_agent_window; window.today = reference; reveal_test_window(window)
                cases = [('L1_UI', None, dict(A=210000, B=312000, C=600000)),
                         ('H4_UI', dict(target_cents='5000', months='8', saved_cents='100', income_cents='3000', budget_cents='2600'), dict(A=330000, C=500000)),
                         ('H5_UI', dict(budget_cents='2450'), dict(A=450000, C=500000))]
                for identifier, changes, expected in cases:
                    if changes:
                        if identifier == 'H4_UI': window.mode_var.set('输入假设')
                        for name, value in changes.items(): window.values[name].set(value)
                        assert window.last_result is None and not window.confirm_var.get()
                    if window.allow_model.get():
                        window.consent_widget.invoke()
                    confirmation = next(w for w in window.body.winfo_children() if w.winfo_class() == 'Checkbutton' and '核对并确认' in w.cget('text'))
                    if not window.confirm_var.get(): confirmation.invoke()
                    window.consent_widget.invoke()
                    assert window.confirm_var.get() and window.allow_model.get()
                    started = time.perf_counter(); window.generate_button.invoke()
                    wait(app, lambda: not window.busy, seconds=105)
                    plan = window.last_result
                    assert plan is not None, 'UI discarded its result'
                    rows = {r['label']: r for r in plan['simulation']['facts']['scenarios']} if plan['simulation'] else {}
                    details = window.details('evidence', hidden=True)
                    text = next(w for w in details.winfo_children() if isinstance(w, tk.Text))
                    evidence = text.get('1.0', 'end'); details.destroy()
                    cards = {name: label.cget('text') for name, label in window.card_labels.items()}
                    passed = plan['model_status'] == 'verified' and plan['planner'] == 'model_tools' and all(
                        rows.get(name, {}).get('projected_cents') == value for name, value in expected.items())
                    row = dict(id=identifier, passed=passed, status=plan['status'], model_status=plan['model_status'],
                               fallback=plan['planner'] == 'local_rules', http_attempts=plan['model_calls'],
                               seconds=round(time.perf_counter()-started, 3), scenarios=rows, cards=cards,
                               claims=plan.get('claims'), tool_trace=plan['model_trace'],
                               readonly_evidence_checked='evidence_ids' in evidence and 'savings_target' in evidence,
                               source_ledger_unchanged=Path(app.store.path).read_bytes() == before,
                               ui_status=window.status.cget('text'))
                    report['cases'].append(row)
                    (output/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                    assert passed and row['readonly_evidence_checked'] and row['source_ledger_unchanged'], identifier
                    assert not report['callback_errors']
                assert client.connection_verified
            finally:
                app.destroy()
    return report


def isolated_run(output):
    """Create an owned test desktop, never SwitchDesktop or activate it."""
    import ctypes
    from ctypes import wintypes
    import subprocess
    import uuid
    user, kernel = ctypes.WinDLL('user32', use_last_error=True), ctypes.WinDLL('kernel32', use_last_error=True)
    user.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    user.CreateDesktopW.restype = wintypes.HANDLE
    user.CloseDesktop.argtypes = [wintypes.HANDLE]
    class Startup(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('reserved', wintypes.LPWSTR), ('desktop', wintypes.LPWSTR),
                    ('title', wintypes.LPWSTR), ('x', wintypes.DWORD), ('y', wintypes.DWORD),
                    ('width', wintypes.DWORD), ('height', wintypes.DWORD), ('xchars', wintypes.DWORD),
                    ('ychars', wintypes.DWORD), ('fill', wintypes.DWORD), ('flags', wintypes.DWORD),
                    ('show', wintypes.WORD), ('reserved_size', wintypes.WORD), ('reserved_data', ctypes.c_void_p),
                    ('stdin', wintypes.HANDLE), ('stdout', wintypes.HANDLE), ('stderr', wintypes.HANDLE)]
    class Process(ctypes.Structure):
        _fields_ = [('process', wintypes.HANDLE), ('thread', wintypes.HANDLE), ('pid', wintypes.DWORD), ('tid', wintypes.DWORD)]
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                                     wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(Startup), ctypes.POINTER(Process)]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = 'CodexSavingsSmoke_' + uuid.uuid4().hex
    desktop = user.CreateDesktopW(name, None, None, 0, 0x01FF, None)
    if not desktop: raise ctypes.WinError(ctypes.get_last_error())
    startup, process = Startup(), Process(); startup.cb = ctypes.sizeof(Startup); startup.desktop = name
    arguments = [sys.executable, str(Path(__file__).resolve()), str(Path(output).resolve()), '--isolated-child']
    if '--regression' in sys.argv: arguments.append('--regression')
    if '--chat-home' in sys.argv: arguments.append('--chat-home')
    if '--build-candidate' in sys.argv: arguments.append('--build-candidate')
    if '--live-model-ui' in sys.argv:
        if '--settings-dir' not in sys.argv: raise ValueError('--live-model-ui requires --settings-dir')
        arguments.extend(['--live-model-ui', '--settings-dir', sys.argv[sys.argv.index('--settings-dir')+1]])
    command = ctypes.create_unicode_buffer(subprocess.list2cmdline(arguments))
    try:
        if not kernel.CreateProcessW(sys.executable, command, None, None, False, 0x08000000, None, str(ROOT), ctypes.byref(startup), ctypes.byref(process)):
            raise ctypes.WinError(ctypes.get_last_error())
        budget = 900 if '--build-candidate' in sys.argv else 300 if '--live-model-ui' in sys.argv else 240 if '--regression' in sys.argv else 90
        end = time.monotonic() + budget
        while kernel.WaitForSingleObject(process.process, 500) == 258:
            if time.monotonic() >= end:
                kernel.TerminateProcess(process.process, 1)
                raise TimeoutError('Owned test child exceeded its time budget')
        code = wintypes.DWORD(); kernel.GetExitCodeProcess(process.process, ctypes.byref(code))
        if code.value:
            detail = Path(output, 'error.txt')
            raise RuntimeError(detail.read_text(encoding='utf-8') if detail.exists() else f'UI child exit {code.value}')
        return json.loads(Path(output, 'results.json').read_text(encoding='utf-8'))
    finally:
        if process.thread: kernel.CloseHandle(process.thread)
        if process.process: kernel.CloseHandle(process.process)
        user.CloseDesktop(desktop)


if __name__ == '__main__':
    if '--isolated-child' in sys.argv:
        try:
            if '--build-candidate' in sys.argv:
                import subprocess
                import shutil
                output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
                shell = shutil.which('pwsh') or shutil.which('powershell')
                if not shell: raise RuntimeError('PowerShell required for the Windows candidate build')
                version = '0.10.0-rc.4'
                command = [shell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(ROOT/'packaging/build.ps1'),
                           '-Python', sys.executable, '-Iscc', str(ROOT.parent/'finance_assistant/tools/inno/ISCC.exe'),
                           '-Version', version, '-CoursePreview']
                with (output/'build.log').open('w', encoding='utf-8') as log:
                    process = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, timeout=840)
                (output/'results.json').write_text(json.dumps(dict(exit_code=process.returncode, version=version,
                    isolation='owned test desktop; never activated')), encoding='utf-8')
                raise SystemExit(process.returncode)
            elif '--live-model-ui' in sys.argv:
                run_live_ui(sys.argv[1], sys.argv[sys.argv.index('--settings-dir')+1])
            elif '--chat-home' in sys.argv:
                from scripts.verify_chat_home import run as run_chat_home
                run_chat_home(sys.argv[1], wait)
            elif '--regression' in sys.argv:
                from contextlib import redirect_stdout, redirect_stderr
                from xml.etree import ElementTree
                import pytest
                output = Path(sys.argv[1]); output.mkdir(parents=True, exist_ok=True)
                with (output / 'pytest.log').open('w', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log):
                    code = pytest.main(['-q', '--basetemp=.test_agent4_full_tmp', '--junitxml=' + str(output / 'regression.xml')])
                suite = ElementTree.parse(output / 'regression.xml').getroot().find('testsuite')
                (output / 'results.json').write_text(json.dumps(dict(exit_code=int(code), isolation='owned test desktop; never activated', **suite.attrib)), encoding='utf-8')
                raise SystemExit(code)
            else:
                run(sys.argv[1])
        except Exception:
            import traceback
            target = Path(sys.argv[1]); target.mkdir(parents=True, exist_ok=True)
            (target / 'error.txt').write_text(traceback.format_exc(), encoding='utf-8')
            raise SystemExit(1)
    else:
        sys.stdout.reconfigure(encoding='utf-8')
        print(json.dumps(isolated_run(sys.argv[1]), ensure_ascii=False))
