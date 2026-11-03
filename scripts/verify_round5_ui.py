"""Round-five workflows with real Tk controls and synthetic local statements."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import ctypes,json,os,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def children(widget):
    for child in widget.winfo_children():yield child;yield from children(child)


def button(widget,text):return next(c for c in children(widget) if c.winfo_class()=='Button' and c.cget('text')==text)
def labels(widget):return '\n'.join(str(c.cget('text')) for c in children(widget) if c.winfo_class()=='Label')
def top(app):return next(c for c in app.winfo_children() if c.winfo_class()=='Toplevel')
def tree(window):return next(c for c in children(window) if c.winfo_class()=='Treeview')


def wait(app,predicate):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        app.update();time.sleep(.01)
        if predicate():return
    raise AssertionError('UI wait timed out')


with TemporaryDirectory(prefix='.test_round5_ui_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from finance.storage import Store
    from finance.m1a_demo import seed_six
    store=Store(Path(temp)/'demo.sqlite3');seed_six(store)
    (Path(temp)/'preferences.json').write_text(json.dumps(dict(space='demo',month='2026-09')))
    from desktop import Desktop
    from finance.ui_scale import px
    from PIL import ImageGrab
    app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(str(args));app.update();app.toggle_animation()
    out=ROOT/'docs/screenshots/round5';out.mkdir(parents=True,exist_ok=True)
    def expense():
        f=store.transactions();return int(f.loc[f.kind=='支出','amount_cents'].sum())
    def ready(w):wait(app,lambda:'发现' in labels(w) and str(button(w,'生成 / 刷新候选').cget('state'))=='normal')
    app.reconciliation_window();app.update();window=top(app);ready(window)
    assert len(tree(window).get_children())==2  # auto scan, no Refresh click
    for _ in range(2):
        t=tree(window);item=next(i for i in t.get_children() if t.item(i,'values')[0]=='待确认')
        t.selection_set(item);t.event_generate('<<TreeviewSelect>>');app.update()
        with patch('finance.desktop_m1a.messagebox.askyesno',return_value=True):button(window,'确认同笔消费').invoke()
        app.update()
    assert expense()==6000;window.destroy();app.update()
    group=next(g for g in store.reconciliation_groups() if g['members'][0]['amount_cents']==2000)
    member=next(r for r in group['members'] if r['role']=='purchase')
    store.correct_categories([(member['id'],'教育')]);app.show('records');app.update()
    assert expense()==8000 and any('待复核' in r['counting_note'] for r in app.record_rows)
    app.reconciliation_window();app.update();window=top(app);ready(window)
    item=next(i for i in tree(window).get_children() if tree(window).item(i,'values')[0]=='待复核')
    tree(window).selection_set(item);tree(window).event_generate('<<TreeviewSelect>>');app.update()
    assert str(button(window,'确认同笔消费').cget('state'))=='disabled'
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'category_needs_review.png')
    window.destroy();app.update()
    # The chooser defaults to automatic format detection.
    fixture=ROOT/'samples/acceptance/m1a_wechat.csv'
    with patch('finance.desktop_m1a.filedialog.askopenfilename',return_value=str(fixture)):app.import_file()
    app.update();window=top(app);combos=[c for c in children(window) if c.winfo_class()=='TCombobox']
    assert combos[0].get()=='自动检测（推荐）'
    button(window,'开始预览').invoke();app.update();window=top(app)
    wait(app,lambda:'已识别：微信 CSV' in labels(window))
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'auto_detect_wechat.png')
    button(window,'取消导入').invoke();app.update()
    # Save an unrelated, amount-only pair and follow the post-import CTA.
    path=Path(temp)/'weak.csv'
    path.write_text('交易ID,日期,商户,描述,金额,类型,平台\nC-W,2026-09-09 12:00:00,果铺,苹果,17,支出,微信\nC-B,2026-09-09 12:00:30,银行结算,外部扣款,17,支出,银行\n',encoding='utf-8-sig')
    app.import_async(str(path));app.update();window=top(app)
    wait(app,lambda:any(c.winfo_class()=='Button' and c.cget('text')=='确认导入并保存来源' for c in children(window)))
    button(window,'确认导入并保存来源').invoke()
    wait(app,lambda:'组待核对候选' in labels(window))
    assert 'C级 1' in labels(window)
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'import_reconcile_cta.png')
    button(window,'去对账核对').invoke();app.update();window=top(app);ready(window)
    t=tree(window);item=next(i for i in t.get_children() if t.item(i,'values')[1]=='C')
    t.selection_set(item);t.event_generate('<<TreeviewSelect>>');app.update()
    assert str(button(window,'确认同笔消费').cget('state'))=='disabled'
    before=expense()
    with patch('finance.desktop_m1a.messagebox.askyesno',side_effect=AssertionError('C must not prompt confirmation')):button(window,'确认同笔消费').invoke()
    assert expense()==before
    clipped=[]
    for widget in children(window):
        if widget.winfo_class()=='Button' and widget.winfo_ismapped() and widget.winfo_rootx()+widget.winfo_width()>window.winfo_rootx()+window.winfo_width()+px(2):clipped.append(widget.cget('text'))
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'C_confirmation_disabled.png')
    window.destroy();app.destroy()
    report=dict(data='synthetic only',auto_scan=True,confirmed_expense_cents=6000,category_changed_expense_cents=8000,
                category_status='needs_review',auto_detect_wechat=True,post_import_cta=True,C_confirm_disabled=True,
                horizontal_clipping=clipped,callback_errors=errors)
    (ROOT/'docs/round5_ui_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    assert not clipped and not errors,(clipped,errors)
print('ROUND5_AUTO_IMPORT_CTA_SCAN_CATEGORY_INVALIDATION_C_GUARD_UI_OK')
