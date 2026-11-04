"""Exercise real Tk controls on synthetic adapter imports; never opens personal data."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from datetime import date
import argparse,ctypes,json,os,sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.m2_ui_fixture import seed_ui_refunds
from finance.analytics import estimate_from_records
from finance.spending_presenter import BASIS_LABELS


def descendants(widget):
    for child in widget.winfo_children():yield child;yield from descendants(child)


def verify(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='.test_m2_4_ui_',dir=ROOT) as directory:
        folder=Path(directory);os.environ['FINANCE_DESKTOP_DATA_DIR']=str(folder)
        store,ids=seed_ui_refunds(folder/'demo.sqlite3')
        (folder/'preferences.json').write_text(json.dumps(dict(space='demo',month='2026-10')),encoding='utf-8')
        from desktop import Desktop
        from finance.ui_scale import set_window_geometry
        from PIL import ImageGrab
        app=Desktop();errors=[];app.report_callback_exception=lambda *args:errors.append(repr(args))
        app.update();app.toggle_animation();set_window_geometry(app,1100,720);app.update()
        result=dict(data='synthetic only',execution='real Tk widgets, Python source; not packaged EXE',reference_date='2026-11-04',checks=[],layout_checks=[])
        def screen(name,widget):
            app.update();handle=ctypes.windll.user32.GetParent(widget.winfo_id()) or widget.winfo_id()
            ImageGrab.grab(window=handle).save(output/(name+'.png'))
        def choose(tree,index=0):tree.selection_set(str(index));tree.event_generate('<<TreeviewSelect>>');app.update()
        def layout(name,widget):
            app.update();spill=[]
            for child in descendants(widget):
                if not child.winfo_ismapped() or child.winfo_class() not in ('Button','TEntry','TCombobox','Frame','Label','Treeview'):continue
                left=child.winfo_rootx()-widget.winfo_rootx()
                if left < -3 or left+child.winfo_width()>widget.winfo_width()+3:spill.append([child.winfo_class(),left,child.winfo_width()])
            result['layout_checks'].append(dict(name=name,size=[widget.winfo_width(),widget.winfo_height()],horizontal_spill=spill))
            assert not spill,(name,spill)
        def yes(widget):
            with patch('finance.desktop_refunds.messagebox.askyesno',return_value=True):widget.invoke();app.update()
            assert not errors,errors
        def pick(combo,choices,identifier):combo.set(next(label for label,value in choices.items() if value==identifier));combo.event_generate('<<ComboboxSelected>>');app.update()
        window=app.refund_window();app.update();controls=window.controls;state=window.refund_state
        before=Path(store.path).read_bytes();controls['refresh'].invoke();app.update();assert Path(store.path).read_bytes()==before
        controls['notebook'].select(2);app.update()
        pick(controls['refund_left'],state['refund_choices'],ids['R-W']);pick(controls['refund_right'],state['refund_choices'],ids['R-B'])
        controls['propose_group'].invoke();app.update();assert len(state['groups'])==1
        controls['acknowledge'].set(True);yes(controls['confirm_group'])
        assert state['groups'][0]['status']=='confirmed' and len(state['events'])==1
        controls['notebook'].select(0);app.update();choose(state['event_tree'])
        pick(controls['purchase'],state['purchase_choices'],ids['P-B']);yes(controls['confirm'])
        assert state['events'][0]['association_status']=='linked' and str(controls['confirm']['state'])=='disabled'
        assert '待处理' not in controls['summary'].cget('text') and state['totals']['net_cents']==7000
        result['checks'].append('source/group proposal -> explicit group confirmation -> whole refund confirmation; B button disabled')
        for tab in range(3):controls['notebook'].select(tab);layout('refund_tab_'+str(tab),window)
        controls['notebook'].select(0);app.update()
        screen('refund_linked',window)
        app.selected_month='2026-10';app.show('home');app.update();assert app.home_totals['net_cents']==-3000
        screen('cash_negative_home',app)
        app.show('records');app.update();assert app.record_totals['net_cents']==-3000
        assert all('已关联' in r['counting_note'] for r in app.record_rows if r['status']=='refund_pending')
        app.show('budget_page');app.update();assert next(r for r in app.budget_rows if r['分类']=='餐饮')['剩余']==130
        app.show('assistant');app.ask('2026-10 支出多少？');app.update()
        assert '-30.00' in app.answer.cget('text') and '现金口径' in app.answer.cget('text') and '退款待处理' not in app.answer.cget('text')
        app.show('plan');app.update()
        with patch('finance.desktop_review.estimate_from_records',side_effect=lambda f:estimate_from_records(f,date(2026,11,4))):app.estimate_plan()
        assert app.plan_estimate['average_expense_cents']==3500 and app.inputs['方案 A 月预算 / 元'].get()=='35.00'
        result['cash']=dict(home=-3000,ledger=-3000,budget_used=-3000,budget_remaining=13000,qa=-3000,plan_average=3500)
        before=Path(store.path).read_bytes();controls['basis'].set(BASIS_LABELS['original_purchase_month'])
        with patch('finance.desktop_refunds.messagebox.askyesno',return_value=False):controls['apply_basis'].invoke();app.update()
        assert app.store.spending_basis()=='cash' and Path(store.path).read_bytes()==before
        controls['basis'].set(BASIS_LABELS['original_purchase_month']);yes(controls['apply_basis'])
        app.selected_month='2026-10';app.show('home');app.update();assert app.home_totals['net_cents']==0
        app.show('records');app.update();assert app.record_totals['net_cents']==0
        app.show('budget_page');app.update();assert next(r for r in app.budget_rows if r['分类']=='餐饮')['已支出']==0
        app.show('assistant');app.ask('2026-10 支出多少？');app.update();assert '0.00' in app.answer.cget('text') and '原消费月' in app.answer.cget('text')
        app.selected_month='2026-09';app.show('home');app.update();assert app.home_totals['net_cents']==7000
        app.show('plan');app.update()
        with patch('finance.desktop_review.estimate_from_records',side_effect=lambda f:estimate_from_records(f,date(2026,11,4))):app.estimate_plan()
        assert app.plan_estimate['average_expense_cents']==7000 and app.inputs['方案 A 月预算 / 元'].get()=='70.00'
        exported=output/'net-original.csv'
        with patch('finance.desktop_refunds.filedialog.asksaveasfilename',return_value=str(exported)):app.export_spending()
        import pandas as pd
        csv=pd.read_csv(exported);assert set(csv.basis)=={'original_purchase_month'} and int(csv.amount_cents.sum())==7000
        result['original']=dict(october_home=0,october_ledger=0,october_budget=0,october_qa=0,september_home=7000,plan_average=7000)
        result['checks'].append('same source: cash/original month amounts agree across overview/ledger/budget/QA/plan; basis cancel and audited confirm')
        # Valid links can only be changed through the revoke path.
        window.refresh_refunds();choose(state['link_tree']);yes(controls['revoke'])
        assert state['events'][0]['association_status']=='pending' and state['totals']['net_cents']==10000
        choose(state['event_tree']);pick(controls['purchase'],state['purchase_choices'],ids['P-W']);yes(controls['confirm'])
        assert state['events'][0]['association_status']=='linked'
        # An external defect arises AFTER startup, so opening/refeshing must remain read-only.
        with app.store.connect() as db:db.execute('UPDATE transactions SET amount_cents=2000 WHERE id=?',(ids['R-B'],))
        before=Path(store.path).read_bytes();window.refresh_refunds();app.update();assert Path(store.path).read_bytes()==before
        assert {r['kind'] for r in state['work']}=={'link','group'}
        controls['notebook'].select(1);choose(state['work_tree'],next(i for i,r in enumerate(state['work']) if r['kind']=='link'))
        controls['inspect_work'].invoke();app.update();assert Path(store.path).read_bytes()==before
        evidence=next(w for w in app.winfo_children() if isinstance(w, __import__('tkinter').Toplevel) and w.title()=='退款来源与证据');evidence.destroy()
        layout('actionable_review',window);screen('actionable_review',window);yes(controls['repair_work'])
        choose(state['work_tree'],next(i for i,r in enumerate(state['work']) if r['kind']=='group'));yes(controls['repair_work'])
        assert app.store.refund_links()[0]['stored_status']=='needs_review'
        # Use the real existing source editor to repair the bank leg.
        assert app.locate_transaction(ids['R-B'],expected_path=store.path);app.edit_selected();app.update()
        editor=next(w for w in app.winfo_children() if isinstance(w,__import__('tkinter').Toplevel) and w.title()=='编辑交易')
        amount=next(w for w in descendants(editor) if w.winfo_class()=='TEntry' and w.get()=='20.00')
        amount.delete(0,'end');amount.insert(0,'30.00')
        next(w for w in descendants(editor) if w.winfo_class()=='Button' and w.cget('text')=='保存修改').invoke();app.update()
        assert not editor.winfo_exists()
        window.refresh_refunds();controls['notebook'].select(2);app.update();choose(state['group_tree'])
        yes(controls['reopen_group']);yes(controls['confirm_group'])
        controls['notebook'].select(0);app.update();choose(state['event_tree']);yes(controls['confirm'])
        assert state['totals']['net_cents']==7000 and state['events'][0]['association_status']=='linked'
        # The blocker revoke exit is tested separately from targeted repair.
        with app.store.connect() as db:db.execute("UPDATE transactions SET date='2026-11-01' WHERE id=?",(ids['R-W'],))
        window.refresh_refunds();controls['notebook'].select(1);app.update();choose(state['work_tree'])
        yes(controls['revoke_work']);assert app.store.refund_links()[0]['stored_status']=='rejected'
        result['checks'].append('relation/group blockers -> inspect -> targeted repair -> actual source edit -> group reopen -> explicit reconfirm; blocker revoke exit')
        # Negative history leaves future budgets empty until the user explicitly enters them.
        controls['basis'].set(BASIS_LABELS['cash']);yes(controls['apply_basis'])
        with app.store.connect() as db:
            db.execute("UPDATE transactions SET date='2026-05-01' WHERE external_id IN ('P-W','P-B')")
            db.execute("UPDATE transactions SET date='2026-10-01' WHERE external_id IN ('R-W','R-B')")
        app.store.repair_refund_links();app.store.confirm_refund_link(ids['R-W'],ids['P-W'])
        app.show('plan');app.update()
        with patch('finance.desktop_review.estimate_from_records',side_effect=lambda f:estimate_from_records(f,date(2026,11,4))):app.estimate_plan()
        assert app.plan_estimate['average_expense_cents']==-3000 and app.inputs['方案 A 月预算 / 元'].get()=='' and app.calculate() is False
        app.inputs['方案 A 月预算 / 元'].insert(0,'100');app.inputs['方案 B 月预算 / 元'].insert(0,'90');assert app.calculate()
        result['checks'].append('negative historical average -> no automatic future budget; explicit nonnegative inputs required')
        assert not errors,errors;result['callback_errors']=errors
        result['ui_scale']=app.ui_scale
        with app.store.connect(read_only=True) as db:result['basis_switch_audits']=db.execute("SELECT COUNT(*) FROM audit_events WHERE event='spending_basis_changed'").fetchone()[0]
        app.destroy()
    (output/'gui-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default=str(ROOT/'docs/evidence/m2-4/gui'))
    args=parser.parse_args();print(json.dumps(verify(args.output),ensure_ascii=False))
