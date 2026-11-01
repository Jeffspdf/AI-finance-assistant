from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from unittest.mock import patch
import os,sys,json,time,ctypes
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from PIL import ImageGrab
from finance.storage import Store
from finance.demo import demo_bytes
from finance.importer import parse_statement

def children(w):
    for child in w.winfo_children():yield child;yield from children(child)
def button(w,text):return next(c for c in children(w) if c.winfo_class()=='Button' and c.cget('text')==text)
def wait(a,predicate):
    end=time.monotonic()+5
    while time.monotonic()<end:
        a.update();time.sleep(.01)
        if predicate():return
    raise AssertionError('UI wait timed out')
def top(a):return next(c for c in a.winfo_children() if c.winfo_class()=='Toplevel')

with TemporaryDirectory(prefix='.test_round2_ui_',dir=root) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    store=Store(Path(temp)/'demo.sqlite3');store.import_rows(parse_statement(demo_bytes(date(2026,10,9)),'demo.csv').rows)
    (Path(temp)/'preferences.json').write_text(json.dumps({'space':'demo','month':'2026-09'}))
    from desktop import Desktop
    from finance.ui_scale import set_window_geometry
    a=Desktop();set_window_geometry(a,1100,720,minimum=(1100,720));a.update()
    assert a.guide_action.winfo_height()>20
    assert any(c.winfo_class()=='Button' and c.cget('text')=='查看依据' for c in children(a.main))
    a.show('records');a.update();a.sort_table(a.tree,3)
    item=a.tree.get_children()[0];a.tree.selection_set(item);assert a.selected()['id']==a.record_rows[int(item)]['id']
    before={r['id']:r['category'] for r in a.record_rows};selection=a.tree.get_children()[:3];a.tree.selection_set(selection);a.category.set('教育')
    with patch('finance.desktop_round2.messagebox.askyesno',return_value=True):a.correct()
    a.update()
    with patch('finance.desktop_round2.messagebox.showinfo'):a.undo_batch()
    a.update();assert all(a.store.transactions().set_index('id').loc[k,'category']==v for k,v in before.items())
    a.quick_entry();a.update();w=top(a);entries=[c for c in children(w) if c.winfo_class()=='TEntry']
    for entry,value in zip(entries,['25','现金午餐','2026-09-10','验收']):entry.delete(0,'end');entry.insert(0,value)
    button(w,'保存这一笔').invoke();a.update();assert any(r['merchant']=='现金午餐' for r in a.record_rows)
    a.show('assistant');a.ask('累计结余是多少？');a.ask('2026-09餐饮花了多少？');a.ask('餐饮占比呢？');a.update()
    assert len(a.chat_histories['demo'])==6 and '占总支出' in a.answer.cget('text')
    a.ask('上个月花了多少？');a.ask('全部');a.update();assert a.pending_clarification is None and a.answer.cget('text')
    old=a.store.budgets(date.today().strftime('%Y-%m'))
    with patch('finance.conversation.messagebox.askyesno',return_value=False):a.ask('把餐饮预算设为 1200')
    assert a.store.budgets(date.today().strftime('%Y-%m'))==old
    with patch('finance.conversation.messagebox.askyesno',return_value=True):a.ask('把餐饮预算设为 1200')
    assert a.store.budgets(date.today().strftime('%Y-%m'))['餐饮']==120000
    # Async parse/preview/cancel must not write; confirm must commit.
    path=Path(temp)/'import.csv';path.write_text('交易ID,日期,商户,描述,金额,类型\nASYNC,2026-09-11,新店,商品,10,支出\nBAD,bad,新店,商品,10,支出\n',encoding='utf-8-sig')
    count=len(a.store.transactions())
    with patch('finance.desktop_round2.filedialog.askopenfilename',return_value=str(path)):a.import_file()
    a.update();w=top(a)
    wait(a,lambda:any(c.winfo_class()=='Button' and c.cget('text')=='确认导入有效行' for c in children(w)))
    button(w,'取消导入').invoke();a.update();assert len(a.store.transactions())==count
    with patch('finance.desktop_round2.filedialog.askopenfilename',return_value=str(path)):a.import_file()
    a.update();w=top(a)
    wait(a,lambda:any(c.winfo_class()=='Button' and c.cget('text')=='确认导入有效行' for c in children(w)))
    button(w,'确认导入有效行').invoke()
    wait(a,lambda:any(c.winfo_class()=='Button' and c.cget('text')=='完成' for c in children(w)))
    assert len(a.store.transactions())==count+1
    button(w,'完成').invoke();a.update()
    out=root/'docs'/'screenshots';out.mkdir(exist_ok=True,parents=True)
    for page in ['home','records','budget_page','plan','assistant']:
        a.selected_month='2026-09';a.show(page);a.update();a.content_canvas.yview_moveto(0);a.update()
        ImageGrab.grab(window=ctypes.windll.user32.GetParent(a.winfo_id())).save(out/(page+'.png'))
    a.destroy()
print('R2_SMALL_SCREEN_SORT_SAFE_BATCH_UNDO_QUICK_ENTRY_CHAT_CONFIRM_ASYNC_IMPORT_OK')
