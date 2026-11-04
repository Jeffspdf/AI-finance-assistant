"""Audited, serialized refund decisions. Source transactions remain untouched."""
import json
import uuid
from .reconciliation import audit,is_wallet
from .refund_rules import (ALGORITHM,RefundInvariantError,_source,_decode_context,_snapshot_ids,
                           assess_confirmed_links,refund_inventory,validate_refund_link,resolve_event,stored_review_reasons)


def _blocker(item):
    link=item['link']
    return dict(id=link['id'],status='needs_review',reasons=item['reasons'],
        refund_txn_id=link['refund_txn_id'],original_txn_id=link['original_txn_id'],
        related_link_ids=item['related_link_ids'],actions=['inspect_sources','repair_refund_links','revoke_refund_link'])


class RefundReviewRequired(RefundInvariantError):
    status='needs_review'

    def __init__(self,items):
        self.link_ids=tuple(item['link']['id'] for item in items)
        self.blockers=[_blocker(item) for item in items]
        super().__init__('已有退款决定需要先复核，未写入新确认：'+ '；'.join(
            item['link']['id']+'：'+'，'.join(item['reasons']) for item in items))


def _reason(value):
    if not isinstance(value,str) or not 1<=len(value.strip())<=500:raise ValueError('请填写1至500字的核对说明')
    return value.strip()


def invalidate_refund_links(db,source_ids,reason,*,group_ids=()):
    """Called BEFORE a source/group mutation, using old facts and decision snapshots."""
    touched=set(source_ids);links=[dict(r) for r in db.execute("SELECT * FROM refund_links WHERE status IN ('candidate','confirmed')")]
    group_ids=set(group_ids)
    for link in links:
        context=_decode_context(link['context_json'])
        for kind in ('purchase','refund'):
            event=context.get(kind) if isinstance(context,dict) else None
            group=event.get('group') if isinstance(event,dict) else None
            if isinstance(group,dict) and isinstance(group.get('id'),str) and group['id'] in group_ids:
                touched.update({link['refund_txn_id'],link['original_txn_id']}|_snapshot_ids(context,'purchase')|_snapshot_ids(context,'refund'))
    groups={}
    for row in db.execute("SELECT m.group_id,m.transaction_id FROM link_members m JOIN link_groups g ON g.id=m.group_id WHERE g.status='confirmed'"):
        groups.setdefault(row['group_id'],set()).add(row['transaction_id'])
    selected={};changed=True
    while changed:
        changed=False
        for members in groups.values():
            if touched&members and not members<=touched:touched.update(members);changed=True
        for link in links:
            context=_decode_context(link['context_json'])
            ids={link['refund_txn_id'],link['original_txn_id']}|_snapshot_ids(context,'refund')|_snapshot_ids(context,'purchase')
            if touched&ids and link['id'] not in selected:
                selected[link['id']]=link;touched.update(ids);changed=True
    for identifier,old in selected.items():
        db.execute("UPDATE refund_links SET status='needs_review',decided_at=CURRENT_TIMESTAMP,decided_by='system' WHERE id=?",(identifier,))
        audit(db,'refund_invalidated',identifier,dict(reason=reason,old=old,new_status='needs_review',algorithm_version=ALGORITHM))
    return len(selected)


def _refund_members(db,identifiers):
    identifiers=list(identifiers)
    if len(identifiers)<2 or len(set(identifiers))!=len(identifiers):raise ValueError('退款重复组至少选择两条不同来源')
    rows=[_source(db,identifier,'refund') for identifier in identifiers]
    accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
    if sum(is_wallet(r,accounts) for r in rows)>1:raise ValueError('同一退款组最多一个钱包，两个钱包不能确认为同一退款')
    if len({(r['currency'],r['amount_cents']) for r in rows})!=1:raise ValueError('退款重复组必须同额同币种')
    return rows,accounts


class Refunds:
    def _begin_refund_write(self,db):
        if self.read_only:raise ValueError('只读账本不能修改退款决定')
        db.execute('BEGIN IMMEDIATE')

    def refund_sources(self):
        with self.connect(read_only=True) as db:
            db.execute('BEGIN');return refund_inventory(db)

    def pending_refunds(self,month=None):
        events=self.refund_sources()
        return self._pending_refund_summary(events,month)

    @staticmethod
    def _pending_refund_summary(events,month=None):
        pending=[r for r in events if r['association_status']!='linked' and
                 (not month or month=='全部月份' or r['date'].startswith(month))]
        return dict(count=len(pending),amount_cents=sum(r['amount_cents'] for r in pending))

    def refund_links(self):
        with self.connect(read_only=True) as db:
            db.execute('BEGIN');assessed={r['link']['id']:r for r in assess_confirmed_links(db)}
            result=[]
            for row in db.execute('SELECT * FROM refund_links ORDER BY created_at,id'):
                row=dict(row);item=assessed.get(row['id']);row['stored_status']=row['status']
                row['repair_pending']=bool(item and not item['valid'])
                row['review_reasons']=item['reasons'] if item else stored_review_reasons(db,row['id']) if row['status']=='needs_review' else []
                if row['repair_pending']:row['status']='needs_review'
                result.append(row)
            return result

    def refund_confirmation_blockers(self):
        with self.connect(read_only=True) as db:
            db.execute('BEGIN');return [_blocker(item) for item in assess_confirmed_links(db) if not item['valid']]

    def repair_refund_links(self,*,identifier=None,source='manual',db=None):
        """Persist review state only; a selected link repairs its whole affected component."""
        if self.read_only:raise ValueError('只读账本不能修复退款决定')
        if db is None:
            with self.connect() as connection:
                self._begin_refund_write(connection)
                return self.repair_refund_links(identifier=identifier,source=source,db=connection)
        if not db.in_transaction:self._begin_refund_write(db)
        if identifier is not None and not db.execute('SELECT 1 FROM refund_links WHERE id=?',(identifier,)).fetchone():
            raise ValueError('退款关联不存在')
        invalid=[item for item in assess_confirmed_links(db) if not item['valid']]
        selected=None
        if identifier is not None:
            item=next((item for item in invalid if item['link']['id']==identifier),None)
            selected=set(item['related_link_ids']) if item else set()
        repaired=0
        for item in invalid:
            old=item['link']
            if selected is not None and old['id'] not in selected:continue
            db.execute("UPDATE refund_links SET status='needs_review',decided_at=CURRENT_TIMESTAMP,decided_by='system' WHERE id=? AND status='confirmed'",(old['id'],))
            audit(db,'refund_repaired',old['id'],dict(old=old,new_status='needs_review',reasons=item['reasons'],
                  related_link_ids=item['related_link_ids'],trigger=source,algorithm_version=ALGORITHM))
            repaired+=1
        return repaired

    def maintain_links(self,*,source='manual'):
        """Writable startup/restore maintenance; group and refund changes commit together."""
        with self.connect() as db:
            self._begin_refund_write(db)
            groups=self.repair_invalid_groups(db=db,source=source)
            refunds=self.repair_refund_links(db=db,source=source)
            seen={};duplicates=0
            for group in db.execute("SELECT id FROM link_groups WHERE kind='refund_duplicate' AND status='candidate' ORDER BY created_at,id").fetchall():
                members=frozenset(r[0] for r in db.execute('SELECT transaction_id FROM link_members WHERE group_id=?',(group['id'],)))
                if len(members)<2:continue
                if members not in seen:seen[members]=group['id'];continue
                db.execute("UPDATE link_groups SET status='stale',decided_at=CURRENT_TIMESTAMP,decided_by='system' WHERE id=?",(group['id'],))
                audit(db,'refund_candidate_deduplicated',group['id'],dict(kept_group_id=seen[members],source=source,member_ids=sorted(members)))
                duplicates+=1
            return dict(groups=groups,refunds=refunds,deduplicated_candidates=duplicates)

    def refund_link_evidence(self,identifier):
        with self.connect(read_only=True) as db:
            db.execute('BEGIN');link=db.execute('SELECT * FROM refund_links WHERE id=?',(identifier,)).fetchone()
            if not link:raise ValueError('退款关联不存在')
            link=dict(link);saved=_decode_context(link['context_json'])
            item=next((item for item in assess_confirmed_links(db) if item['link']['id']==identifier),None)
            source_ids={link['refund_txn_id'],link['original_txn_id']}|_snapshot_ids(saved,'refund')|_snapshot_ids(saved,'purchase')
            if item:
                for kind in ('purchase','refund'):
                    if item[kind] is not None:source_ids.update(item[kind].member_ids)
            sources=[]
            for source_id in sorted(source_ids):
                row=db.execute('''SELECT t.*,d.sha256,d.parser_version,r.raw_json,r.parse_status
                    FROM transactions t LEFT JOIN source_documents d ON d.id=t.source_document_id
                    LEFT JOIN raw_rows r ON r.id=t.raw_row_id WHERE t.id=?''',(source_id,)).fetchone()
                sources.append(dict(row) if row else dict(id=source_id,missing=True))
            entities={identifier}|source_ids
            for row in sources:
                entities.update(row[key] for key in ('import_batch_id','source_document_id','link_group_id') if row.get(key))
            for context in (saved,item['context'] if item else None):
                for kind in ('purchase','refund'):
                    event=context.get(kind) if isinstance(context,dict) else None
                    group=event.get('group') if isinstance(event,dict) else None
                    if isinstance(group,dict) and isinstance(group.get('id'),str):entities.add(group['id'])
            marks=','.join('?' for _ in entities)
            history=[dict(r) for r in db.execute(f'SELECT * FROM audit_events WHERE entity_id IN ({marks}) ORDER BY id',sorted(entities))]
            edits=[]
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='transaction_edits' AND type='table'").fetchone():
                marks=','.join('?' for _ in source_ids)
                edits=[dict(r) for r in db.execute(f'SELECT * FROM transaction_edits WHERE transaction_id IN ({marks}) ORDER BY id',sorted(source_ids))]
            reasons=item['reasons'] if item else stored_review_reasons(db,identifier) if link['status']=='needs_review' else []
            return dict(link=link,saved_context=saved,current_context=item['context'] if item else None,
                valid=bool(item and item['valid']),repair_pending=bool(item and not item['valid']),reasons=reasons,
                effective_status='needs_review' if item and not item['valid'] else link['status'],
                sources=sources,audit=history,edits=edits)

    def confirm_refund_link(self,refund_txn_id,original_txn_id,amount_cents=None,*,
                            reason='用户核对独立退款与原消费来源后确认',confidence='B'):
        reason=_reason(reason)
        if confidence not in ('A','B'):raise ValueError('确认退款关联需明确核对来源，置信度须为A或B')
        with self.connect() as db:
            self._begin_refund_write(db)
            assessed=assess_confirmed_links(db)
            invalid=[item for item in assessed if not item['valid']]
            if invalid:raise RefundReviewRequired(invalid)
            event=resolve_event(db,refund_txn_id,'refund')
            if any(item['refund'].key==event.key for item in assessed):
                raise RefundInvariantError('同一逻辑退款事件已有关联；有效的已确认关联必须先撤销，再重新确认')
            proposal=validate_refund_link(db,refund_txn_id,original_txn_id,amount_cents)
            old=db.execute('SELECT * FROM refund_links WHERE refund_txn_id=?',(proposal['refund_txn_id'],)).fetchone()
            old=dict(old) if old else None;identifier=old['id'] if old else 'RL-'+uuid.uuid4().hex
            values=(proposal['original_txn_id'],proposal['amount_cents'],reason,confidence,ALGORITHM,proposal['context_json'])
            if old:
                db.execute("""UPDATE refund_links SET original_txn_id=?,amount_cents=?,status='confirmed',reason=?,confidence=?,
                    algorithm_version=?,context_json=?,decided_at=CURRENT_TIMESTAMP,decided_by='user' WHERE id=?""",values+(identifier,))
            else:
                db.execute("""INSERT INTO refund_links(original_txn_id,amount_cents,status,reason,confidence,algorithm_version,context_json,
                    id,refund_txn_id,decided_at,decided_by) VALUES (?,?,'confirmed',?,?,?,?,?,?,CURRENT_TIMESTAMP,'user')""",
                    values+(identifier,proposal['refund_txn_id']))
            new=dict(db.execute('SELECT * FROM refund_links WHERE id=?',(identifier,)).fetchone())
            audit(db,'refund_confirmed',identifier,dict(old=old,new=new,algorithm_version=ALGORITHM))
        return new

    def revoke_refund_link(self,identifier,*,reason='用户撤销退款关联确认'):
        reason=_reason(reason)
        with self.connect() as db:
            self._begin_refund_write(db)
            old=db.execute('SELECT * FROM refund_links WHERE id=?',(identifier,)).fetchone()
            if not old or old['status'] not in ('confirmed','needs_review'):raise ValueError('请选择已确认或待复核的退款关联')
            assessed=assess_confirmed_links(db)
            if any(item['link']['id']==identifier and not item['valid'] for item in assessed):
                invalidate_refund_links(db,[old['refund_txn_id'],old['original_txn_id']],'撤销失效关联前，相关决定一并转复核')
            db.execute("UPDATE refund_links SET status='rejected',decided_at=CURRENT_TIMESTAMP,decided_by='user' WHERE id=?",(identifier,))
            new=dict(db.execute('SELECT * FROM refund_links WHERE id=?',(identifier,)).fetchone())
            audit(db,'refund_revoked',identifier,dict(reason=reason,old=dict(old),new=new,algorithm_version=ALGORITHM))
        return new

    def create_refund_group(self,transaction_ids,*,reason):
        reason=_reason(reason)
        with self.connect() as db:
            self._begin_refund_write(db);rows,_=_refund_members(db,transaction_ids)
            for row in rows:
                if db.execute("SELECT 1 FROM link_members m JOIN link_groups g ON g.id=m.group_id WHERE m.transaction_id=? AND g.status='confirmed'",(row['id'],)).fetchone():
                    raise ValueError('来源已在已确认组中，请先拆分；不做传递合并')
            members={r['id'] for r in rows}
            for existing in db.execute("SELECT id FROM link_groups WHERE kind='refund_duplicate' AND status='candidate' ORDER BY created_at,id"):
                if {r[0] for r in db.execute('SELECT transaction_id FROM link_members WHERE group_id=?',(existing['id'],))}==members:
                    return existing['id']  # An idempotent retry never creates another proposal or audit event.
            identifier='RG-'+uuid.uuid4().hex
            db.execute("INSERT INTO link_groups(id,kind,status,reason,confidence,algorithm_version) VALUES (?,'refund_duplicate','candidate',?,'B',?)",(identifier,reason,ALGORITHM))
            db.executemany("INSERT INTO link_members VALUES (?,?,'refund')",[(identifier,r['id']) for r in rows])
            audit(db,'refund_group_proposed',identifier,dict(transaction_ids=[r['id'] for r in rows],reason=reason,algorithm_version=ALGORITHM))
        return identifier

    def _decide_refund_group(self,db,group,decision,acknowledge_no_wallet=False):
        if decision=='split' and group['status']!='confirmed':raise ValueError('只有已确认的退款组可以拆分')
        ids=[r[0] for r in db.execute('SELECT transaction_id FROM link_members WHERE group_id=?',(group['id'],))]
        if decision=='confirmed':
            if group['status']!='candidate':raise ValueError('退款组已有决定，请先明确重新核对')
            rows,accounts=_refund_members(db,ids)
            if group['confidence']=='C':raise ValueError('C级不能直接确认退款重复组')
            if not any(is_wallet(r,accounts) for r in rows) and acknowledge_no_wallet is not True:
                raise ValueError('本组没有钱包，请再次核对来源并明确确认')
            if db.execute("""SELECT 1 FROM link_members a JOIN link_members b ON a.transaction_id=b.transaction_id
                JOIN link_groups g ON g.id=b.group_id WHERE a.group_id=? AND b.group_id<>? AND g.status='confirmed'""",(group['id'],group['id'])).fetchone():
                raise ValueError('来源已在其他确认组中，请先拆分；不做传递合并')
        invalidate_refund_links(db,ids,'退款组决定改变：'+decision,group_ids=[group['id']])
        db.execute("UPDATE link_members SET role='refund' WHERE group_id=?",(group['id'],))
        db.execute("UPDATE link_groups SET status=?,decided_at=CURRENT_TIMESTAMP,decided_by='user' WHERE id=?",(decision,group['id']))
        audit(db,'refund_group_'+decision,group['id'],dict(previous_status=group['status'],reason=group['reason'],
              non_wallet_acknowledged=bool(acknowledge_no_wallet),algorithm_version=ALGORITHM))

    def spending_view(self,basis=None):
        from .spending import spending_view
        with self.connect(read_only=True) as db:
            db.execute('BEGIN');return spending_view(db,self._source_frame(db),basis)
