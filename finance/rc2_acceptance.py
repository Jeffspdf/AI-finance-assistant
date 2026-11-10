"""Explicit synthetic UI acceptance for the three rc.2 feedback fixes."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import tkinter as tk

from .storage import Store
from .importer import parse_statement
from .help_content import HELP_TOPICS


def verify_rc2_controls(app, directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    original=(app.store,app.mode,app.selected_month,app.page)
    original_bytes=Path(app.store.path).read_bytes()
    errors=[];old_error=app.report_callback_exception
    app.report_callback_exception=lambda *args:errors.append(str(args))
    def wait(predicate):
        until=time.monotonic()+8
        while time.monotonic()<until:
            app.update()
            if predicate():return
            time.sleep(.01)
        raise AssertionError('rc.2 UI operation did not finish')
    try:
        first=app.show_help_window();second=app.show_help_window()
        assert first is second and first.winfo_exists()
        app.update()
        topics=first.help_controls['topics'];text=first.help_controls['text']
        topics.selection_clear(0,'end');topics.selection_set(HELP_TOPICS.index('AI 储蓄规划'))
        topics.event_generate('<<ListboxSelect>>');app.update()
        help_text=text.get('1.0','end')
        for word in ('A/B/C','历史模式','输入假设','claims','工具轨迹','不会写账本'):
            assert word in help_text,word
        assert text.cget('state')=='disabled'
        first.destroy();third=app.show_help_window();assert third is not first;third.destroy()
        with TemporaryDirectory(prefix='rc2-import-',dir=directory) as temporary:
            folder=Path(temporary);store=Store(folder/'ledger.sqlite3')
            data=('交易ID,日期,商户,描述,金额,类型,平台,币种\n'
                  'RC2-ONE,2026-09-01,合成餐厅,午餐,10,支出,样例,CNY\n'
                  'RC2-TWO,2026-09-02,地铁,交通,20,支出,样例,CNY\n').encode('utf-8-sig')
            source=folder/'synthetic.csv';source.write_bytes(data)
            parsed=parse_statement(data,source.name,retain_source=True)
            store.import_statement(parsed);identifier=parsed.rows[0]['id']
            store.soft_delete_transaction(identifier,confirmed=True)
            app.store=store;app.mode='demo';app.selected_month='全部月份';app.show('records')
            preview_bytes=Path(store.path).read_bytes()
            app.import_async(str(source));window=app.import_dialog
            wait(lambda:hasattr(window,'import_controls'))
            controls=window.import_controls
            assert controls['deleted_preview']['deleted_count']==1
            assert controls['restore_deleted'].get() is False
            assert Path(store.path).read_bytes()==preview_bytes
            controls['commit'].invoke();wait(lambda:'outcome' in window.import_controls)
            kept=window.import_controls['outcome']
            assert kept.deleted_skipped==1 and kept.restored==0
            assert store.transaction_source(identifier)['status']=='deleted'
            assert '已删除保持 1 行' in controls['status'].cget('text')
            window.destroy()
            app.import_async(str(source));window=app.import_dialog
            wait(lambda:hasattr(window,'import_controls'))
            from .feedback_acceptance import descendants
            choice=next(w for w in descendants(window) if w.winfo_class()=='Checkbutton' and '恢复本次预览' in w.cget('text'))
            choice.invoke();assert window.import_controls['restore_deleted'].get() is True
            window.import_controls['commit'].invoke();wait(lambda:'outcome' in window.import_controls)
            restored=window.import_controls['outcome']
            assert restored.restored==1 and restored.deleted_skipped==0
            assert store.transaction_source(identifier)['status']=='normal'
            assert int(store.spending_view().amount_cents.sum())==3000
            assert '显式恢复 1 笔' in window.import_controls['status'].cget('text')
            window.destroy()
        assert not errors,errors
        return dict(help_chapters=len(HELP_TOPICS),help_singleton=True,agent_help_checked=True,
                    preview_readonly=True,default_deleted_kept=True,explicit_restore_same_id=True,
                    restored_total_cents=3000,callback_errors=errors,original_ledger_unchanged=Path(original[0].path).read_bytes()==original_bytes)
    finally:
        for child in app.winfo_children():
            if isinstance(child,tk.Toplevel):child.destroy()
        app.store,app.mode,app.selected_month,page=original
        app.report_callback_exception=old_error;app.show(page)
