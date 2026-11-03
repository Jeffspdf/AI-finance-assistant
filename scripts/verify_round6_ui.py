"""Real Tk workflows: type/category guards, two confirmations, actionable C evidence."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import ctypes,json,os,sys,time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def children(widget):
    for child in widget.winfo_children():yield child;yield from children(child)


def button(w,text):return next(c for c in children(w) if c.winfo_class()=='Button' and c.cget('text')==text)
def labels(w):return '\n'.join(str(c.cget('text')) for c in children(w) if c.winfo_class()=='Label')
def tops(app):return [c for c in app.winfo_children() if c.winfo_class()=='Toplevel']
def tree(w):return next(c for c in children(w) if c.winfo_class()=='Treeview')
def wait(app,predicate):
    end=time.monotonic()+10
    while time.monotonic()<end:
        app.update();time.sleep(.01)
        if predicate():return
    raise AssertionError('UI wait timed out')
def ready(app,w):wait(app,lambda:'发现' in labels(w) and str(button(w,'生成 / 刷新候选').cget('state'))=='normal')
def choose_amount(app,w,amount):
    t=tree(w);item=next(i for i in t.get_children() if t.item(i,'values')[2]==amount)
    t.selection_set(item);t.event_generate('<<TreeviewSelect>>');app.update();return item


with TemporaryDirectory(prefix='.test_round6_ui_',dir=ROOT) as temp:
    os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
    from finance.storage import Store
    from finance.importer import parse_statement
    from finance.m1a_demo import seed_six
    from finance.ui_scale import px
    from PIL import ImageGrab
    store=Store(Path(temp)/'demo.sqlite3');seed_six(store)
    (Path(temp)/'preferences.json').write_text(json.dumps(dict(space='demo',month='2026-09')))
    from desktop import Desktop
    app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(str(args));app.update();app.toggle_animation()
    store=app.store;out=ROOT/'docs/screenshots/round6';out.mkdir(parents=True,exist_ok=True)
    def expense():
        f=store.transactions();return int(f.loc[f.kind=='支出','amount_cents'].sum())
    identifier=store.add_manual_transaction('2026-09-06','编辑样例',1234,'支出','餐饮')
    app.show('records');app.update()
    item=next(i for i,r in enumerate(app.record_rows) if r['id']==identifier);app.tree.selection_set(str(item));app.update()
    assert '转账' not in app.category.cget('values')
    app.edit_selected();app.update();window=tops(app)[0]
    combos=[c for c in children(window) if c.winfo_class()=='TCombobox'];kind,category=combos
    assert '转账' not in category.cget('values')
    kind.set('转账');kind.event_generate('<<ComboboxSelected>>');app.update()
    assert tuple(category.cget('values'))==('转账',) and category.get()=='转账'
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'type_category_linked.png')
    button(window,'保存修改').invoke();app.update()
    edited=store.source_transactions().set_index('id').loc[identifier]
    assert edited.kind==edited.category=='转账' and expense()==12000
    # Two bank sources require two separate positive choices.
    bank_a=store.add_account('银行','debit','合成银行A');bank_b=store.add_account('银行','debit','合成银行B')
    content='交易ID,日期,商户,描述,金额,类型,平台\nR6-A,2026-09-10 12:00:00,同店,同笔消费,17,支出,银行\nR6-B,2026-09-10 12:00:30,同店,同笔消费,17,支出,银行\n'.encode()
    rows=parse_statement(content,'banks.csv').rows;rows[0]['account_id']=bank_a;rows[1]['account_id']=bank_b;store.import_rows(rows)
    app.reconciliation_window();app.update();window=tops(app)[0];ready(app,window);choose_amount(app,window,'17.00')
    assert '均非钱包' in labels(window);before=expense()
    with patch('finance.desktop_m1a.messagebox.askyesno',side_effect=[True,False]) as ask:button(window,'确认同笔消费').invoke()
    app.update();assert ask.call_count==2 and expense()==before
    with patch('finance.desktop_m1a.messagebox.askyesno',side_effect=[True,True]) as ask:button(window,'确认同笔消费').invoke()
    app.update();assert ask.call_count==2 and expense()==before-1700
    window.destroy();app.update()
    # C-level account binding gap must name the missing declaration and provide actions.
    wallet=store.add_account('支付宝','wallet','合成支付宝');bank=store.add_account('银行','debit','合成支付卡')
    content='交易ID,日期,商户,描述,金额,类型,平台\nR6-W,2026-09-11 12:00:00,校园食堂,午餐,23,支出,支付宝\nR6-C,2026-09-11 12:00:30,结算平台,ALIPAY商户付款,23,支出,银行\n'.encode()
    rows=parse_statement(content,'alipay.csv').rows;rows[0]['account_id']=wallet;rows[1]['account_id']=bank;store.import_rows(rows)
    app.reconciliation_window();app.update();window=tops(app)[0];ready(app,window);item=choose_amount(app,window,'23.00')
    assert tree(window).item(item,'values')[1]=='C' and '没有有效支付绑定' in labels(window)
    assert '合成支付宝' in labels(window) and '合成支付卡' in labels(window)
    assert str(button(window,'确认同笔消费').cget('state'))=='disabled'
    window.form_canvas.yview_moveto(1);app.update()
    ImageGrab.grab(window=ctypes.windll.user32.GetParent(window.winfo_id())).save(out/'C_evidence_gap_guidance.png')
    button(window,'去账户与支付绑定').invoke();app.update();account_window=next(w for w in tops(app) if w is not window)
    assert account_window.title()=='账户与支付绑定';account_window.destroy();app.update()
    store.add_funding_link(wallet,bank,'2026-01-01')
    button(window,'重新生成候选').invoke();ready(app,window);item=choose_amount(app,window,'23.00')
    assert tree(window).item(item,'values')[1]=='A'
    before=expense()
    with patch('finance.desktop_m1a.messagebox.askyesno',return_value=True) as ask:button(window,'确认同笔消费').invoke()
    app.update();assert ask.call_count==1 and expense()==before-2300
    report=dict(data='synthetic only',transfer_category_linked=True,no_wallet_second_confirmation=True,
                cancelled_second_confirmation_preserved_count=True,C_missing_binding_named=True,C_binding_action=True,
                rescan_upgraded_C_to_A=True,callback_errors=errors)
    (ROOT/'docs/round6_ui_validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    window.destroy();app.destroy();assert not errors,errors
print('ROUND6_TYPE_GUARD_DOUBLE_CONFIRM_AND_C_UPGRADE_UI_OK')
