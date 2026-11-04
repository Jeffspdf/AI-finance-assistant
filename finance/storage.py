from pathlib import Path
import sqlite3
import pandas as pd
from contextlib import contextmanager,closing
from collections import defaultdict
import json
import threading
import uuid
from hashlib import sha256
from .migrations import ensure_schema, assert_supported, backup_database, inspect_database
from .reconciliation import Reconciliation, audit
from .categories import validate_kind_category,category_options
from .backup_management import BackupManagement

class Store(Reconciliation,BackupManagement):
    def __init__(self, path, *, read_only=False):
        self.path = str(path)
        self._lock=threading.RLock()
        self.read_only=bool(read_only)
        self.semantic_backup=None
        if self.read_only:
            self.migration_backup=None
            with self.connect(read_only=True) as db:
                from .migrations import SCHEMA_VERSION
                if assert_supported(db)!=SCHEMA_VERSION:raise ValueError('请先用可写模式升级此账本，再以只读模式打开')
        else:
            self.migration_backup=ensure_schema(path)
            self._repair_category_semantics_on_open()
            self.repair_invalid_groups(source='startup')

    @contextmanager
    def connect(self, *, read_only=False):
        with self._lock:
            readonly=self.read_only or read_only
            db=sqlite3.connect(Path(self.path).resolve().as_uri()+'?mode=ro',uri=True,timeout=10) if readonly else sqlite3.connect(self.path,timeout=10)
            db.row_factory = sqlite3.Row
            try:
                assert_supported(db)
                db.execute('PRAGMA foreign_keys=ON')
                if readonly:db.execute('PRAGMA query_only=ON')
                with db:yield db
            finally:db.close()

    def _repair_category_semantics_on_open(self):
        """Normalize legacy categories, never infer a different cash-flow kind."""
        with self.connect(read_only=True) as db:
            count=db.execute("SELECT COUNT(*) FROM transactions WHERE (kind='转账')<>(category='转账')").fetchone()[0]
            rules=[dict(r) for r in db.execute("SELECT * FROM merchant_rules WHERE (kind='转账')<>(category='转账')")]
        if not count and not rules:return
        self.semantic_backup=self.create_semantic_backup()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS transaction_edits(id INTEGER PRIMARY KEY,transaction_id TEXT,old_values TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            for old in db.execute("SELECT * FROM transactions WHERE (kind='转账')<>(category='转账')").fetchall():
                category={'收入':'其他收入','支出':'其他支出','转账':'转账'}[old['kind']]
                db.execute('INSERT INTO transaction_edits(transaction_id,old_values) VALUES (?,?)',(old['id'],json.dumps(dict(old),ensure_ascii=False)))
                self.invalidate_groups(db,old['id'],'旧类型/分类不一致，启动时修正分类，收支类型与金额不变')
                db.execute("UPDATE transactions SET category=?,category_source='待确认' WHERE id=?",(category,old['id']))
                audit(db,'category_semantics_repaired',old['id'],dict(kind=old['kind'],old_category=old['category'],category=category,amount_unchanged=True))
            for rule in rules:
                db.execute('DELETE FROM merchant_rules WHERE merchant=? AND kind=?',(rule['merchant'],rule['kind']))
                audit(db,'invalid_category_rule_removed',rule['merchant'],dict(kind=rule['kind'],category=rule['category']))

    def transactions(self):
        """The only statistical projection; source_transactions retains all source legs."""
        frame=self.source_transactions()
        pending=frame[frame.status=='refund_pending']
        frame.attrs['refund_pending']=dict(count=len(pending),amount_cents=int(pending.amount_cents.sum()))
        return frame[frame.included_in_stats].copy()

    def import_rows(self, rows):
        with self.connect() as db:return self._insert_rows(db,rows)

    def validate_refund_link(self,refund_txn_id,original_txn_id,amount_cents=None):
        """M2-1 foundation: read-only validation, without confirming or deducting."""
        from .refund_rules import validate_refund_link
        with self.connect(read_only=True) as db:
            db.execute('BEGIN')  # One snapshot across sources, groups and existing decisions.
            return validate_refund_link(db,refund_txn_id,original_txn_id,amount_cents)

    def category_conflicts(self):
        """Expose invalid legacy classifications, including non-counting source legs."""
        frame=self.source_transactions()
        invalid=pd.Series(False,index=frame.index)
        for kind in ('收入','支出','转账'):
            invalid|=(frame.kind==kind)&~frame.category.isin(category_options(kind))
        return frame.loc[invalid].copy()

    def _insert_rows(self, db, rows):
        added, duplicates, conflicts = 0, 0, []
        existing={r['signature']:dict(r) for r in db.execute('SELECT * FROM transactions')}
        origins=defaultdict(list);weak_identities=defaultdict(list)
        def content_key(row):
            return tuple(row.get(k) for k in ('external_id','date','amount_cents','kind','merchant','description','currency'))
        def index(row):
            if row['status']=='deleted':return
            if row.get('source_document_id'):origins[(row['source_document_id'],row['source_row'])].append(row)
            if row['external_id']:weak_identities[content_key(row)].append(row)
        for row in existing.values():index(row)
        learned={(r['merchant'],r['kind']):r['category'] for r in db.execute('SELECT * FROM merchant_rules')}
        keys=['id','external_id','date','merchant','description','amount_cents','kind','category','category_source','channel','source_file','source_row','signature',
              'currency','status','account_id','source_document_id','raw_row_id','import_batch_id']
        for value in rows:
            row=dict(value)
            row.setdefault('currency','CNY');row.setdefault('status','normal')
            for key in ('account_id','source_document_id','raw_row_id','import_batch_id'):row.setdefault(key,None)
            validate_kind_category(row['kind'],row['category'])
            old=existing.get(row['signature'])
            if row.get('source_document_id') and (not old or old['status']=='deleted'):
                same_origin=origins.get((row['source_document_id'],row['source_row']),[])
                # An exact source row cannot become a second transaction by changing
                # parser/account. Scoped IDs in genuinely different known accounts
                # remain valid; a global external-ID uniqueness rule would lose data.
                ambiguous=[other for other in weak_identities.get(content_key(row),[])
                           if (other['channel'] in ('未指定','通用') or row['channel'] in ('未指定','通用'))
                           and (other['channel'],other['account_id'])!=(row['channel'],row['account_id'])]
                collision=(same_origin or ambiguous)
                if collision:
                    conflicts.append({'行号':row['source_row'],'原因':'相同原始行或未明确来源的同交易ID已存在，账户/格式声明不同，未重复计入；请核对原批次'})
                    continue
            if not old and row.get('source_document_id'):
                identity=('external|'+row['channel']+'|'+row['external_id']) if row['external_id'] else 'fingerprint|'+'|'.join([row['date'],str(row['amount_cents']),row['kind'],row['merchant'],row['description'],row['channel']])
                legacy=existing.get(sha256(identity.encode()).hexdigest())
                if legacy and legacy['status']!='deleted' and legacy['account_id'] is None:
                    if row['account_id'] is not None:
                        conflicts.append({'行号':row['source_row'],'原因':'存在未指定账户的旧记录，未再次计入；请核对旧记录来源'})
                        continue
                    old=legacy
            if old and old['status']!='deleted':
                if any(old[k] != row[k] for k in ['date','amount_cents','kind','merchant','description','currency']):
                    conflicts.append({'行号':row['source_row'], '原因':'同一交易 ID 的内容不同，未覆盖原记录'})
                else:duplicates+=1
                continue
            learned_category=learned.get((row['merchant'].strip(),row['kind']))
            if learned_category and row['category_source']!='用户修正':
                try:validate_kind_category(row['kind'],learned_category)
                except ValueError:pass
                else:row.update(category=learned_category,category_source='商户学习')
            validate_kind_category(row['kind'],row['category'])
            if old:
                # Reimport after a soft deletion is explicit; preserve stable identity.
                row['id']=old['id']
                db.execute('UPDATE transactions SET '+','.join(k+'=?' for k in keys if k!='id')+' WHERE id=?',
                           [row[k] for k in keys if k!='id']+[old['id']])
            else:
                db.execute(f"INSERT INTO transactions ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",[row[k] for k in keys])
            existing[row['signature']]=row;index(row);added+=1
        return added, duplicates, conflicts

    def import_statement(self,result):
        """Commit provenance, issues and transactions together, even an all-invalid file."""
        with self.connect() as db:return self._import_statement(db,result)

    def _import_statement(self,db,result):
        """Also used by the demo generator inside its replacement transaction."""
        if not result.document:raise ValueError('此预览没有来源信息，请重新使用导入入口解析')
        doc=result.document;document_id='DOC-'+doc['sha256'];batch_id='BATCH-'+uuid.uuid4().hex
        account=doc.get('account_id')
        if account and not db.execute('SELECT 1 FROM accounts WHERE id=?',(account,)).fetchone():raise ValueError('所选账户已不存在')
        if account and doc['channel'] in ('微信','银行'):
            selected=db.execute('SELECT * FROM accounts WHERE id=?',(account,)).fetchone()
            if (doc['channel']=='微信' and (selected['type']!='wallet' or selected['channel']!='微信')) or (doc['channel']=='银行' and selected['type']=='wallet'):
                raise ValueError('账单格式与所选账户类型不一致，请重新选择')
        db.execute('INSERT OR IGNORE INTO source_documents(id,sha256,filename,channel,account_id,parser_version) VALUES (?,?,?,?,?,?)',
                   (document_id,doc['sha256'],doc['filename'],doc['channel'],account,doc['parser_version']))
        db.execute("INSERT INTO import_batches(id,document_id,account_id,parser_version,status) VALUES (?,?,?,?,'committing')",(batch_id,document_id,account,doc['parser_version']))
        raw_ids={}
        for raw in result.raw_rows:
            raw_id=document_id+'-'+str(raw['row_no']);raw_ids[raw['row_no']]=raw_id
            db.execute('INSERT OR IGNORE INTO raw_rows(id,document_id,row_no,raw_json,parse_status) VALUES (?,?,?,?,?)',
                       (raw_id,document_id,raw['row_no'],json.dumps(raw['fields'],ensure_ascii=False),raw['parse_status']))
        rows=[dict(r,source_document_id=document_id,raw_row_id=raw_ids[r['source_row']],import_batch_id=batch_id,account_id=account) for r in result.rows]
        added,duplicates,conflicts=self._insert_rows(db,rows)
        db.execute("UPDATE import_batches SET status='committed',finished_at=CURRENT_TIMESTAMP,added=?,duplicates=?,conflicts=?,issues_json=? WHERE id=?",(added,duplicates,len(conflicts),json.dumps(result.issues+conflicts,ensure_ascii=False),batch_id))
        audit(db,'batch_imported',batch_id,{'document':document_id,'added':added,'duplicates':duplicates,'conflicts':len(conflicts)})
        return added,duplicates,conflicts

    def import_batches(self):
        with self.connect(read_only=True) as db:
            return [dict(r) for r in db.execute('SELECT b.*,d.filename,d.sha256 FROM import_batches b JOIN source_documents d ON d.id=b.document_id ORDER BY b.started_at DESC,b.id')]

    def delete_import_batch(self,identifier):
        with self.connect() as db:
            batch=db.execute('SELECT * FROM import_batches WHERE id=?',(identifier,)).fetchone()
            if not batch or batch['status']=='deleted':raise ValueError('批次不存在或已经删除')
            ids=[r[0] for r in db.execute("SELECT id FROM transactions WHERE import_batch_id=? AND status<>'deleted'",(identifier,))]
            for txid in ids:self.invalidate_groups(db,txid,'所属导入批次已删除')
            db.execute("UPDATE transactions SET status='deleted' WHERE import_batch_id=?",(identifier,))
            db.execute("UPDATE import_batches SET status='deleted' WHERE id=?",(identifier,))
            audit(db,'batch_deleted',identifier,{'removed_transactions':len(ids),'raw_rows_retained':True})
            self.repair_invalid_groups(db=db,source='batch_delete')
        return len(ids)

    def transaction_source(self,identifier):
        with self.connect(read_only=True) as db:
            row=db.execute('''SELECT t.*,d.sha256,d.parser_version,r.raw_json,r.parse_status
              FROM transactions t LEFT JOIN source_documents d ON d.id=t.source_document_id
              LEFT JOIN raw_rows r ON r.id=t.raw_row_id WHERE t.id=?''',(identifier,)).fetchone()
            if not row:raise ValueError('交易不存在')
            return dict(row)

    def backup(self,destination=None):
        with self._lock:return backup_database(self.path,destination)

    def restore(self,source,confirmed=False):
        if self.read_only:raise ValueError('只读账本不能恢复或修改数据库')
        if not confirmed:raise ValueError('请先预览备份并确认恢复')
        source=Path(source).resolve()
        if source==Path(self.path).resolve():raise ValueError('不能恢复当前数据库自身')
        inspect_database(source)
        staged=Path(self.path).with_name('.restore-'+uuid.uuid4().hex+'.sqlite3')
        staged_backup=None
        try:
            backup_database(source,staged)
            staged_backup=ensure_schema(staged)
            with self._lock:
                before=self.backup()
                try:
                    # SQLite backup writes a transaction, rather than copying an open file.
                    with closing(sqlite3.connect(staged)) as origin, closing(sqlite3.connect(self.path)) as target:
                        assert_supported(target);origin.backup(target)
                    inspect_database(self.path)
                    self._repair_category_semantics_on_open()
                    self.repair_invalid_groups(source='restore')
                except Exception:
                    with closing(sqlite3.connect(before)) as origin, closing(sqlite3.connect(self.path)) as target:origin.backup(target)
                    raise
            return before
        finally:
            staged.unlink(missing_ok=True)
            if staged_backup:staged_backup.unlink(missing_ok=True)

    def correct_categories(self, corrections,learn=False):
        from .categories import CATEGORIES
        with self.connect() as db:
            for txid, category in corrections:
                if category not in CATEGORIES:
                    raise ValueError('无效分类')
                old=db.execute('SELECT category,kind FROM transactions WHERE id=?',(txid,)).fetchone()
                if old:validate_kind_category(old['kind'],category)
                if old and old['category']!=category:self.invalidate_groups(db,txid,'分类已修改，请重新核对关联')
                db.execute('UPDATE transactions SET category=?, category_source=? WHERE id=?', (category, '用户修正', txid))
                if learn:
                    r=db.execute('SELECT merchant,kind FROM transactions WHERE id=?',(txid,)).fetchone()
                    if r and r['merchant'].strip():db.execute('INSERT INTO merchant_rules(merchant,kind,category) VALUES (?,?,?) ON CONFLICT(merchant,kind) DO UPDATE SET category=excluded.category,updated_at=CURRENT_TIMESTAMP',(r['merchant'].strip(),r['kind'],category))
            self.repair_invalid_groups(db=db,source='category_edit')

    def save_budget(self, month, category, cents):
        if cents < 0: raise ValueError('预算不能为负数')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO budgets VALUES (?,?,?)', (month,category,int(cents)))

    def budgets(self, month):
        with self.connect(read_only=True) as db:
            return dict(db.execute('SELECT category,amount_cents FROM budgets WHERE month=?', (month,)).fetchall())

    def save_goal(self, plan):
        fields = ['name','target_cents','saved_cents','start_date','deadline','income_cents','budget_cents']
        with self.connect() as db:
            db.execute(f"INSERT INTO goals ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [plan[k] for k in fields])

    def goals(self):
        with self.connect(read_only=True) as db:
            return pd.read_sql_query('SELECT * FROM goals ORDER BY id DESC', db)

    def edit_transaction(self, txid, values):
        from .categories import CATEGORIES
        from datetime import datetime
        import json
        parsed=datetime.fromisoformat(values['date'])
        if not 2000<=parsed.year<=2100 or parsed.tzinfo:raise ValueError('日期须为 2000 至 2100 年本地时间')
        if values['kind'] not in ('收入','支出','转账') or values['category'] not in CATEGORIES:raise ValueError('无效类型或分类')
        validate_kind_category(values['kind'],values['category'])
        if not isinstance(values['amount_cents'],int) or not 0<values['amount_cents']<=10**12:raise ValueError('金额须大于零且在范围内')
        if not (values['merchant'].strip() or values['description'].strip()):raise ValueError('商户或描述至少填一项')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS transaction_edits(id INTEGER PRIMARY KEY,transaction_id TEXT,old_values TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            old=db.execute('SELECT * FROM transactions WHERE id=?',(txid,)).fetchone()
            if old is None:raise ValueError('记录不存在')
            db.execute('INSERT INTO transaction_edits(transaction_id,old_values) VALUES (?,?)',(txid,json.dumps(dict(old),ensure_ascii=False)))
            if any(old[k]!=values[k] for k in ['date','merchant','description','amount_cents','kind','category']):
                self.invalidate_groups(db,txid,'交易关键字段已编辑')
            fields=['date','merchant','description','amount_cents','kind','category']
            db.execute('UPDATE transactions SET '+','.join(k+'=?' for k in fields)+',category_source=? WHERE id=?',[values[k] for k in fields]+['用户修正',txid])
            self.repair_invalid_groups(db=db,source='transaction_edit')

    def update_goal(self, goal_id, plan):
        fields=['name','target_cents','saved_cents','start_date','deadline','income_cents','budget_cents']
        with self.connect() as db:
            db.execute('UPDATE goals SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[plan[k] for k in fields]+[goal_id])

    def delete_goal(self, goal_id):
        with self.connect() as db:
            db.execute('DELETE FROM goal_contributions WHERE goal_id=?',(goal_id,))
            db.execute('DELETE FROM goals WHERE id=?',(goal_id,))

    def contributions(self,goal_id):
        with self.connect(read_only=True) as db:return pd.read_sql_query('SELECT * FROM goal_contributions WHERE goal_id=? ORDER BY date,id',db,params=[int(goal_id)])

    def add_contribution(self,goal_id,when,amount_cents,today=None):
        from datetime import date
        today=today or date.today();when=date.fromisoformat(when)
        if not isinstance(amount_cents,int) or not 0<amount_cents<=10**12:raise ValueError('实际存入必须大于零且在范围内')
        with self.connect() as db:
            goal=db.execute('SELECT * FROM goals WHERE id=?',(int(goal_id),)).fetchone()
            if goal is None:raise ValueError('目标不存在')
            if when>today or when<date.fromisoformat(goal['start_date']):raise ValueError('实际存入日期须在开始日期之后且不晚于今天')
            db.execute('INSERT INTO goal_contributions(goal_id,date,amount_cents) VALUES (?,?,?)',(int(goal_id),when.isoformat(),amount_cents))

    def remove_contribution(self,goal_id,entry_id):
        with self.connect() as db:db.execute('DELETE FROM goal_contributions WHERE goal_id=? AND id=?',(int(goal_id),int(entry_id)))

    def apply_model_category(self,txid,category):
        from .categories import CATEGORIES
        if category not in CATEGORIES:raise ValueError('无效模型分类')
        with self.connect() as db:
            row=db.execute('SELECT kind,category,category_source FROM transactions WHERE id=?',(txid,)).fetchone()
            if not row or row['category_source']!='待确认':return False
            validate_kind_category(row['kind'],category)
            permitted=['工资','生活费','其他收入'] if row['kind']=='收入' else ['转账'] if row['kind']=='转账' else [c for c in CATEGORIES if c not in ['工资','生活费','其他收入','转账']]
            if category not in permitted:raise ValueError('模型分类与收支类型冲突')
            if row['category']!=category:self.invalidate_groups(db,txid,'接受模型分类修改，请重新核对关联')
            db.execute('UPDATE transactions SET category=?,category_source=? WHERE id=?',(category,'模型建议（需核对）',txid))
            self.repair_invalid_groups(db=db,source='model_category')
            return True

    def add_manual_transaction(self,when,merchant,amount_cents,kind,category,description=''):
        from .importer import parse_statement
        from .categories import CATEGORIES
        import csv,io,uuid
        if not isinstance(amount_cents,int) or amount_cents<=0:raise ValueError('金额必须大于零')
        if category not in CATEGORIES:raise ValueError('无效分类')
        validate_kind_category(kind,category)
        external='MANUAL-'+uuid.uuid4().hex
        stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['交易ID','日期','商户','描述','金额','类型','平台','币种'])
        writer.writerow([external,when,merchant,description,f'{amount_cents//100}.{amount_cents%100:02d}',kind,'手动','CNY'])
        result=parse_statement(stream.getvalue().encode('utf-8-sig'),'manual.csv')
        if not result.rows:raise ValueError(result.issues[0]['原因'] if result.issues else '无效交易')
        row=result.rows[0];row.update(category=category,category_source='用户修正',source_file='手动记账')
        self.import_rows([row]);return row['id']

    def merchant_rules(self):
        with self.connect(read_only=True) as db:return pd.read_sql_query('SELECT * FROM merchant_rules ORDER BY merchant,kind',db)

    def delete_merchant_rule(self,merchant,kind):
        with self.connect() as db:db.execute('DELETE FROM merchant_rules WHERE merchant=? AND kind=?',(merchant,kind))

    def batch_category(self,ids,category):
        from .categories import CATEGORIES
        import json
        if category not in CATEGORIES:raise ValueError('无效分类')
        snapshot=[]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS transaction_edits(id INTEGER PRIMARY KEY,transaction_id TEXT,old_values TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            for txid in dict.fromkeys(ids):
                old=db.execute('SELECT * FROM transactions WHERE id=?',(txid,)).fetchone()
                if old is None:continue
                validate_kind_category(old['kind'],category)
                snapshot.append(dict(id=txid,category=old['category'],source=old['category_source'],new=category))
                if old['category']!=category:self.invalidate_groups(db,txid,'批量分类已修改，请重新核对关联')
                db.execute('INSERT INTO transaction_edits(transaction_id,old_values) VALUES (?,?)',(txid,json.dumps(dict(old),ensure_ascii=False)))
                db.execute('UPDATE transactions SET category=?,category_source=? WHERE id=?',(category,'用户修正',txid))
            self.repair_invalid_groups(db=db,source='batch_category')
        return snapshot

    def undo_category_batch(self,snapshot):
        count=0
        with self.connect() as db:
            for row in snapshot:
                current=db.execute('SELECT category,category_source,kind FROM transactions WHERE id=?',(row['id'],)).fetchone()
                if current and current['category']==row['new'] and current['category_source']=='用户修正':
                    validate_kind_category(current['kind'],row['category'])
                    if row['category']!=row['new']:self.invalidate_groups(db,row['id'],'撤销批量分类，请重新核对关联')
                count+=db.execute('UPDATE transactions SET category=?,category_source=? WHERE id=? AND category=? AND category_source=?',(row['category'],row['source'],row['id'],row['new'],'用户修正')).rowcount
            self.repair_invalid_groups(db=db,source='undo_category')
        return count

    def copy_budgets(self,source,target,overwrite=False):
        import re
        if any(not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',m) for m in [source,target]):raise ValueError('无效月份')
        with self.connect() as db:
            rows=db.execute('SELECT category,amount_cents FROM budgets WHERE month=?',(source,)).fetchall()
            operation='REPLACE' if overwrite else 'IGNORE'
            db.executemany(f'INSERT OR {operation} INTO budgets(month,category,amount_cents) VALUES (?,?,?)',[(target,r['category'],r['amount_cents']) for r in rows])
        return len(rows)
