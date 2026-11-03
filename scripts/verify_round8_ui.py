"""Real-widget checks for visible legacy categories and confirmation-time replacement."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import ctypes,json,os,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def descendants(w):
    for child in w.winfo_children():yield child;yield from descendants(child)
def button(w,text):return next(c for c in descendants(w) if c.winfo_class()=='Button' and c.cget('text')==text)
def labels(w):return '\n'.join(str(c.cget('text')) for c in descendants(w) if c.winfo_class()=='Label')
def tops(app):return [w for w in app.winfo_children() if w.winfo_class()=='Toplevel']
def trees(w):return [c for c in descendants(w) if c.winfo_class()=='Treeview']
def wait(app,predicate):
    end=time.monotonic()+10
    while time.monotonic()<end:
        app.update();time.sleep(.01)
        if predicate():return
    raise AssertionError('UI wait timed out')
def select(app,t,item):t.selection_set(item);t.event_generate('<<TreeviewSelect>>');app.update()

with TemporaryDirectory(prefix='.test_round8_ui_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from finance.storage import Store
    from finance.importer import parse_statement
    from finance.m1a_demo import seed_six
    from PIL import ImageGrab
    store=Store(Path(temp)/'demo.sqlite3');seed_six(store)
    content='交易ID,日期,商户,描述,金额,类型,平台\n'+''.join(f'R8-{i},2026-09-20,合成普通消费,回归数据,1,支出,银行\n' for i in range(205))
    store.import_rows(parse_statement(content.encode(),'many.csv').rows)
    income=store.add_manual_transaction('2025-01-01','历史收入错分类',100,'收入','工资')
    expense=store.add_manual_transaction('2026-08-01','历史支出错分类',200,'支出','餐饮')
    with store.connect() as db:
        db.execute("UPDATE transactions SET category='餐饮' WHERE id=?",(income,))
        db.execute("UPDATE transactions SET category='工资' WHERE id=?",(expense,))
    (Path(temp)/'preferences.json').write_text(json.dumps(dict(space='demo',month='2026-09')))
    from desktop import Desktop
    app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(str(args));app.update();app.toggle_animation();store=app.store
    out=ROOT/'docs/screenshots/round8';out.mkdir(parents=True,exist_ok=True)
    def shot(w,name):ImageGrab.grab(window=ctypes.windll.user32.GetParent(w.winfo_id())).save(out/name)
    before=Path(store.path).read_bytes();app.show('settings');button(app.main,'历史分类待修正').invoke();app.update();w=tops(app)[0]
    assert '2 笔分类待修正' in labels(w);shot(w,'legacy_category_conflicts.png')
    kind=next(c for c in descendants(w) if c.winfo_class()=='TCombobox');kind.set('收入');kind.event_generate('<<ComboboxSelected>>');app.update()
    assert '1 笔分类待修正' in labels(w) and Path(store.path).read_bytes()==before
    t=trees(w)[0];select(app,t,t.get_children()[0]);button(w,'定位到账本').invoke();app.update()
    assert app.selected_month=='全部月份' and app.record_page==1 and app.selected()['id']==income
    assert Path(store.path).read_bytes()==before
    button(app.main,'历史分类待修正').invoke();app.update();w=tops(app)[0];t=trees(w)[0]
    item=next(i for i in t.get_children() if t.item(i,'values')[2]=='收入');select(app,t,item)
    button(w,'定位并编辑').invoke();app.update();editor=tops(app)[0]
    assert '原记录为“收入 / 餐饮”' in labels(editor)
    category=[c for c in descendants(editor) if c.winfo_class()=='TCombobox'][1];assert not category.get()
    shot(editor,'explicit_legacy_category_edit.png')
    button(editor,'保存修改').invoke();app.update();assert Path(store.path).read_bytes()==before
    assert '请先选择' in labels(editor)
    category.set('其他收入');button(editor,'保存修改').invoke();app.update()
    row=store.source_transactions().set_index('id').loc[income];assert row.kind=='收入' and row.amount_cents==100 and row.category=='其他收入'
    assert store.category_conflicts().id.tolist()==[expense]
    # A list captured from one space must not edit another space after switching.
    app.category_conflicts_window();app.update();w=tops(app)[0];select(app,trees(w)[0],trees(w)[0].get_children()[0]);before=Path(store.path).read_bytes()
    app.switch_space();app.update()
    with patch('desktop.messagebox.showinfo') as info:button(w,'定位并编辑').invoke()
    assert info.call_count==1 and Path(store.path).read_bytes()==before
    w.destroy();app.switch_space();app.update();store=app.store
    readonly=Store(store.path,read_only=True);app.store=readonly;app.category_conflicts_window();app.update();w=tops(app)[0]
    assert str(button(w,'定位并编辑').cget('state'))=='disabled';w.destroy();app.store=store
    app.reconciliation_window();w=tops(app)[0]
    wait(app,lambda:'发现' in labels(w) and str(button(w,'生成 / 刷新候选').cget('state'))=='normal')
    assert '打开后自动扫描' in labels(w) and '不会自动确认或合并' in labels(w);shot(w,'scan_boundary_notice.png');w.destroy();app.update()
    snapshot=store.backup();app.semantic_backups_window();app.update();w=tops(app)[0]
    t=next(t for t in trees(w) if len(t.cget('columns'))==4);select(app,t,t.get_children()[0])
    def change_during_confirmation(*args,**kwargs):snapshot.write_bytes(b'synthetic replacement snapshot');return True
    with patch('finance.desktop_m1a.messagebox.askyesno',side_effect=change_during_confirmation):button(w,'删除选中快照').invoke()
    app.update();assert snapshot.read_bytes()==b'synthetic replacement snapshot' and '确认期间已变化' in labels(w)
    report=dict(data='synthetic only',legacy_conflicts_visible_across_months=True,read_only_discovery=True,
                exact_record_located_on_second_page=True,explicit_edit_required=True,other_space_guarded=True,
                readonly_editor_disabled=True,scan_boundary_notice=True,changed_snapshot_not_deleted=True,callback_errors=errors)
    (ROOT/'docs/round8_ui_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    w.destroy();app.destroy();assert not errors,errors
print('ROUND8_LEGACY_DISCOVERY_AND_SNAPSHOT_CONFIRMATION_UI_OK')
