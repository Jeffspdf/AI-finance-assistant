"""Real widgets, synthetic ledgers: category domains, pending repair and snapshots."""
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

with TemporaryDirectory(prefix='.test_round7_ui_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from finance.storage import Store
    from finance.m1a_demo import seed_six
    from PIL import ImageGrab
    store=Store(Path(temp)/'demo.sqlite3');seed_six(store)
    (Path(temp)/'preferences.json').write_text(json.dumps(dict(space='demo',month='2026-09')))
    from desktop import Desktop
    app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(str(args));app.update();app.toggle_animation()
    store=app.store;out=ROOT/'docs/screenshots/round7';out.mkdir(parents=True,exist_ok=True)
    def shot(w,name):ImageGrab.grab(window=ctypes.windll.user32.GetParent(w.winfo_id())).save(out/name)
    identifier=store.add_manual_transaction('2026-09-06','收入分类样例',333,'收入','工资')
    app.show('records');app.update()
    income=next(str(i) for i,r in enumerate(app.record_rows) if r['id']==identifier)
    expense=next(str(i) for i,r in enumerate(app.record_rows) if r['kind']=='支出')
    select(app,app.tree,income);assert tuple(app.category.cget('values'))==('工资','生活费','其他收入')
    app.tree.selection_set(income,expense);app.tree.event_generate('<<TreeviewSelect>>');app.update()
    assert not app.category.get() and not app.category.cget('values')
    select(app,app.tree,income);app.edit_selected();app.update();w=tops(app)[0]
    category=[c for c in descendants(w) if c.winfo_class()=='TCombobox'][1]
    assert tuple(category.cget('values'))==('工资','生活费','其他收入');shot(w,'income_category_choices.png');w.destroy();app.update()
    store.generate_candidates()
    for group in store.reconciliation_groups():store.decide_group(group['id'],'confirmed')
    app.reconciliation_window();w=tops(app)[0]
    wait(app,lambda:'发现' in labels(w) and str(button(w,'生成 / 刷新候选').cget('state'))=='normal')
    group=next(g for g in store.reconciliation_groups() if g['members'][0]['amount_cents']==2000)
    member=next(r for r in group['members'] if r['role']=='funding')
    with store.connect() as db:db.execute("UPDATE transactions SET channel='支付宝' WHERE id=?",(member['id'],))
    before=Path(store.path).read_bytes();button(w,'刷新显示（只读）').invoke();app.update()
    t=trees(w)[0];item=next(i for i in t.get_children() if t.item(i,'values')[2]=='20.00');select(app,t,item)
    assert '待修复' in t.item(item,'values')[0] and '存储状态仍为已确认' in labels(w)
    assert Path(store.path).read_bytes()==before and str(button(w,'重新核对').cget('state'))=='disabled'
    assert str(button(w,'修复此组').cget('state'))=='normal';shot(w,'pending_group_repair.png')
    with patch('finance.desktop_m1a.messagebox.askyesno',return_value=False):button(w,'修复此组').invoke()
    assert Path(store.path).read_bytes()==before
    with patch('finance.desktop_m1a.messagebox.askyesno',return_value=True):button(w,'修复此组').invoke()
    app.update();assert next(g for g in store.reconciliation_groups() if g['id']==group['id'])['stored_status']=='needs_review'
    w.destroy();app.update()
    # A genuinely read-only dialog must not schedule a mutating scan.
    readonly=Store(store.path,read_only=True);before=Path(store.path).read_bytes();app.reconciliation_window(readonly)
    w=tops(app)[0];app.update();assert '只读查看' in labels(w)
    for text in ['生成 / 刷新候选','修复此组','确认同笔消费','重新核对']:
        assert str(button(w,text).cget('state'))=='disabled'
    button(w,'刷新显示（只读）').invoke();app.update();assert Path(store.path).read_bytes()==before;w.destroy();app.update()
    with store.connect() as db:db.execute("UPDATE transactions SET category='转账' WHERE id=?",(identifier,))
    store=Store(store.path);app.store=store;app.show('settings');app.update()
    button(app.main,'查看修复明细与备份').invoke();app.update();w=tops(app)[0];snapshot=store.semantic_backup
    assert snapshot and snapshot.is_file() and '不会自动清理' in labels(w)
    t=next(t for t in trees(w) if len(t.cget('columns'))==4);select(app,t,t.get_children()[0])
    before=Path(store.path).read_bytes();button(w,'预览选中快照').invoke();app.update()
    assert str(snapshot) in labels(w) and Path(store.path).read_bytes()==before;shot(w,'repair_history_and_snapshots.png')
    with patch('finance.desktop_m1a.messagebox.askyesno',return_value=False):button(w,'删除选中快照').invoke()
    assert snapshot.is_file()
    with patch('finance.desktop_m1a.messagebox.askyesno',return_value=True):button(w,'删除选中快照').invoke()
    app.update();assert not snapshot.exists() and Path(store.path).read_bytes()==before
    report=dict(data='synthetic only',income_category_filtered=True,mixed_kind_batch_blocked=True,pending_group_repair_action=True,
                repair_cancel_did_not_write=True,readonly_dialog_did_not_scan=True,backup_history_and_preview=True,
                backup_cancel_and_explicit_delete=True,callback_errors=errors)
    (ROOT/'docs/round7_ui_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    w.destroy();app.destroy();assert not errors,errors
print('ROUND7_CATEGORY_REPAIR_AND_BACKUP_UI_OK')
