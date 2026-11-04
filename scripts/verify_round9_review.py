"""Verify the existing rule manager before accepting a review's missing-UI claim."""
from pathlib import Path
from tempfile import TemporaryDirectory
import ctypes,json,os,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))

def descendants(widget):
    for child in widget.winfo_children():yield child;yield from descendants(child)
def button(widget,text):return next(c for c in descendants(widget) if c.winfo_class()=='Button' and c.cget('text')==text)

with TemporaryDirectory(prefix='.test_round9_review_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from finance.storage import Store
    from finance.m1a_demo import seed_six
    from PIL import ImageGrab
    store=Store(Path(temp)/'demo.sqlite3');seed_six(store)
    with store.connect() as db:
        db.execute("INSERT INTO merchant_rules(merchant,kind,category) VALUES ('合成旧收入规则','收入','餐饮')")
        db.execute("INSERT INTO merchant_rules(merchant,kind,category) VALUES ('合成有效规则','支出','餐饮')")
    (Path(temp)/'preferences.json').write_text(json.dumps(dict(space='demo',month='2026-09')))
    from desktop import Desktop
    app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(str(args));app.update();app.toggle_animation();store=app.store
    before=Path(store.path).read_bytes();transactions=store.source_transactions().to_json()
    app.show('settings');button(app.main,'管理分类记忆').invoke();app.update()
    window=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
    tree=next(c for c in descendants(window) if c.winfo_class()=='Treeview')
    item=next(i for i in tree.get_children() if tree.item(i,'values')==('合成旧收入规则','收入','餐饮'))
    assert Path(store.path).read_bytes()==before
    out=ROOT/'docs/screenshots/round9';out.mkdir(parents=True,exist_ok=True)
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'existing_invalid_rule_visible.png')
    tree.selection_set(item);button(window,'删除选中的记忆').invoke();app.update()
    rules=store.merchant_rules()
    assert rules.merchant.tolist()==['合成有效规则'] and store.source_transactions().to_json()==transactions
    report=dict(data='synthetic only',existing_settings_entry=True,invalid_income_rule_visible=True,
                viewing_rules_did_not_write=True,manual_delete_removed_only_selected_rule=True,
                transactions_unchanged=True,callback_errors=errors)
    (ROOT/'docs/round9_review_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    window.destroy();app.destroy();assert not errors,errors
print('ROUND9_EXISTING_RULE_MANAGER_REVIEW_OK')
