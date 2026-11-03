"""Manual cross-source decisions and a single conservative statistical projection."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, date
from difflib import SequenceMatcher
from hashlib import sha256
import json
import re
import uuid

ALGORITHM = 'm1a-2'
ACTIVE = ('normal','legacy')


def eligible(row):
    return row['status'] in ACTIVE and row['kind']=='支出' and row['category']!='转账'


def is_wallet(row,accounts):
    return row['channel'] in ('微信','支付宝') or accounts.get(row['account_id'],{}).get('type')=='wallet'


def representative(rows, accounts):
    def rank(row):
        account = accounts.get(row['account_id'], {})
        wallet = account.get('type')=='wallet' or row['channel'] in ('微信','支付宝')
        return (0 if wallet and row['merchant'] else 1 if account.get('type')=='wallet' else 2,
                row.get('imported_at') or '', row['id'])
    return min(rows,key=rank)


def valid_group(rows,accounts=None):
    accounts=accounts or {}
    return (len(rows)>=2 and all(eligible(r) for r in rows)
            and sum(is_wallet(r,accounts) for r in rows)<=1
            and len({(r['currency'],r['kind'],r['amount_cents']) for r in rows})==1)


def audit(db, event, entity, detail):
    db.execute('INSERT INTO audit_events(event,entity_id,detail) VALUES (?,?,?)',
               (event,entity,json.dumps(detail,ensure_ascii=False)))


class Reconciliation:
    def accounts(self):
        with self.connect(read_only=True) as db:return [dict(r) for r in db.execute('SELECT * FROM accounts ORDER BY created_at,id')]

    def add_account(self,channel,account_type,label,last4=''):
        if account_type not in ('wallet','debit','credit'):raise ValueError('账户类型无效')
        if not 1<=len(label.strip())<=60 or not 1<=len(channel.strip())<=30:raise ValueError('请填写账户名称与平台')
        if last4 and not re.fullmatch(r'\d{4}',last4):raise ValueError('仅填写卡号末四位，或留空')
        identifier='AC-'+uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT INTO accounts(id,channel,type,label,last4) VALUES (?,?,?,?,?)',
                       (identifier,channel.strip(),account_type,label.strip(),last4))
            audit(db,'account_created',identifier,{'label':label.strip()})
        return identifier

    def funding_links(self):
        with self.connect(read_only=True) as db:return [dict(r) for r in db.execute('SELECT * FROM funding_links ORDER BY created_at,id')]

    def add_funding_link(self,wallet,bank,start,end=None):
        start=date.fromisoformat(start).isoformat();end=date.fromisoformat(end).isoformat() if end else None
        if end and end<start:raise ValueError('绑定结束日期不能早于开始日期')
        with self.connect() as db:
            accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
            if accounts.get(wallet,{}).get('type')!='wallet' or accounts.get(bank,{}).get('type') not in ('debit','credit'):
                raise ValueError('请选择钱包账户和银行卡账户')
            # Overlapping declarations are retained only after the old interval is ended.
            overlap=db.execute('SELECT 1 FROM funding_links WHERE wallet_account_id=? AND active_from<=? AND COALESCE(active_to,?)>=?',
                               (wallet,end or '9999-12-31','9999-12-31',start)).fetchone()
            if overlap:raise ValueError('该钱包在此期间已有绑定，请先结束原绑定再声明新绑定')
            identifier='FL-'+uuid.uuid4().hex
            db.execute('INSERT INTO funding_links(id,wallet_account_id,bank_account_id,active_from,active_to) VALUES (?,?,?,?,?)',
                       (identifier,wallet,bank,start,end))
            audit(db,'funding_declared',identifier,{'wallet':wallet,'bank':bank,'from':start,'to':end})
        return identifier

    def end_funding_link(self,identifier,end):
        end=date.fromisoformat(end).isoformat()
        with self.connect() as db:
            row=db.execute('SELECT * FROM funding_links WHERE id=?',(identifier,)).fetchone()
            if not row or end<row['active_from']:raise ValueError('结束日期不能早于绑定开始日期')
            db.execute('UPDATE funding_links SET active_to=? WHERE id=?',(end,identifier))
            audit(db,'funding_ended',identifier,{'to':end})

    @staticmethod
    def _source_frame(db):
        import pandas as pd
        frame=pd.read_sql_query('''SELECT t.*,COALESCE(b.started_at,'') AS imported_at
          FROM transactions t LEFT JOIN import_batches b ON b.id=t.import_batch_id
          WHERE t.status<>'deleted' ORDER BY t.date,t.id''',db)
        frame['included_in_stats']=frame.status.isin(ACTIVE)
        frame['counting_note']=frame.status.map({'refund_pending':'退款待处理 · 未扣退款','review':'类型待复核'}).fillna('计入')
        frame.loc[frame.kind=='转账','counting_note']='转账 · 不计收支'
        groups=[dict(r) for r in db.execute("SELECT * FROM link_groups WHERE status='confirmed' AND kind='purchase_duplicate'")]
        byid={r['id']:r for r in frame.to_dict('records')}
        accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
        decisions=[];notes={};excluded=set();invalid_groups={}
        for row in db.execute("""SELECT DISTINCT t.id FROM transactions t JOIN link_members m ON m.transaction_id=t.id
            JOIN link_groups g ON g.id=m.group_id WHERE g.status='needs_review'
            AND t.status IN ('normal','legacy') AND t.kind='支出'"""):
            notes[row['id']]='关联待复核 · 独立计数'
        members_by_group=defaultdict(list)
        for member in db.execute("SELECT m.* FROM link_members m JOIN link_groups g ON g.id=m.group_id WHERE g.status='confirmed' AND g.kind='purchase_duplicate'"):
            members_by_group[member['group_id']].append(dict(member))
        for group in groups:
            members=members_by_group[group['id']]
            rows=[byid[m['transaction_id']] for m in members if m['transaction_id'] in byid]
            purchases=[m['transaction_id'] for m in members if m['role']=='purchase']
            if len(rows)!=len(members) or not valid_group(rows,accounts) or len(purchases)!=1:
                invalid_groups[group['id']]='当前来源不再符合排重约束'
                frame.loc[frame.id.isin([r['id'] for r in rows]),'link_group_id']=None
                notes.update({r['id']:'关联失效 · 已恢复计数，请复核' for r in rows})
                continue
            rep=purchases[0]
            ids=[r['id'] for r in rows]
            excluded.update(txid for txid in ids if txid!=rep)
            notes.update({txid:'消费代表 · 计一次' if txid==rep else '资金来源腿 · 不重复计数' for txid in ids})
            decisions.append(dict(id=group['id'],status='confirmed',representative_id=rep,
                                  transaction_ids=ids,reason=group['reason'],decided_at=group['decided_at'],
                                  source_rows=rows,algorithm_version=group['algorithm_version']))
        frame.loc[frame.id.isin(excluded),'included_in_stats']=False
        frame['counting_note']=[notes.get(identifier,note) for identifier,note in zip(frame.id,frame.counting_note)]
        frame.attrs['link_decisions']=decisions
        frame.attrs['invalid_groups']=invalid_groups
        return frame

    def repair_invalid_groups(self,*,db=None,source='manual',identifier=None):
        """Explicit write operation; ordinary readers never persist repairs or audit."""
        if self.read_only:raise ValueError('只读账本不能执行关联修复，请用可写模式打开')
        if db is None:
            with self.connect() as connection:return self.repair_invalid_groups(db=connection,source=source,identifier=identifier)
        if identifier is not None and not db.execute('SELECT 1 FROM link_groups WHERE id=?',(identifier,)).fetchone():
            raise ValueError('关联组不存在')
        invalid=self._source_frame(db).attrs['invalid_groups']
        repaired=0
        for gid,reason in invalid.items():
            if identifier is not None and gid!=identifier:continue
            changed=db.execute("UPDATE link_groups SET status='needs_review',decided_at=CURRENT_TIMESTAMP,decided_by='system' WHERE id=? AND status='confirmed'",(gid,)).rowcount
            if changed:
                audit(db,'link_invalidated',gid,dict(reason=reason,algorithm_version=ALGORITHM,trigger=source));repaired+=1
        return repaired

    def source_transactions(self):
        """All active source legs, including refunds and excluded funding legs."""
        with self.connect(read_only=True) as db:return self._source_frame(db)

    def pending_refunds(self,month=None):
        rows=self.source_transactions()
        rows=rows[rows.status=='refund_pending']
        if month and month!='全部月份':rows=rows[rows.date.str.startswith(month)]
        return dict(count=len(rows),amount_cents=int(rows.amount_cents.sum()))

    def reconciliation_groups(self):
        with self.connect(read_only=True) as db:
            invalid=self._source_frame(db).attrs['invalid_groups']
            groups=[dict(r) for r in db.execute('SELECT * FROM link_groups ORDER BY created_at DESC,id')]
            for g in groups:
                g['stored_status']=g['status'];g['repair_pending']=g['id'] in invalid
                if g['repair_pending']:g['status']='needs_review'
                g['members']=[dict(r) for r in db.execute('''SELECT t.*,m.role FROM link_members m
                    JOIN transactions t ON t.id=m.transaction_id WHERE m.group_id=? ORDER BY m.role,t.id''',(g['id'],))]
            return groups

    def generate_candidates(self):
        """Indexed buckets + bisect; at most 20 inspected neighbours per purchase."""
        with self.connect() as db:
            self.repair_invalid_groups(db=db,source='candidate_scan')
            frame=self._source_frame(db)
            accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
            links=[dict(r) for r in db.execute('SELECT * FROM funding_links')]
            locked={r[0] for r in db.execute("SELECT m.transaction_id FROM link_members m JOIN link_groups g ON m.group_id=g.id WHERE g.status='confirmed'")}
            rows=[r for r in frame.to_dict('records') if eligible(r) and r['id'] not in locked]
            buckets=defaultdict(list)
            for r in rows:
                r['_time']=datetime.fromisoformat(r['date']).timestamp()
                buckets[(r['currency'],r['kind'],r['amount_cents'])].append(r)
            pairs={};overflow=set();strong=defaultdict(set)
            for bucket in buckets.values():
                bucket.sort(key=lambda r:(r['_time'],r['id']));times=[r['_time'] for r in bucket]
                for left in bucket:
                    lo=bisect_left(times,left['_time']-86400);hi=bisect_right(times,left['_time']+86400)
                    # Prefer the nearest bounded neighbours, never scan a huge same-amount bucket.
                    center=bisect_left(times,left['_time']);indices=range(max(lo,center-20),min(hi,center+21))
                    nearby=sorted((bucket[i] for i in indices if bucket[i]['id']!=left['id']),key=lambda r:(abs(r['_time']-left['_time']),r['id']))
                    if hi-lo-1>20:overflow.add(left['id'])
                    for right in nearby[:20]:
                        if left['channel']==right['channel'] and not (left['account_id'] and right['account_id'] and left['account_id']!=right['account_id']):continue
                        if left['account_id'] and left['account_id']==right['account_id']:continue
                        if is_wallet(left,accounts) and is_wallet(right,accounts):continue
                        pair=tuple(sorted([left['id'],right['id']]))
                        if pair in pairs:continue
                        a,b=left,right
                        if accounts.get(b['account_id'],{}).get('type')=='wallet':a,b=b,a
                        binding=bool(a['account_id'] and b['account_id'] and any(
                            l['wallet_account_id']==a['account_id'] and l['bank_account_id']==b['account_id']
                            and l['active_from']<=min(a['date'][:10],b['date'][:10])
                            and (not l['active_to'] or l['active_to']>=max(a['date'][:10],b['date'][:10])) for l in links))
                        bank_text=(b['merchant']+' '+b['description']).casefold()
                        wallet_channel=a['channel'] if a['channel'] in ('微信','支付宝') else accounts.get(a['account_id'],{}).get('channel')
                        channel_ok=(wallet_channel=='微信' and any(s in bank_text for s in ('财付通','微信支付'))
                                    or wallet_channel=='支付宝' and any(s in bank_text for s in ('支付宝','alipay')))
                        strong_match=binding and channel_ok and abs(a['_time']-b['_time'])<=120
                        if strong_match:
                            strong[left['id']].add(right['id']);strong[right['id']].add(left['id'])
                        text1=left['merchant']+' '+left['description'];text2=right['merchant']+' '+right['description']
                        similar=SequenceMatcher(None,text1,text2).ratio()>=.55 or bool(left['merchant'] and left['merchant']==right['merchant'])
                        pairs[pair]=(left,right,strong_match,similar)
            # Only refresh undecided proposals; rejected/split/edited decisions survive reruns.
            db.execute("UPDATE link_groups SET status='stale' WHERE status='candidate'")
            created=0
            for pair,(a,b,strong_match,similar) in pairs.items():
                unique=strong_match and len(strong[a['id']])==len(strong[b['id']])==1 and not ({a['id'],b['id']}&overflow)
                level='A' if unique else 'B' if similar else 'C'
                gid='LG-'+sha256('|'.join(pair).encode()).hexdigest()[:24]
                prior=db.execute('SELECT status FROM link_groups WHERE id=?',(gid,)).fetchone()
                if prior and prior['status'] not in ('candidate','stale'):continue
                reason={'A':'有效绑定、同额同向、两分钟内且候选唯一；仍需人工确认',
                        'B':'同额同向、一天内且商户/描述相近；请人工核对',
                        'C':'仅金额与日期相近，证据不足；不建议直接确认'}[level]
                if prior:
                    db.execute("UPDATE link_groups SET status='candidate',reason=?,confidence=?,algorithm_version=? WHERE id=?",(reason,level,ALGORITHM,gid))
                else:
                    db.execute("INSERT INTO link_groups(id,kind,status,reason,confidence,algorithm_version) VALUES (?,'purchase_duplicate','candidate',?,?,?)",(gid,reason,level,ALGORITHM))
                    rep=representative([a,b],accounts)
                    db.executemany('INSERT INTO link_members(group_id,transaction_id,role) VALUES (?,?,?)',
                                   [(gid,r['id'],'purchase' if r['id']==rep['id'] else 'funding') for r in (a,b)])
                created+=1
            return dict(candidates=created,overflow=len(overflow),automatic=False)

    def clean_stale_candidates(self):
        """Remove undecided expired proposals only, never user decisions or raw evidence."""
        with self.connect() as db:
            ids=[r[0] for r in db.execute("SELECT id FROM link_groups WHERE status='stale' AND decided_by IS NULL")]
            for gid in ids:
                db.execute('DELETE FROM link_members WHERE group_id=?',(gid,))
                db.execute('DELETE FROM link_groups WHERE id=?',(gid,))
            if ids:audit(db,'stale_candidates_cleaned','reconciliation',{'count':len(ids)})
        return len(ids)

    def candidate_summary(self):
        with self.connect(read_only=True) as db:
            counts=dict(db.execute("SELECT confidence,COUNT(*) FROM link_groups WHERE status='candidate' GROUP BY confidence"))
            return {level:counts.get(level,0) for level in ('A','B','C')}

    def group_diagnostics(self,identifier):
        """Explain evidence gaps without changing records, accounts or candidate grades."""
        with self.connect(read_only=True) as db:
            group=db.execute('SELECT * FROM link_groups WHERE id=?',(identifier,)).fetchone()
            if not group:raise ValueError('关联组不存在')
            rows=[dict(r) for r in db.execute('SELECT t.* FROM transactions t JOIN link_members m ON t.id=m.transaction_id WHERE m.group_id=?',(identifier,))]
            accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
            wallets=[r for r in rows if is_wallet(r,accounts)];gaps=[]
            def gap(code,message):gaps.append(dict(code=code,message=message))
            if not wallets:gap('no_wallet','本组均非钱包来源，可能是两笔真实消费；确认需要额外核对，不能凭金额相近判断。')
            elif len(wallets)>1:gap('multiple_wallets','本组含多个钱包，不允许确认为同一笔消费，请拒绝或核对导入来源。')
            if any(not r['account_id'] for r in rows):
                gap('account','部分来源未指定账户。先建立账户；旧的未指定记录需核对并删除原导入批次后，选择正确账户重新导入。')
            if not valid_group(rows,accounts):gap('record_fields','金额、币种、类型、分类或状态不符合消费排重要求，请先编辑或核对原始来源。')
            if len(wallets)==1 and len(rows)>=2:
                wallet=wallets[0]
                channel=wallet['channel'] if wallet['channel'] in ('微信','支付宝') else accounts.get(wallet['account_id'],{}).get('channel')
                for bank in [r for r in rows if r['id']!=wallet['id']]:
                    try:
                        seconds=abs((datetime.fromisoformat(wallet['date'])-datetime.fromisoformat(bank['date'])).total_seconds())
                        if seconds>120:gap('time',f'两来源相隔 {seconds/60:.1f} 分钟，不满足A级的两分钟条件；请核对是否确为同笔。')
                    except ValueError:gap('time','交易时间格式不明确，请先编辑为合法日期时间。')
                    if wallet['account_id'] and bank['account_id']:
                        linked=db.execute('''SELECT 1 FROM funding_links WHERE wallet_account_id=? AND bank_account_id=?
                            AND active_from<=? AND COALESCE(active_to,'9999-12-31')>=?''',
                            (wallet['account_id'],bank['account_id'],min(wallet['date'][:10],bank['date'][:10]),max(wallet['date'][:10],bank['date'][:10]))).fetchone()
                        if not linked:
                            w=accounts.get(wallet['account_id'],{}).get('label','钱包账户');b=accounts.get(bank['account_id'],{}).get('label','资金账户')
                            gap('binding',f'“{w}”与“{b}”在交易日期没有有效支付绑定；请声明正确绑定及有效期。')
                    text=(bank['merchant']+' '+bank['description']).casefold()
                    tokens={'微信':('财付通','微信支付'),'支付宝':('支付宝','alipay')}.get(channel,())
                    if not tokens or not any(t in text for t in tokens):
                        gap('channel','资金腿描述未匹配该钱包的支付通道，或钱包平台暂无A级规则；请核对原始来源，不要为了升级等级改写事实。')
                    t1=wallet['merchant']+' '+wallet['description'];t2=bank['merchant']+' '+bank['description']
                    if SequenceMatcher(None,t1,t2).ratio()<.55 and not (wallet['merchant'] and wallet['merchant']==bank['merchant']):
                        gap('merchant','商户/描述相似度不足以支持B级；有可靠绑定且满足其余条件时可重新扫描判断A级。')
            if rows and group['confidence']!='A':
                ids=[r['id'] for r in rows];placeholders=','.join('?' for _ in ids)
                others=db.execute(f"SELECT COUNT(DISTINCT g.id) FROM link_groups g JOIN link_members m ON m.group_id=g.id WHERE g.id<>? AND g.status='candidate' AND m.transaction_id IN ({placeholders})",[identifier]+ids).fetchone()[0]
                if others:gap('ambiguity','同一来源还有其他候选，请逐笔排除两笔真实消费的可能；A级还要求候选唯一。')
            if not gaps:gap('rescan','当前字段未发现上述缺口；若刚补充证据，请重新生成候选，等级仍由完整候选集判断。')
            return dict(wallet_count=len(wallets),gaps=gaps,requires_reimport=any(g['code']=='account' for g in gaps))

    def decide_group(self,identifier,decision,*,acknowledge_no_wallet=False):
        if decision not in ('confirmed','rejected','split'):raise ValueError('无效对账决定')
        with self.connect() as db:
            group=db.execute('SELECT * FROM link_groups WHERE id=?',(identifier,)).fetchone()
            if not group:raise ValueError('关联组不存在')
            if decision=='split' and group['status']!='confirmed':raise ValueError('只有已确认的关联组可以拆分')
            if decision=='confirmed':
                if group['status']!='candidate':raise ValueError('此组已有决定或候选已过期，请重新核对来源')
                rows=[dict(r) for r in db.execute('''SELECT t.*,COALESCE(b.started_at,'') AS imported_at
                    FROM transactions t JOIN link_members m ON m.transaction_id=t.id
                    LEFT JOIN import_batches b ON b.id=t.import_batch_id WHERE m.group_id=?''',(identifier,))]
                accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
                wallet_count=sum(is_wallet(r,accounts) for r in rows)
                if wallet_count>1:raise ValueError('同一消费组最多一个钱包，两个钱包不能确认为重复消费')
                if group['kind']!='purchase_duplicate' or not valid_group(rows,accounts):raise ValueError('仅同币种、同额、同向的普通消费可确认排重；退款与转账不可加入')
                active=db.execute("SELECT 1 FROM link_members a JOIN link_members b ON a.transaction_id=b.transaction_id JOIN link_groups g ON g.id=b.group_id WHERE a.group_id=? AND b.group_id<>? AND g.status='confirmed'",(identifier,identifier)).fetchone()
                if active:raise ValueError('成员已在其他确认组内，请先拆分原组；不做传递合并')
                if group['confidence']=='C':raise ValueError('C级仅金额日期相近，不能直接确认；请补充账户/绑定或来源信息，再重新核对')
                if wallet_count==0 and acknowledge_no_wallet is not True:
                    raise ValueError('本组没有钱包消费，可能是两笔真实消费；需要再次核对来源并明确确认')
                rep=representative(rows,accounts)
                db.execute("UPDATE link_members SET role=CASE WHEN transaction_id=? THEN 'purchase' ELSE 'funding' END WHERE group_id=?",(rep['id'],identifier))
            db.execute("UPDATE link_groups SET status=?,decided_at=CURRENT_TIMESTAMP,decided_by='user' WHERE id=?",(decision,identifier))
            audit(db,'link_'+decision,identifier,{'algorithm_version':group['algorithm_version'],'reason':group['reason'],
                                                'non_wallet_acknowledged':bool(decision=='confirmed' and wallet_count==0 and acknowledge_no_wallet is True)})

    def reopen_group(self,identifier):
        """Only an explicit user action may undo a rejected/split/invalidated decision."""
        with self.connect() as db:
            group=db.execute('SELECT * FROM link_groups WHERE id=?',(identifier,)).fetchone()
            if not group or group['status'] not in ('rejected','split','needs_review','candidate','stale'):
                raise ValueError('请选择待核对、已拒绝或已拆分的组')
            rows=[dict(r) for r in db.execute('SELECT t.* FROM transactions t JOIN link_members m ON t.id=m.transaction_id WHERE m.group_id=?',(identifier,))]
            accounts={r['id']:dict(r) for r in db.execute('SELECT * FROM accounts')}
            if not valid_group(rows,accounts):raise ValueError('当前来源金额、类型、钱包数量或状态不符合排重条件，请先核对交易')
            db.execute("UPDATE link_groups SET status='candidate',decided_at=CURRENT_TIMESTAMP,decided_by='user' WHERE id=?",(identifier,))
            audit(db,'link_reopened',identifier,{'previous_status':group['status']})

    @staticmethod
    def invalidate_groups(db,txid,reason):
        groups=[r[0] for r in db.execute("SELECT g.id FROM link_groups g JOIN link_members m ON m.group_id=g.id WHERE m.transaction_id=? AND g.status IN ('candidate','confirmed')",(txid,))]
        for gid in groups:
            db.execute("UPDATE link_groups SET status='needs_review',decided_at=CURRENT_TIMESTAMP,decided_by='system' WHERE id=?",(gid,))
            audit(db,'link_invalidated',gid,{'transaction_id':txid,'reason':reason})
