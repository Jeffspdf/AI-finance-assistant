"""Deterministic synthetic imports for M2 desktop/Web acceptance; no model or private data."""
from io import BytesIO
from pathlib import Path
import pandas as pd
from finance.storage import Store
from finance.importer import parse_statement


def seed_ui_refunds(path):
    store=Store(Path(path))
    wallet=store.add_account('微信','wallet','合成微信');bank=store.add_account('银行','debit','合成银行卡','1234')
    store.add_funding_link(wallet,bank,'2026-01-01')
    content=('交易时间,交易对方,商品,收/支,金额(元),当前状态,交易单号\n'
             '2026-09-01 12:00:00,合成餐厅,午餐,支出,100.00,支付成功,P-W\n'
             '2026-10-01 09:00:00,合成餐厅,部分退款,收入,30.00,退款成功,R-W\n')
    frame=pd.DataFrame([
        ['P-B','2026-09-01 12:00:30','100.00','','财付通','合成餐厅微信支付','CNY','消费'],
        ['R-B','2026-10-01 09:00:30','','30.00','财付通','合成餐厅退款','CNY','退款']],
        columns=['流水号','交易时间','借方发生额','贷方发生额','对方名称','摘要','币种','业务类型'])
    stream=BytesIO();frame.to_excel(stream,index=False,engine='openpyxl')
    store.import_statement(parse_statement(content.encode(),'合成微信.csv',adapter='wechat_csv',account_id=wallet))
    store.import_statement(parse_statement(stream.getvalue(),'合成银行.xlsx',adapter='bank_template_v1',account_id=bank))
    store.generate_candidates()
    group=next(g for g in store.reconciliation_groups() if g['kind']=='purchase_duplicate' and g['status']=='candidate')
    store.decide_group(group['id'],'confirmed')
    store.save_budget('2026-10','餐饮',10000)
    ids={r['external_id']:r['id'] for r in store.source_transactions().to_dict('records')}
    return store,ids
