"""Isolated frozen-program acceptance for semantics and explicit maintenance."""
from pathlib import Path
import hashlib,json,uuid
from .storage import Store
from .importer import parse_statement
from .m1a_demo import seed_six


def verify_round6(directory):
    folder=Path(directory)/('round6-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    store=Store(folder/'ledger.sqlite3');seed_six(store);store.generate_candidates()
    groups=store.reconciliation_groups()
    for group in groups:store.decide_group(group['id'],'confirmed')
    group=next(g for g in groups if g['members'][0]['amount_cents']==2000)
    row=next(r for r in group['members'] if r['role']=='purchase')
    def expense(s):
        frame=s.transactions();return int(frame.loc[frame.kind=='支出','amount_cents'].sum())
    assert expense(store)==6000
    try:store.correct_categories([(row['id'],'转账')])
    except ValueError:pass
    else:raise AssertionError('Invalid type/category accepted')
    assert expense(store)==6000
    store.correct_categories([(row['id'],'教育')]);assert expense(store)==8000
    other=next(g for g in groups if g['id']!=group['id'])
    bank=next(r for r in other['members'] if r['role']=='funding')
    with store.connect() as db:db.execute("UPDATE transactions SET channel='支付宝' WHERE id=?",(bank['id'],))
    before=hashlib.sha256(Path(store.path).read_bytes()).hexdigest()
    assert expense(store)==12000 and any(g['repair_pending'] for g in store.reconciliation_groups())
    readonly=Store(store.path,read_only=True);assert expense(readonly)==12000
    with readonly.connect() as db:assert db.execute('PRAGMA query_only').fetchone()[0]==1
    assert hashlib.sha256(Path(store.path).read_bytes()).hexdigest()==before
    assert store.repair_invalid_groups(source='round6_explicit')==1
    with store.connect(read_only=True) as db:assert not db.execute('SELECT 1 FROM transactions WHERE link_group_id IS NOT NULL').fetchone()

    no_wallet=Store(folder/'banks.sqlite3')
    a=no_wallet.add_account('银行','debit','合成A');b=no_wallet.add_account('银行','debit','合成B')
    content='交易ID,日期,商户,描述,金额,类型,平台\nA,2026-09-01 12:00:00,同店,消费,17,支出,银行\nB,2026-09-01 12:00:30,同店,消费,17,支出,银行\n'.encode()
    rows=parse_statement(content,'bank.csv').rows;rows[0]['account_id']=a;rows[1]['account_id']=b;no_wallet.import_rows(rows);no_wallet.generate_candidates()
    candidate=no_wallet.reconciliation_groups()[0];assert candidate['confidence']=='B'
    try:no_wallet.decide_group(candidate['id'],'confirmed')
    except ValueError:pass
    else:raise AssertionError('No-wallet group did not require explicit acknowledgement')
    assert expense(no_wallet)==3400
    no_wallet.decide_group(candidate['id'],'confirmed',acknowledge_no_wallet=True);assert expense(no_wallet)==1700

    c=Store(folder/'C.sqlite3');wallet=c.add_account('支付宝','wallet','合成支付宝');bank=c.add_account('银行','debit','合成支付卡')
    content='交易ID,日期,商户,描述,金额,类型,平台\nC1,2026-09-02 12:00:00,校园食堂,午餐,23,支出,支付宝\nC2,2026-09-02 12:00:30,结算平台,ALIPAY商户付款,23,支出,银行\n'.encode()
    rows=parse_statement(content,'alipay.csv').rows;rows[0]['account_id']=wallet;rows[1]['account_id']=bank;c.import_rows(rows);c.generate_candidates()
    candidate=c.reconciliation_groups()[0];assert candidate['confidence']=='C'
    assert 'binding' in {g['code'] for g in c.group_diagnostics(candidate['id'])['gaps']}
    c.add_funding_link(wallet,bank,'2026-01-01');c.generate_candidates();assert c.reconciliation_groups()[0]['confidence']=='A'

    legacy=Store(folder/'legacy.sqlite3');identifier=legacy.add_manual_transaction('2026-09-01','合成店',2000,'支出','餐饮')
    with legacy.connect() as db:db.execute("UPDATE transactions SET category='转账' WHERE id=?",(identifier,))
    upgraded=Store(legacy.path);fixed=upgraded.transactions().iloc[0]
    assert upgraded.semantic_backup and upgraded.semantic_backup.is_file()
    assert fixed.kind=='支出' and fixed.category=='其他支出' and fixed.amount_cents==2000
    report=dict(data='synthetic only',invalid_transfer_category_rejected=True,confirmed_expense_cents=6000,
                valid_category_change_expense_cents=8000,read_queries_unchanged=True,read_only_view=True,
                explicit_repair_cleared_cache=True,no_wallet_ack_required=True,C_binding_gap_named=True,
                C_upgraded_to_A=True,legacy_category_backup_and_normalization=True)
    (Path(directory)/'round6-self-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
