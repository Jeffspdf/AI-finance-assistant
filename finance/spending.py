"""One read-only signed spending projection; never materialize adjustments in SQL."""
from datetime import datetime,timezone
import pandas as pd
from .refund_rules import ALGORITHM,assess_confirmed_links,refund_inventory

BASES=('cash','original_purchase_month')
EXTRA_COLUMNS=('projection_kind','record_count','gross_amount_cents','confirmed_refund_cents',
               'spending_basis','source_txn_id','original_txn_id','refund_txn_id','refund_link_id','original_date','refund_date')


def spending_view(db,source_frame,basis=None):
    if basis is None:
        setting=db.execute("SELECT value FROM ledger_settings WHERE key='spending_basis'").fetchone()
        if not setting:raise ValueError('缺少净额口径设置，请先复核空间设置')
        basis=setting['value']
    if basis not in BASES:raise ValueError('无效净额口径，只支持cash和original_purchase_month')
    computed_at=datetime.now(timezone.utc).isoformat();assessed=assess_confirmed_links(db)
    inventory=refund_inventory(db,assessed);rows=[];decisions=[];repair=[]
    gross=source_frame[source_frame.included_in_stats].copy()
    for row in gross.to_dict('records'):
        row.update(projection_kind='source',record_count=1,gross_amount_cents=row['amount_cents'] if row['kind']=='支出' else 0,
                   confirmed_refund_cents=0,spending_basis=basis,source_txn_id=row['id'],
                   original_txn_id=row['id'] if row['kind']=='支出' else None,refund_txn_id=None,refund_link_id=None,
                   original_date=row['date'],refund_date=None)
        rows.append(row)
    for item in assessed:
        link=item['link']
        if not item['valid']:
            repair.append(dict(id=link['id'],status='needs_review',stored_status=link['status'],reasons=item['reasons']))
            continue
        purchase=item['purchase'].representative;refund=item['refund'].representative
        row=dict(refund)
        row.update(id='refund-adjustment:'+link['id'],date=refund['date'] if basis=='cash' else purchase['date'],
            merchant=purchase['merchant'],description='已确认退款抵扣 · 原消费：'+purchase['description'],
            amount_cents=-link['amount_cents'],kind='支出',category=purchase['category'],category_source='退款关联 · 跟随原消费',
            status='refund_adjustment',included_in_stats=True,counting_note='退款调整 · 不计新增消费笔数',
            projection_kind='refund_adjustment',record_count=0,gross_amount_cents=0,confirmed_refund_cents=link['amount_cents'],
            spending_basis=basis,source_txn_id=refund['id'],original_txn_id=purchase['id'],refund_txn_id=refund['id'],
            refund_link_id=link['id'],original_date=purchase['date'],refund_date=refund['date'])
        rows.append(row)
        decisions.append(dict(id=link['id'],adjustment_id=row['id'],amount_cents=link['amount_cents'],basis=basis,
            computed_at=computed_at,algorithm_version=ALGORITHM,decision=link,
            purchase=item['purchase'].snapshot(),refund=item['refund'].snapshot()))
    columns=list(dict.fromkeys([*source_frame.columns,*EXTRA_COLUMNS]))
    frame=pd.DataFrame(rows,columns=columns)
    frame['included_in_stats']=frame['included_in_stats'].astype(bool)
    for column in ('amount_cents','record_count','gross_amount_cents','confirmed_refund_cents'):
        frame[column]=frame[column].astype('int64')
    if not frame.empty:frame=frame.sort_values(['date','id'],kind='stable').reset_index(drop=True)
    frame.attrs=dict(gross.attrs,projection='spending_view',spending_basis=basis,computed_at=computed_at,
                     algorithm_version=ALGORITHM,refund_decisions=decisions,repair_pending=repair,
                     source_months=sorted(set(source_frame.date.str[:7])),refund_sources=inventory,
                     excluded_sources=source_frame.loc[~source_frame.included_in_stats,
                         ['id','date','merchant','amount_cents','kind','status','source_file','source_row','counting_note']].to_dict('records'),
                     refund_pending=dict(count=sum(r['association_status']!='linked' for r in inventory),
                       amount_cents=sum(r['amount_cents'] for r in inventory if r['association_status']!='linked')))
    return frame
