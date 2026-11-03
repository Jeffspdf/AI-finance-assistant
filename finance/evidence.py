"""Small calculation envelopes; snapshots distinguish records from user assumptions."""
from datetime import datetime,timezone


def effective_frame(frame):
    if 'included_in_stats' in frame:
        return frame[frame.included_in_stats].copy()
    return frame.copy()


def calculation(result_type,formula_id,frame,inputs=None,extra=None):
    evidence=[{'type':'Transaction','id':str(r['id']),'snapshot':r} for r in frame.to_dict('records')]
    ids=set(frame.id) if 'id' in frame else set()
    for decision in frame.attrs.get('link_decisions',[]):
        if ids.intersection(decision['transaction_ids']):
            evidence.append({'type':'LinkDecision','id':decision['id'],'snapshot':decision})
    evidence.extend(extra or [])
    return dict(result_type=result_type,formula_id=formula_id,inputs=inputs or {},evidence=evidence,
                computed_at=datetime.now(timezone.utc).isoformat(),algorithm_version='m1a-1')
