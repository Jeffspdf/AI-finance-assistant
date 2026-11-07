"""Bounded regression checks on isolated synthetic ledgers; no remote API or user data."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import sys
import tkinter as tk

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def button(widget,text):
    return next(c for c in descendants(widget) if c.winfo_class()=='Button' and c.cget('text')==text)


def verify(case):
    from scripts.m2_ui_fixture import seed_ui_refunds
    with TemporaryDirectory(prefix='.test_stability_gui_',dir=ROOT) as temp:
        folder=Path(temp);os.environ['FINANCE_DESKTOP_DATA_DIR']=temp
        seed_ui_refunds(folder/'demo.sqlite3')
        (folder/'preferences.json').write_text(json.dumps({'space':'demo','month':'2026-09'}))
        from desktop import Desktop
        app=Desktop();app.withdraw();errors=[]
        app.report_callback_exception=lambda *args:errors.append(str(args))
        try:
            if case=='edit_atomic':
                app.show('records')
                index=next(i for i,r in enumerate(app.record_rows) if r['external_id']=='P-W')
                app.tree.selection_set(str(index));app.edit_selected()
                window=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
                fields=[w for w in descendants(window) if w.winfo_class()=='TEntry']
                fields[3].delete(0,'end');fields[3].insert(0,'80.00')
                next(w for w in descendants(window) if w.winfo_class()=='Checkbutton').invoke()
                with app.store.connect() as db:
                    db.execute("CREATE TRIGGER stability_reject_rule BEFORE INSERT ON merchant_rules BEGIN SELECT RAISE(ABORT,'synthetic rule failure'); END")
                before=Path(app.store.path).read_bytes()
                button(window,'保存修改').invoke();app.update()
                assert Path(app.store.path).read_bytes()==before,'Edit was committed although remembering the rule failed'
                assert window.winfo_exists(),'Failed edit closed the dialog'
                assert any(w.winfo_class()=='Label' and 'synthetic rule failure' in w.cget('text') for w in descendants(window))
                with app.store.connect() as db:db.execute('DROP TRIGGER stability_reject_rule')
                button(window,'保存修改').invoke();app.update()
                assert not window.winfo_exists()
                row=app.store.source_transactions().query("external_id == 'P-W'").iloc[0]
                assert row.amount_cents==8000
                assert app.store.merchant_rules().query("merchant == '合成餐厅'").iloc[0].category=='餐饮'
            elif case=='import_space':
                with patch('finance.desktop_m1a.filedialog.askopenfilename',return_value='synthetic.csv'):app.select_import()
                window=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
                original=app.store
                app.switch_space();app.update()
                before=(Path(original.path).read_bytes(),Path(app.store.path).read_bytes())
                with patch.object(app,'import_async') as importing:
                    button(window,'开始预览').invoke();app.update()
                    importing.assert_not_called()
                assert window.winfo_exists()
                assert before==(Path(original.path).read_bytes(),Path(app.store.path).read_bytes())
                assert any(w.winfo_class()=='Label' and '空间已切换' in w.cget('text') for w in descendants(window))
                app.switch_space();app.update()
                with patch.object(app,'import_async') as importing:
                    button(window,'开始预览').invoke();app.update()
                    importing.assert_called_once_with('synthetic.csv','auto',None)
            elif case=='budget_chat':
                app.show('assistant');before=Path(app.store.path).read_bytes()
                with patch('finance.conversation.messagebox.askyesno',return_value=True) as consent:
                    app.ask('把工资预算设为100');app.update()
                    consent.assert_not_called()
                assert Path(app.store.path).read_bytes()==before
                assert '支出分类' in app.chat_histories[app.mode][-1]['text']
                with patch('finance.conversation.messagebox.askyesno',return_value=True),patch.object(app.store,'save_budget',side_effect=ValueError('synthetic write failure')):
                    app.ask('把餐饮预算设为100');app.update()
                assert Path(app.store.path).read_bytes()==before
                assert '未保存' in app.chat_histories[app.mode][-1]['text']
            elif case=='budget_suggestions':
                app.show('budget_page')
                with app.store.connect() as db:
                    db.execute("CREATE TRIGGER stability_reject_budget BEFORE INSERT ON budgets WHEN NEW.category='交通' BEGIN SELECT RAISE(ABORT,'synthetic budget failure'); END")
                before=Path(app.store.path).read_bytes()
                with patch('finance.desktop_round2.suggested_budgets',return_value={'餐饮':500,'交通':700}),patch('finance.desktop_round2.messagebox.askyesno',return_value=True),patch('finance.desktop_round2.messagebox.showerror') as failure:
                    app.propose_budgets();app.update()
                    failure.assert_called_once()
                assert Path(app.store.path).read_bytes()==before,'Budget suggestion was partially saved'
            else:raise ValueError('Unknown case')
            assert not errors,errors
            return {'case':case,'passed':True,'callback_errors':errors,'data':'isolated synthetic only'}
        finally:app.destroy()


if __name__=='__main__':print(json.dumps(verify(sys.argv[1]),ensure_ascii=False))
