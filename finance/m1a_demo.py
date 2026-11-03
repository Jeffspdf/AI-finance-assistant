"""Six synthetic rows, shared by the interactive example and packaged acceptance."""
from datetime import date
from io import BytesIO
import pandas as pd
from .importer import parse_statement


def seed_six(store):
    if not store.source_transactions().empty:return
    wallet=store.add_account('微信','wallet','示例微信')
    bank=store.add_account('银行','debit','示例银行卡','1234')
    store.add_funding_link(wallet,bank,'2026-01-01')
    content=('交易时间,交易对方,商品,收/支,金额(元),当前状态,交易单号\n'
             '2026-09-01 12:00:00,校园食堂,午餐,支出,20.00,支付成功,DEMO-W20\n'
             '2026-09-02 14:00:00,校园书店,教材,支出,40.00,支付成功,DEMO-W40\n'
             '2026-09-03 10:00:00,校园书店,部分退款,收入,10.00,退款成功,DEMO-R10\n'
             '2026-09-04 09:00:00,自己的账户,转账,不计收支,500.00,成功,DEMO-T500\n')
    bank_frame=pd.DataFrame([
        ['DEMO-B20','2026-09-01 12:00:30','20.00','','财付通','校园食堂微信支付','CNY','消费'],
        ['DEMO-B40','2026-09-02 14:00:30','40.00','','财付通','校园书店微信支付','CNY','消费']],
        columns=['流水号','交易时间','借方发生额','贷方发生额','对方名称','摘要','币种','业务类型'])
    stream=BytesIO();bank_frame.to_excel(stream,index=False,engine='openpyxl')
    store.import_statement(parse_statement(content.encode(),'合成微信.csv',adapter='wechat_csv',account_id=wallet))
    store.import_statement(parse_statement(stream.getvalue(),'合成银行.xlsx',adapter='bank_template_v1',account_id=bank))


def verify_six(directory):
    from .storage import Store
    from .analytics import overview,budget_report,estimate_from_records
    from .qa import answer_question
    from pathlib import Path
    import json
    store=Store(Path(directory)/'m1a-acceptance.sqlite3');seed_six(store)
    def amount():
        f=store.transactions();return int(f.loc[f.kind=='支出','amount_cents'].sum())
    before=amount();assert before==12000
    scan=store.generate_candidates();assert scan['automatic'] is False
    groups=[g for g in store.reconciliation_groups() if g['status']=='candidate' and g['confidence']=='A']
    assert len(groups)==2
    for g in groups:store.decide_group(g['id'],'confirmed')
    after=amount();assert after==6000
    f=store.transactions();raw=store.source_transactions()
    assert overview(raw)[0].iloc[0]['支出']==60
    assert budget_report(raw,'2026-09',{'餐饮':1000,'教育':1000})['已支出'].sum()==60
    answer=answer_question(f,'2026-09 支出多少？');assert '60.00' in answer['answer']
    assert len([e for e in answer['trace']['evidence'] if e['type']=='LinkDecision'])==2
    assert estimate_from_records(raw,date(2026,10,1))['average_expense_cents']==6000
    pending=store.pending_refunds();assert pending==dict(count=1,amount_cents=1000)
    assert len(raw)==6
    for g in groups:store.decide_group(g['id'],'split')
    split=amount();assert split==12000
    report=dict(data='synthetic only',before_cents=before,confirmed_cents=after,split_cents=split,
                pending_refund_cents=1000,transfer_cents=50000,source_rows=6,automatic=False,
                checks=['overview','ledger','budget','question','planning_estimate','link_evidence','split_persistence'])
    (Path(directory)/'m1a-self-test.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
