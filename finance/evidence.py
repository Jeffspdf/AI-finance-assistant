"""Small calculation envelopes; snapshots distinguish records from user assumptions."""
from datetime import datetime,timezone


def effective_frame(frame):
    if 'included_in_stats' in frame:
        return frame[frame.included_in_stats].copy()
    return frame.copy()


def count_records(frame):
    """Virtual refund adjustments affect amounts, never the source/event count."""
    return int(frame.record_count.sum()) if 'record_count' in frame else len(frame)


def calculation(result_type,formula_id,frame,inputs=None,extra=None):
    evidence=[{'type':'RefundAdjustment' if r.get('projection_kind')=='refund_adjustment' else 'Transaction',
               'id':str(r['id']),'snapshot':r} for r in frame.to_dict('records')]
    ids=set(frame.id) if 'id' in frame else set()
    for decision in frame.attrs.get('link_decisions',[]):
        if ids.intersection(decision['transaction_ids']):
            evidence.append({'type':'LinkDecision','id':decision['id'],'snapshot':decision})
    for decision in frame.attrs.get('refund_decisions',[]):
        if decision['adjustment_id'] in ids:
            evidence.append({'type':'RefundDecision','id':decision['id'],'snapshot':decision})
    evidence.extend(extra or [])
    inputs=dict(inputs or {})
    if frame.attrs.get('projection')=='spending_view':inputs['spending_basis']=frame.attrs['spending_basis']
    return dict(result_type=result_type,formula_id=formula_id,inputs=inputs,evidence=evidence,
                computed_at=datetime.now(timezone.utc).isoformat(),algorithm_version=frame.attrs.get('algorithm_version','m1a-1'))
