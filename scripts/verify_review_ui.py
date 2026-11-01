from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from unittest.mock import patch
import os,sys,json,time,ctypes
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from PIL import ImageGrab

def descendants(w):
    for c in w.winfo_children():yield c;yield from descendants(c)
def button(w,text):return next(c for c in descendants(w) if c.winfo_class()=='Button' and c.cget('text')==text)

with TemporaryDirectory(prefix='review-ui-',dir=root) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from finance.storage import Store
    from finance.demo import demo_bytes
    from finance.importer import parse_statement
    store=Store(Path(temp)/'demo.sqlite3');store.import_rows(parse_statement(demo_bytes(date(2026,10,9)),'review-demo.csv').rows)
    (Path(temp)/'preferences.json').write_text(json.dumps({'space':'demo','month':'2026-09'}))
    from desktop import Desktop
    a=Desktop();a.update();assert len(a.frame)>0
    assert any(c.winfo_class()=='Button' and '超支 ¥45.00' in c.cget('text') for c in descendants(a.main))
    a.show('budget_page');a.update();r=next(r for r in a.budget_rows if r['分类']=='餐饮');assert r['超支']==45
    a.budget_amount.delete(0,'end');a.budget_amount.insert(0,'200');a.save_desktop_budget();a.update();assert a.store.budgets('2026-09')['餐饮']==20000
    a.open_category('餐饮','2026-09');a.update();assert len(a.record_rows)==6
    a.show('plan');a.update()
    # Use an explicit reference date for repeatable three-month evidence.
    with patch('finance.desktop_review.estimate_from_records',side_effect=lambda f:__import__('finance.analytics',fromlist=['estimate_from_records']).estimate_from_records(f,date(2026,10,9))):a.estimate_plan()
    assert '2026-07' in a.estimate_note.cget('text') and a.inputs['每月收入 / 元'].get()!='5000'
    with patch('desktop.messagebox.showinfo'):a.save_plan()
    a.update();a.goal_tree.selection_set(a.goal_tree.get_children()[0]);a.contribution_window();a.update()
    window=next(c for c in a.winfo_children() if c.winfo_class()=='Toplevel')
    button(window,'记录实际存入').invoke();a.update();assert len(a.store.contributions(a.goal_rows[0]['id']))==1
    window.destroy()
    unknown=parse_statement('交易ID,日期,商户,描述,金额,类型,平台,币种\nNEW,2026-09-05,陌生店,待分类商品,18,支出,验收,CNY\n'.encode(),'unknown.csv').rows
    a.store.import_rows(unknown)
    class Mock:
        model='mock-validation-only'
        def suggest_category(self,*args):return '购物'
    a.model_client=Mock();a.show('records');a.update()
    with patch('finance.desktop_review.messagebox.askyesno',return_value=True):a.suggest_categories_ui()
    a.update();window=next(c for c in a.winfo_children() if c.winfo_class()=='Toplevel')
    end=time.monotonic()+4
    while time.monotonic()<end:
        a.update();time.sleep(.02)
        if any(c.winfo_class()=='Button' and c.cget('text')=='接受选中建议' for c in descendants(window)):break
    tree=next(c for c in descendants(window) if c.winfo_class()=='Treeview');tree.selection_set(tree.get_children()[0])
    with patch('finance.desktop_review.messagebox.showinfo'):button(window,'接受选中建议').invoke()
    a.update();assert a.store.transactions().query("external_id=='NEW'").iloc[0].category_source=='模型建议（需核对）'
    a.store.save_budget('2026-09','餐饮',10000)
    out=root/'docs'/'screenshots';out.mkdir(parents=True,exist_ok=True)
    for page in ['home','budget_page','records','plan','assistant']:
        a.show(page);a.update()
        ImageGrab.grab(window=ctypes.windll.user32.GetParent(a.winfo_id())).save(out/(page+'.png'))
    from finance.ui_scale import set_window_geometry
    set_window_geometry(a,1100,720,minimum=(1100,720));a.show('home');a.update();assert a.guide_action.winfo_height()>20
    a.destroy()
print('REVIEW_UI_BUDGET_SOURCE_ESTIMATE_PROGRESS_AI_CONFIRM_MIN_WINDOW_OK')
