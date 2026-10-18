from finance.storage import Store
from finance.importer import parse_statement
from finance.demo import demo_bytes
import pytest,json

def test_edit_audits_and_preserves_source(tmp_path):
    store=Store(tmp_path/'db.sqlite');row=parse_statement(demo_bytes(),'sample.csv').rows[0];store.import_rows([row])
    fields={k:row[k] for k in ['date','merchant','description','amount_cents','kind','category']}
    fields.update(amount_cents=12345,merchant='修正商户')
    store.edit_transaction(row['id'],fields)
    after=store.transactions().iloc[0]
    assert after.amount_cents==12345 and after.source_file=='sample.csv' and after.external_id==row['external_id']
    with store.connect() as db:
        old=json.loads(db.execute('SELECT old_values FROM transaction_edits').fetchone()[0]);assert old['amount_cents']==row['amount_cents']
    with pytest.raises(ValueError):store.edit_transaction(row['id'],{**fields,'amount_cents':-1})
    assert store.transactions().iloc[0].amount_cents==12345

def test_goal_update_and_delete(tmp_path):
    store=Store(tmp_path/'db.sqlite')
    goal=dict(name='目标',target_cents=600000,saved_cents=0,start_date='2026-10-01',deadline='2027-04-01',income_cents=500000,budget_cents=380000)
    store.save_goal(goal);gid=int(store.goals().iloc[0].id)
    store.update_goal(gid,{**goal,'saved_cents':10000})
    assert len(store.goals())==1 and store.goals().iloc[0].saved_cents==10000
    store.delete_goal(gid);assert store.goals().empty
