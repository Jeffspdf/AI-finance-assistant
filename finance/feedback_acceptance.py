"""Actual controls exercised only by explicit self-tests on synthetic isolated data."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import date
import json
import tkinter as tk
from .storage import Store
from .qa import month_shift
from .help_content import HELP_TOPICS


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(widget,text):
    return next(w for w in descendants(widget) if w.winfo_class()=='Button' and w.cget('text')==text)


def verify_feedback_controls(app,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    original=(app.store,app.mode,app.selected_month,app.page)
    original_bytes=Path(app.store.path).read_bytes()
    old_error=app.report_callback_exception;errors=[];checks=[];layouts={}
    app.report_callback_exception=lambda *args:errors.append(str(args))
    def layout(name,window):
        app.update();spill=[]
        for widget in descendants(window):
            if not widget.winfo_ismapped() or widget.winfo_class() not in ('Button','TEntry','TCombobox','Frame','Listbox','Text'):continue
            left=widget.winfo_rootx()-window.winfo_rootx()
            if left < -3 or left+widget.winfo_width()>window.winfo_width()+3:spill.append(str(widget))
        layouts[name]={'size':[window.winfo_width(),window.winfo_height()],'horizontal_spill':spill}
        assert not spill,(name,spill)
        if window is app:
            guide=app.guide_action
            assert guide.winfo_ismapped() and guide.winfo_height()>=guide.winfo_reqheight()-2,'Sidebar guide action clipped'
            assert guide.winfo_rooty()+guide.winfo_height()<=app.guide.winfo_rooty()+app.guide.winfo_height()+2,'Sidebar guide action outside guide'
        from PIL import ImageGrab
        import ctypes
        ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(folder/(name+'.png'))
    with TemporaryDirectory(prefix='feedback-ledger-',dir=folder) as temp:
        try:
            store=Store(Path(temp)/'ledger.sqlite3');app.store=store;app.mode='demo';app.selected_month=date.today().strftime('%Y-%m')
            identifier=store.add_manual_transaction(date.today().isoformat(),'合成删除示例',10000,'支出','餐饮','仅用于自检')
            card=store.add_account('银行','debit','合成银行卡','1234')
            app.show('records');app.update();app.tree.selection_set(app.tree.get_children()[0]);app.edit_selected()
            editor=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
            entries=[w for w in descendants(editor) if w.winfo_class()=='TEntry']
            entries[1].delete(0,'end');entries[1].insert(0,'未保存的商户')
            entries[3].delete(0,'end');entries[3].insert(0,'999.00')
            before=Path(store.path).read_bytes()
            with patch('tkinter.messagebox.askyesno',return_value=False):button(editor,'删除这笔交易').invoke()
            assert Path(store.path).read_bytes()==before and editor.winfo_exists()
            layout('delete_confirmation_controls',editor)
            with patch('tkinter.messagebox.askyesno',return_value=True) as consent:button(editor,'删除这笔交易').invoke()
            question=consent.call_args.args[1]
            assert '合成删除示例' in question and '100.00' in question and '999.00' not in question
            assert store.transaction_source(identifier)['status']=='deleted' and store.spending_view().empty
            assert not app.tree.selection();checks.append('soft delete uses original record; cancellation is read-only; projections refreshed')
            manager=app.account_window(store);app.update()
            manager.account_state['account_tree'].selection_set('0');app.update()
            button(manager,'编辑选中账户').invoke();app.update()
            editing=next(w for w in manager.winfo_children() if isinstance(w,tk.Toplevel))
            entries=[w for w in descendants(editing) if w.winfo_class()=='TEntry']
            entries[0].delete(0,'end');entries[0].insert(0,'已编辑的卡')
            entries[2].delete(0,'end');entries[2].insert(0,'5678')
            assert len(entries)==3
            with patch('tkinter.messagebox.askyesno',return_value=True):button(editing,'保存账户修改').invoke()
            app.update();assert store.accounts()[0]['label']=='已编辑的卡'
            with patch('tkinter.messagebox.askyesno',return_value=True):button(manager,'归档选中账户').invoke()
            app.update();assert not store.accounts(include_archived=False)
            layout('account_archived',manager);manager.destroy()
            with patch('tkinter.filedialog.askopenfilename',return_value='synthetic.csv'):app.select_import()
            importing=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
            picker=[w for w in descendants(importing) if w.winfo_class()=='TCombobox'][1]
            assert len(picker.cget('values'))==1
            button(importing,'管理账户').invoke();app.update()
            manager=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel) and w is not importing)
            manager.account_state['account_tree'].selection_set('0');app.update()
            with patch('tkinter.messagebox.askyesno',return_value=True):button(manager,'恢复选中账户').invoke()
            app.update();manager.destroy();app.update()
            assert '已编辑的卡' in picker.get() and store.accounts(include_archived=False)[0]['id']==card
            layout('import_restored_account',importing);importing.destroy()
            checks.append('account metadata edit -> archive -> absent in import -> restore -> import picker refresh')
            with patch('socket.socket',side_effect=AssertionError('Help must be offline')):help_window=app.show_help_window()
            app.update();controls=help_window.help_controls
            assert controls['topics'].size()==len(HELP_TOPICS) and controls['text'].cget('state')=='disabled'
            controls['topics'].selection_clear(0,'end');controls['topics'].selection_set(HELP_TOPICS.index('常见问题'));controls['topics'].event_generate('<<ListboxSelect>>');app.update()
            assert '常见问题' in controls['text'].get('1.0','end')
            close=button(help_window,'关闭帮助')
            assert close.winfo_rooty()+close.winfo_height()<=help_window.winfo_rooty()+help_window.winfo_height()+2
            layout('offline_help',help_window);help_window.destroy();checks.append(f'{len(HELP_TOPICS)} offline chapters; readonly text; chapter switching; fixed close button')
            app.selected_month='全部月份';app.show('budget_page');app.update()
            assert app.selected_month==date.today().strftime('%Y-%m')
            future=month_shift(date.today(),1)
            assert all(month_shift(date.today(),i) in app.month_picker.cget('values') for i in range(-24,13))
            app.month_picker.set(future);app.month_picker.event_generate('<<ComboboxSelected>>');app.update()
            app.budget_amount.delete(0,'end');app.budget_amount.insert(0,'75.00');app.save_desktop_budget();app.update()
            assert Store(store.path).budgets(future)=={'餐饮':7500}
            assert any(w.winfo_class()=='Label' and '尚无记录' in w.cget('text') for w in descendants(app.main))
            assert app.budget_rows[0]['预算']==75 and app.budget_rows[0]['已支出']==0
            layout('future_budget',app);checks.append('one month selector; -24..+12 range; future budget persists; missing coverage not zero consumption')
            assert not errors,errors
        finally:
            for child in app.winfo_children():
                if isinstance(child,tk.Toplevel):child.destroy()
            app.store,app.mode,app.selected_month,page=original
            app.report_callback_exception=old_error;app.show(page);app.update()
        assert Path(original[0].path).read_bytes()==original_bytes,'Self-test changed the original ledger'
    report={'data':'isolated synthetic only','checks':checks,'layouts':layouts,'callback_errors':errors,'ui_scale':app.ui_scale,'original_ledger_unchanged':True}
    (folder/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
