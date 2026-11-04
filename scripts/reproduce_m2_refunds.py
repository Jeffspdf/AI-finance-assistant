"""Synthetic M2-2 acceptance through both supported import adapters and write APIs."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json,sys
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from finance.storage import Store
from finance.importer import parse_statement
from finance.analytics import month_records


def verify_refunds(directory):
    results=[]
    for name,amounts in [('full',[100]),('partial',[30]),('multiple',[20,30])]:
        store=Store(Path(directory)/name/'ledger.sqlite3')
        wallet=store.add_account('微信','wallet','合成微信');bank=store.add_account('银行','debit','合成银行卡','1234')
        store.add_funding_link(wallet,bank,'2026-01-01')
        content=('交易时间,交易对方,商品,收/支,金额(元),当前状态,交易单号\n'
                 '2026-09-01 12:00:00,合成餐厅,午餐,支出,100.00,支付成功,P-W\n')
        bank_rows=[['P-B','2026-09-01 12:00:30','100.00','','财付通','合成餐厅微信支付','CNY','消费']]
        for i,amount in enumerate(amounts,1):
            content+=f'2026-10-{i:02d} 09:00:00,合成餐厅,独立退款{i},收入,{amount:.2f},退款成功,R-W{i}\n'
            bank_rows.append([f'R-B{i}',f'2026-10-{i:02d} 09:00:30','',f'{amount:.2f}','财付通','合成餐厅退款','CNY','退款'])
        stream=BytesIO()
        pd.DataFrame(bank_rows,columns=['流水号','交易时间','借方发生额','贷方发生额','对方名称','摘要','币种','业务类型']).to_excel(stream,index=False,engine='openpyxl')
        for data,filename,adapter,account in [(content.encode(),'合成微信.csv','wechat_csv',wallet),
                                             (stream.getvalue(),'合成银行.xlsx','bank_template_v1',bank)]:
            parsed=parse_statement(data,filename,adapter=adapter,account_id=account)
            assert not parsed.issues;store.import_statement(parsed)
        ids={r['external_id']:r['id'] for r in store.source_transactions().to_dict('records')}
        store.generate_candidates()
        purchases=[g for g in store.reconciliation_groups() if g['kind']=='purchase_duplicate' and g['status']=='candidate']
        assert len(purchases)==1;store.decide_group(purchases[0]['id'],'confirmed')
        for i in range(1,len(amounts)+1):
            group=store.create_refund_group([ids[f'R-W{i}'],ids[f'R-B{i}']],reason='人工核对合成退款的跨源来源')
            store.decide_group(group,'confirmed')
        for i in range(1,len(amounts)+1):store.confirm_refund_link(ids[f'R-B{i}'],ids['P-B'])
        cash=store.spending_view();original=store.spending_view('original_purchase_month')
        def expense(frame,month=None):
            if month:frame=month_records(frame,month)
            return int(frame.loc[frame.kind=='支出','amount_cents'].sum())
        net=10000-sum(amounts)*100
        assert expense(store.transactions())==10000 and expense(cash)==net and expense(original)==net
        assert expense(cash,'2026-09')==10000 and expense(cash,'2026-10')==-sum(amounts)*100
        assert expense(original,'2026-09')==net and expense(original,'2026-10')==0
        assert store.pending_refunds()==dict(count=0,amount_cents=0)
        sources=store.source_transactions();refunds=sources[sources.kind=='收入']
        assert len(sources)==2+len(amounts)*2 and set(refunds.status)=={'refund_pending'}
        results.append(dict(scenario=name,import_adapters=['wechat_csv','bank_template_v1'],source_rows=len(sources),
            refund_source_rows=len(refunds),confirmed_refund_events=len(amounts),gross_cents=10000,net_cents=net,
            cash_months={'2026-09':expense(cash,'2026-09'),'2026-10':expense(cash,'2026-10')},
            original_months={'2026-09':expense(original,'2026-09'),'2026-10':expense(original,'2026-10')},
            source_refund_statuses=sorted(set(refunds.status)),repair_pending=cash.attrs['repair_pending']))
    return dict(data='synthetic only',execution='Python source, not packaged EXE',scenarios=results)


if __name__=='__main__':
    with TemporaryDirectory(prefix='.test_m2_2_import_',dir=ROOT) as temp:report=verify_refunds(temp)
    destination=ROOT/'docs/evidence/m2-2/business-results.json';destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))
