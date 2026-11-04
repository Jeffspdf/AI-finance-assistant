"""Read-only M2 invariants. This module never confirms a link or computes net spend."""
from dataclasses import dataclass
from datetime import datetime
import json
from .reconciliation import ACTIVE,is_wallet,representative,valid_group

ALGORITHM='m2-refund-1'
SNAPSHOT_FIELDS=('id','external_id','date','merchant','description','amount_cents','kind','category','channel',
                 'currency','account_id','status','source_document_id','raw_row_id','import_batch_id',
                 'source_file','source_row')


class RefundInvariantError(ValueError):
    pass


@dataclass(frozen=True)
class LogicalEvent:
    kind:str
    key:str
    representative:dict
    members:tuple
    group:dict|None

    @property
    def member_ids(self):return frozenset(r['id'] for r in self.members)

    def snapshot(self):
        return dict(event_key=self.key,representative_id=self.representative['id'],
                    member_ids=sorted(self.member_ids),group=self.group,
                    members=[{k:r.get(k) for k in SNAPSHOT_FIELDS} for r in sorted(self.members,key=lambda r:r['id'])])


def _time(row):
    try:value=datetime.fromisoformat(row['date'])
    except (TypeError,ValueError):raise RefundInvariantError('来源交易时间无效，请先复核原始记录') from None
    if value.tzinfo is not None or not 2000<=value.year<=2100:
        raise RefundInvariantError('来源交易须使用2000至2100年的本地时间')
    return value


def _source(db,identifier,kind):
    row=db.execute('''SELECT t.*,COALESCE(b.started_at,'') imported_at,b.status import_batch_status
        FROM transactions t LEFT JOIN import_batches b ON b.id=t.import_batch_id WHERE t.id=?''',(identifier,)).fetchone()
    if row is None:raise RefundInvariantError('来源交易不存在')
    row=dict(row)
    if row['status']=='deleted' or row['import_batch_status']=='deleted':raise RefundInvariantError('来源或所属导入批次已删除')
    if type(row['amount_cents']) is not int or row['amount_cents']<=0:raise RefundInvariantError('来源金额必须为正整数分')
    if row['currency']!='CNY':raise RefundInvariantError('来源币种不受支持；本版仅支持人民币')
    _time(row)
    if kind=='purchase':
        if row['kind']!='支出' or row['status'] not in ACTIVE or row['category']=='转账':
            raise RefundInvariantError('原消费必须是已明确的普通支出；仅“已退款”状态或类型待复核的记录不能替代退款流水')
    elif kind=='refund':
        if row['kind']!='收入' or row['status']!='refund_pending' or row['category']=='转账':
            raise RefundInvariantError('必须选择独立的待处理退款流水，不能用普通收入、转账或原消费状态抵扣')
    else:raise RefundInvariantError('不支持的逻辑事件类型')
    return row


def resolve_event(db,identifier,kind):
    """Resolve a source leg to a valid confirmed event; ignore the cache column."""
    row=_source(db,identifier,kind)
    groups=[dict(r) for r in db.execute('''SELECT g.* FROM link_groups g JOIN link_members m ON m.group_id=g.id
        WHERE m.transaction_id=? AND g.status='confirmed' ''',(identifier,))]
    if len(groups)>1:raise RefundInvariantError('来源存在多个已确认组，请先修复关联')
    if not groups:return LogicalEvent(kind,kind+':'+identifier,row,(row,),None)
    group=groups[0];expected='purchase_duplicate' if kind=='purchase' else 'refund_duplicate'
    if group['kind']!=expected:raise RefundInvariantError('来源位于不兼容的已确认组，请先复核关联')
    members=[dict(r) for r in db.execute('SELECT transaction_id,role FROM link_members WHERE group_id=? ORDER BY transaction_id',(group['id'],))]
    if len(members)<2:raise RefundInvariantError('已确认组成员不完整，请先修复关联')
    rows=[_source(db,m['transaction_id'],kind) for m in members]
    accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
    if kind=='purchase':
        purchases=[m['transaction_id'] for m in members if m['role']=='purchase']
        if not valid_group(rows,accounts) or len(purchases)!=1 or any(m['role'] not in ('purchase','funding') for m in members):
            raise RefundInvariantError('消费组当前已失效，请先修复并核对')
        chosen=next(r for r in rows if r['id']==purchases[0])
    else:
        if (any(m['role']!='refund' for m in members) or sum(is_wallet(r,accounts) for r in rows)>1 or
            len({(r['currency'],r['amount_cents']) for r in rows})!=1):
            raise RefundInvariantError('退款重复组不符合单钱包、同额同币种和退款来源约束')
        chosen=representative(rows,accounts)
    group_snapshot={k:group[k] for k in ('id','kind','status','algorithm_version','decided_at','decided_by')}
    group_snapshot['roles']={m['transaction_id']:m['role'] for m in members}
    return LogicalEvent(kind,expected+':'+group['id'],chosen,tuple(rows),group_snapshot)


def _context(db,refund_id,purchase_id,amount):
    refund=resolve_event(db,refund_id,'refund');purchase=resolve_event(db,purchase_id,'purchase')
    if type(amount) is not int or amount<=0:raise RefundInvariantError('退款关联金额必须为正整数分')
    if amount!=refund.representative['amount_cents']:
        raise RefundInvariantError('首版必须使用完整退款金额，一笔退款只关联一个原消费；不能自动拆单')
    if refund.representative['currency']!=purchase.representative['currency']:
        raise RefundInvariantError('退款与消费币种不一致')
    if _time(refund.representative)<_time(purchase.representative):
        raise RefundInvariantError('退款时间不能早于原消费')
    return refund,purchase,dict(version=1,refund=refund.snapshot(),purchase=purchase.snapshot())


def _decode_context(value):
    try:result=json.loads(value)
    except (TypeError,ValueError):return None
    return result if isinstance(result,dict) else None


def _snapshot_ids(context,kind):
    event=context.get(kind) if context else None
    values=event.get('member_ids') if isinstance(event,dict) else None
    return {v for v in values if isinstance(v,str)} if isinstance(values,list) else set()


def validate_refund_link(db,refund_txn_id,original_txn_id,amount_cents=None):
    """Validate a whole-refund proposal inside the caller's read or write transaction.

    This does not reserve capacity. A future confirmation must validate and write
    in the same BEGIN IMMEDIATE transaction, rather than reuse a preview result.
    """
    if refund_txn_id==original_txn_id:raise RefundInvariantError('退款和原消费不能是同一来源')
    initial=resolve_event(db,refund_txn_id,'refund')
    amount=initial.representative['amount_cents'] if amount_cents is None else amount_cents
    refund,purchase,context=_context(db,refund_txn_id,original_txn_id,amount)
    used=0;refund_events=set()
    for stored in db.execute("SELECT * FROM refund_links WHERE status='confirmed' ORDER BY id"):
        stored=dict(stored);previous=_decode_context(stored['context_json'])
        related=(stored['original_txn_id'] in purchase.member_ids or stored['refund_txn_id'] in refund.member_ids or
                 bool(_snapshot_ids(previous,'purchase')&purchase.member_ids) or bool(_snapshot_ids(previous,'refund')&refund.member_ids))
        if not related:continue
        try:
            existing_refund,existing_purchase,current=_context(db,stored['refund_txn_id'],stored['original_txn_id'],stored['amount_cents'])
        except RefundInvariantError as exc:
            raise RefundInvariantError('相关既有退款决定已失效，请先复核：'+str(exc)) from None
        if previous!=current:
            raise RefundInvariantError('相关消费或退款事件已变化，既有决定需要先复核；不会自动搬移代表')
        if existing_refund.key==refund.key:raise RefundInvariantError('同一逻辑退款事件已有关联，不能由另一来源重复抵扣')
        if existing_purchase.key==purchase.key:
            if existing_refund.key in refund_events:raise RefundInvariantError('既有退款决定存在跨源重复，请先复核')
            refund_events.add(existing_refund.key);used+=stored['amount_cents']
    limit=purchase.representative['amount_cents']
    if used+amount>limit:raise RefundInvariantError('累计退款超过同一消费事件金额，必须进入复核；不能按每条资金腿各享一次上限')
    return dict(refund_txn_id=refund.representative['id'],original_txn_id=purchase.representative['id'],
                refund_event_key=refund.key,purchase_event_key=purchase.key,amount_cents=amount,
                confirmed_before_cents=used,remaining_before_cents=limit-used,remaining_after_cents=limit-used-amount,
                context=context,context_json=json.dumps(context,ensure_ascii=False,sort_keys=True),algorithm_version=ALGORITHM)
