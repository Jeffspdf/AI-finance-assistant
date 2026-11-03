"""Synthetic executable acceptance for the round-five money-counting boundaries."""
from pathlib import Path
import json
import uuid
from .storage import Store
from .importer import parse_statement
from .demo import initialize_demo


def verify_round5(directory):
    folder=Path(directory)/('round5-'+uuid.uuid4().hex);folder.mkdir(parents=True)
    store=Store(folder/'alipay.sqlite3')
    wallet=store.add_account('支付宝','wallet','合成支付宝');bank=store.add_account('银行','debit','合成银行卡')
    store.add_funding_link(wallet,bank,'2026-01-01')
    content=('交易ID,日期,商户,描述,金额,类型,平台\n'
             'A,2026-09-01 12:00:00,校园食堂,午餐,20,支出,支付宝\n'
             'B,2026-09-01 12:00:30,结算平台,ALIPAY商户付款,20,支出,银行\n').encode()
    rows=parse_statement(content,'synthetic.csv').rows
    rows[0]['account_id']=wallet;rows[1]['account_id']=bank;store.import_rows(rows)
    def expense(s):
        frame=s.transactions();return int(frame.loc[frame.kind=='支出','amount_cents'].sum())
    store.generate_candidates();group=store.reconciliation_groups()[0];assert group['confidence']=='A'
    store.decide_group(group['id'],'confirmed');assert expense(store)==2000
    store.correct_categories([(rows[0]['id'],'转账')]);assert expense(store)==4000
    assert store.reconciliation_groups()[0]['status']=='needs_review'
    with store.connect() as db:assert not db.execute('SELECT 1 FROM transactions WHERE link_group_id IS NOT NULL').fetchone()

    wallets=Store(folder/'wallets.sqlite3')
    rows[0]['category']='餐饮';rows[1]['channel']='微信'
    rows[0]['account_id']=None;rows[1]['account_id']=None;wallets.import_rows(rows)
    with wallets.connect() as db:
        db.execute("INSERT INTO link_groups(id,kind,status,reason,confidence,algorithm_version) VALUES ('legacy','purchase_duplicate','candidate','合成旧版组','B','m1a-1')")
        db.executemany("INSERT INTO link_members VALUES ('legacy',?,?)",[(r['id'],'purchase' if i==0 else 'funding') for i,r in enumerate(rows)])
    try:wallets.decide_group('legacy','confirmed')
    except ValueError as exc:assert '钱包' in str(exc)
    else:raise AssertionError('Two wallets were accepted')
    assert expense(wallets)==4000
    with wallets.connect() as db:db.execute("UPDATE link_groups SET status='confirmed' WHERE id='legacy'")
    assert expense(wallets)==4000 and wallets.reconciliation_groups()[0]['status']=='needs_review'

    source=Store(folder/'source.sqlite3')
    wx=('交易时间,交易对方,收/支,金额(元),当前状态,交易单号\n'
        '2026-09-01 12:00:00,食堂,支出,20,支付成功,SYNTHETIC\n').encode()
    parsed=parse_statement(wx,'wx.csv',adapter='auto');assert parsed.adapter=='wechat_csv'
    source.import_statement(parsed)
    changed=source.import_statement(parse_statement(wx,'renamed.csv',retain_source=True))
    assert changed[0]==0 and len(changed[2])==1 and expense(source)==2000

    weak=Store(folder/'weak.sqlite3');rows=parse_statement(content,'weak.csv').rows;weak.import_rows(rows);weak.generate_candidates()
    group=weak.reconciliation_groups()[0];assert group['confidence']=='C'
    try:weak.decide_group(group['id'],'confirmed')
    except ValueError as exc:assert 'C级' in str(exc)
    else:raise AssertionError('C confirmation was accepted')

    demo=Store(folder/'demo.sqlite3');initialize_demo(demo)
    with demo.connect() as db:db.execute("UPDATE transactions SET status='deleted'")
    assert not initialize_demo(Store(demo.path)) and demo.transactions().empty
    report=dict(data='synthetic only',alipay_confidence='A',confirmed_expense_cents=2000,
                category_changed_expense_cents=4000,group_status='needs_review',cache_cleared=True,
                two_wallets_rejected=True,legacy_invalid_group_repaired=True,auto_detect_wechat=True,
                changed_parser_conflicts=1,C_confirm_rejected=True,demo_reseeded=False)
    (Path(directory)/'round5-self-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
