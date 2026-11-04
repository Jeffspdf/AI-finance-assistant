"""Shared net labels, status wording and export metadata for desktop and Web."""
from .analytics import yuan
from .evidence import count_records

BASIS_LABELS={'cash':'现金口径 · 按退款发生月','original_purchase_month':'原消费月口径 · 回溯原消费月'}
BASIS_CHANGE_NOTICE='将重算当前空间所有净额，不改原始账单。预算、洞察、规划和问答将一并使用新口径。'


def basis_label(basis):
    if basis not in BASIS_LABELS:raise ValueError('无效净额口径')
    return BASIS_LABELS[basis]


def spending_totals(frame):
    if frame.attrs.get('projection')!='spending_view':raise ValueError('金额展示必须使用spending_view净额投影')
    gross=int(frame.gross_amount_cents.sum());refund=int(frame.confirmed_refund_cents.sum())
    income=int(frame.loc[frame.kind=='收入','amount_cents'].sum())
    return dict(gross_cents=gross,refund_cents=refund,net_cents=gross-refund,income_cents=income,
                balance_cents=income-gross+refund,record_count=count_records(frame),basis=frame.attrs['spending_basis'])


def refund_status_summary(events,month=None):
    events=[r for r in events if not month or month=='全部月份' or r['date'].startswith(month)]
    result={state:dict(count=0,amount_cents=0) for state in ('linked','pending','needs_review')}
    for event in events:
        entry=result[event['association_status']];entry['count']+=1;entry['amount_cents']+=event['amount_cents']
    return result


def refund_status_text(summary):
    parts=[]
    for state,title in [('pending','退款待处理'),('needs_review','退款待复核'),('linked','退款已关联')]:
        row=summary[state]
        if row['count']:parts.append(f'{title} {row["count"]} 笔 / ¥{yuan(row["amount_cents"])}')
    return '；'.join(parts) or '暂无退款流水'


def annotate_refund_sources(frame,events):
    frame=frame.copy();byid={identifier:event for event in events for identifier in event['member_ids']}
    for index,row in frame.iterrows():
        if row['status']!='refund_pending':continue
        state=byid.get(row['id'],{}).get('association_status','pending')
        frame.at[index,'counting_note']={'linked':'退款已关联 · 不计收入','pending':'退款待处理 · 尚未抵扣',
            'needs_review':'退款待复核 · 尚未抵扣'}[state]
    return frame


def net_export_csv(frame):
    spending_totals(frame)
    exported=frame.drop(columns=['signature'],errors='ignore').copy()
    exported['口径']=basis_label(frame.attrs['spending_basis']);exported['basis']=frame.attrs['spending_basis']
    exported['计算时间']=frame.attrs['computed_at']
    return exported.to_csv(index=False).encode('utf-8-sig')


def answer_export_csv(result):
    import pandas as pd
    basis=result.get('trace',{}).get('inputs',{}).get('spending_basis')
    frame=pd.DataFrame(result.get('evidence',[]));frame['basis']=basis or 'gross/source'
    frame['口径']=basis_label(basis) if basis else '原始来源/消费总额'
    frame['amount_basis']=result.get('trace',{}).get('inputs',{}).get('amount_basis','gross/source')
    return frame.to_csv(index=False).encode('utf-8-sig')
