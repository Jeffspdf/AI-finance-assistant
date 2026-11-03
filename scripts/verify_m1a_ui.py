"""Synthetic Tk integration: exercise the actual controls without personal data/API."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import ctypes,json,os,sys,time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def descendants(widget):
    for child in widget.winfo_children():yield child;yield from descendants(child)


def button(widget,text):
    return next(c for c in descendants(widget) if c.winfo_class()=='Button' and c.cget('text')==text)


def top(app):return next(c for c in app.winfo_children() if c.winfo_class()=='Toplevel')


def wait(app,predicate):
    end=time.monotonic()+8
    while time.monotonic()<end:
        app.update();time.sleep(.01)
        if predicate():return
    raise AssertionError('UI wait timed out')


with TemporaryDirectory(prefix='.test_m1a_ui_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from finance.storage import Store
    from finance.m1a_demo import seed_six
    from finance.analytics import yuan
    store=Store(Path(temp)/'demo.sqlite3');seed_six(store)
    (Path(temp)/'preferences.json').write_text(json.dumps(dict(space='demo',month='2026-09')))
    from desktop import Desktop
    from finance.ui_scale import set_window_geometry,px
    from PIL import ImageGrab
    app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(str(args));app.update()
    app.toggle_animation();set_window_geometry(app,1100,720,minimum=(1100,720));app.update()
    def expense():
        f=app.store.transactions();return int(f.loc[f.kind=='支出','amount_cents'].sum())
    def screen(name,window):
        app.update();ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/(name+'.png'))
    out=ROOT/'docs/screenshots/m1a';out.mkdir(parents=True,exist_ok=True)
    assert expense()==12000
    app.show('records');app.update();assert len(app.record_rows)==6
    assert any('退款待处理' in r['counting_note'] for r in app.record_rows)
    screen('records_before',app)
    app.reconciliation_window();app.update();window=top(app)
    button(window,'生成 / 刷新候选').invoke();app.update()
    tree=next(c for c in descendants(window) if c.winfo_class()=='Treeview')
    assert len(tree.get_children())==2
    for _ in range(2):
        tree=next(c for c in descendants(window) if c.winfo_class()=='Treeview')
        candidate=next(i for i in tree.get_children() if tree.item(i,'values')[0]=='待确认')
        tree.selection_set(candidate);tree.event_generate('<<TreeviewSelect>>');app.update()
        with patch('finance.desktop_m1a.messagebox.askyesno',return_value=True):button(window,'确认同笔消费').invoke()
        app.update()
    assert expense()==6000
    screen('reconciliation_confirmed',window);window.destroy();app.update()
    app.show('records');app.update();assert len(app.record_rows)==6
    assert sum(r['counting_note']=='资金来源腿 · 不重复计数' for r in app.record_rows)==2
    assert '60.00' in app.result_count.cget('text');screen('records_confirmed',app)
    app.show('home');app.update();screen('home_confirmed',app)
    app.store.save_budget('2026-09','餐饮',1000);app.store.save_budget('2026-09','教育',1000)
    app.show('budget_page');app.update();assert sum(r['已支出'] for r in app.budget_rows)==60
    app.show('assistant');app.ask('2026-09 支出多少？');app.update()
    assert '60.00' in app.answer.cget('text') and '未扣退款' in app.answer.cget('text')
    assert app.chat_histories['demo'][-1]['trace']['evidence'];screen('answer_confirmed',app)
    # Check dialog bounds including the new account/provenance/recovery controls.
    dialogs={}
    for name,open_dialog in [('accounts',app.account_window),('batches',app.import_history_window),('backup',app.backup_window),('six_demo',app.six_row_demo)]:
        open_dialog();app.update();window=top(app)
        clipped=[]
        for widget in descendants(window):
            if widget.winfo_class() not in ('Button','TEntry','TCombobox'):continue
            if widget.winfo_ismapped() and (widget.winfo_rootx()+widget.winfo_width()>window.winfo_rootx()+window.winfo_width()+px(2) or widget.winfo_width()<widget.winfo_reqwidth()-px(3)):
                clipped.append(widget.cget('text') if 'text' in widget.keys() else widget.winfo_class())
        dialogs[name]=dict(size=[window.winfo_width(),window.winfo_height()],clipped=clipped)
        screen(name,window);window.destroy();app.update()
    # Import chooser and cancel: no writes before preview confirmation.
    fixture=ROOT/'samples/acceptance/m1a_wechat.csv';before=len(app.store.import_batches())
    with patch('finance.desktop_m1a.filedialog.askopenfilename',return_value=str(fixture)):app.import_file()
    app.update();window=top(app);screen('import_choice',window)
    combos=[c for c in descendants(window) if c.winfo_class()=='TCombobox'];combos[0].set('微信 CSV')
    button(window,'开始预览').invoke();app.update();window=top(app)
    wait(app,lambda:any(c.winfo_class()=='Button' and c.cget('text')=='确认导入并保存来源' for c in descendants(window)))
    button(window,'取消导入').invoke();app.update();assert len(app.store.import_batches())==before
    app.reconciliation_window();app.update();window=top(app)
    for _ in range(2):
        tree=next(c for c in descendants(window) if c.winfo_class()=='Treeview')
        item=next(i for i in tree.get_children() if tree.item(i,'values')[0]=='已确认');tree.selection_set(item)
        with patch('finance.desktop_m1a.messagebox.askyesno',return_value=True):button(window,'拆分已确认组').invoke()
        app.update()
    assert expense()==12000;window.destroy();app.update()
    report=dict(data='synthetic only',before_cents=12000,confirmed_cents=6000,split_cents=12000,source_rows=6,dialogs=dialogs,callback_errors=errors)
    (ROOT/'docs/m1a_ui_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    app.destroy()
    assert not errors,errors
    assert all(not d['clipped'] for d in dialogs.values()),dialogs
print('M1A_UI_IMPORT_CANCEL_CONFIRM_SPLIT_PROJECTION_EVIDENCE_OK')
