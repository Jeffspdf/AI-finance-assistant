from pathlib import Path
import sqlite3
import pandas as pd
from contextlib import contextmanager

class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY, external_id TEXT NOT NULL, date TEXT NOT NULL,
                merchant TEXT NOT NULL, description TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
                kind TEXT NOT NULL CHECK(kind IN ('收入','支出','转账')), category TEXT NOT NULL,
                category_source TEXT NOT NULL, channel TEXT NOT NULL, source_file TEXT NOT NULL,
                source_row INTEGER NOT NULL, signature TEXT NOT NULL UNIQUE
              );
              CREATE TABLE IF NOT EXISTS budgets(month TEXT NOT NULL, category TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>=0), PRIMARY KEY(month,category));
              CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, target_cents INTEGER NOT NULL, saved_cents INTEGER NOT NULL, start_date TEXT NOT NULL, deadline TEXT NOT NULL, income_cents INTEGER NOT NULL, budget_cents INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS idx_date ON transactions(date);
              CREATE TABLE IF NOT EXISTS goal_contributions(id INTEGER PRIMARY KEY AUTOINCREMENT,goal_id INTEGER NOT NULL,date TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>0),created_at TEXT DEFAULT CURRENT_TIMESTAMP);
              CREATE INDEX IF NOT EXISTS idx_contribution_goal ON goal_contributions(goal_id);
              CREATE TABLE IF NOT EXISTS merchant_rules(merchant TEXT NOT NULL,kind TEXT NOT NULL,category TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(merchant,kind));
              PRAGMA user_version=3;
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def transactions(self):
        with self.connect() as db:
            return pd.read_sql_query('SELECT * FROM transactions ORDER BY date, id', db)

    def import_rows(self, rows):
        added, duplicates, conflicts = 0, 0, []
        with self.connect() as db:
            existing={r['signature']:dict(r) for r in db.execute('SELECT * FROM transactions')}
            learned={(r['merchant'],r['kind']):r['category'] for r in db.execute('SELECT * FROM merchant_rules')}
            pending=[]
            for row in rows:
                row=dict(row)
                old=existing.get(row['signature'])
                if old:
                    if any(old[k] != row[k] for k in ['date','amount_cents','kind','merchant','description']):
                        conflicts.append({'行号':row['source_row'], '原因':'同一交易 ID 的内容不同，未覆盖原记录'})
                    else:
                        duplicates += 1
                    continue
                learned_category=learned.get((row['merchant'].strip(),row['kind']))
                if learned_category and row['category_source']!='用户修正':row.update(category=learned_category,category_source='商户学习')
                pending.append(row);existing[row['signature']]=row
                added += 1
            if pending:
                keys=['id','external_id','date','merchant','description','amount_cents','kind','category','category_source','channel','source_file','source_row','signature']
                db.executemany(f"INSERT INTO transactions ({','.join(keys)}) VALUES ({','.join('?' for _ in keys)})",[[row[k] for k in keys] for row in pending])
        return added, duplicates, conflicts

    def correct_categories(self, corrections,learn=False):
        from .categories import CATEGORIES
        with self.connect() as db:
            for txid, category in corrections:
                if category not in CATEGORIES:
                    raise ValueError('无效分类')
                db.execute('UPDATE transactions SET category=?, category_source=? WHERE id=?', (category, '用户修正', txid))
                if learn:
                    r=db.execute('SELECT merchant,kind FROM transactions WHERE id=?',(txid,)).fetchone()
                    if r and r['merchant'].strip():db.execute('INSERT INTO merchant_rules(merchant,kind,category) VALUES (?,?,?) ON CONFLICT(merchant,kind) DO UPDATE SET category=excluded.category,updated_at=CURRENT_TIMESTAMP',(r['merchant'].strip(),r['kind'],category))

    def save_budget(self, month, category, cents):
        if cents < 0: raise ValueError('预算不能为负数')
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO budgets VALUES (?,?,?)', (month,category,int(cents)))

    def budgets(self, month):
        with self.connect() as db:
            return dict(db.execute('SELECT category,amount_cents FROM budgets WHERE month=?', (month,)).fetchall())

    def save_goal(self, plan):
        fields = ['name','target_cents','saved_cents','start_date','deadline','income_cents','budget_cents']
        with self.connect() as db:
            db.execute(f"INSERT INTO goals ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})", [plan[k] for k in fields])

    def goals(self):
        with self.connect() as db:
            return pd.read_sql_query('SELECT * FROM goals ORDER BY id DESC', db)

    def edit_transaction(self, txid, values):
        from .categories import CATEGORIES
        from datetime import datetime
        import json
        parsed=datetime.fromisoformat(values['date'])
        if not 2000<=parsed.year<=2100 or parsed.tzinfo:raise ValueError('日期须为 2000 至 2100 年本地时间')
        if values['kind'] not in ('收入','支出','转账') or values['category'] not in CATEGORIES:raise ValueError('无效类型或分类')
        if not isinstance(values['amount_cents'],int) or not 0<values['amount_cents']<=10**12:raise ValueError('金额须大于零且在范围内')
        if not (values['merchant'].strip() or values['description'].strip()):raise ValueError('商户或描述至少填一项')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS transaction_edits(id INTEGER PRIMARY KEY,transaction_id TEXT,old_values TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            old=db.execute('SELECT * FROM transactions WHERE id=?',(txid,)).fetchone()
            if old is None:raise ValueError('记录不存在')
            db.execute('INSERT INTO transaction_edits(transaction_id,old_values) VALUES (?,?)',(txid,json.dumps(dict(old),ensure_ascii=False)))
            fields=['date','merchant','description','amount_cents','kind','category']
            db.execute('UPDATE transactions SET '+','.join(k+'=?' for k in fields)+',category_source=? WHERE id=?',[values[k] for k in fields]+['用户修正',txid])

    def update_goal(self, goal_id, plan):
        fields=['name','target_cents','saved_cents','start_date','deadline','income_cents','budget_cents']
        with self.connect() as db:
            db.execute('UPDATE goals SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[plan[k] for k in fields]+[goal_id])

    def delete_goal(self, goal_id):
        with self.connect() as db:
            db.execute('DELETE FROM goal_contributions WHERE goal_id=?',(goal_id,))
            db.execute('DELETE FROM goals WHERE id=?',(goal_id,))

    def contributions(self,goal_id):
        with self.connect() as db:return pd.read_sql_query('SELECT * FROM goal_contributions WHERE goal_id=? ORDER BY date,id',db,params=[int(goal_id)])

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
            row=db.execute('SELECT kind,category_source FROM transactions WHERE id=?',(txid,)).fetchone()
            if not row or row['category_source']!='待确认':return False
            permitted=['工资','生活费','其他收入'] if row['kind']=='收入' else [c for c in CATEGORIES if c not in ['工资','生活费','其他收入','转账']]
            if category not in permitted:raise ValueError('模型分类与收支类型冲突')
            db.execute('UPDATE transactions SET category=?,category_source=? WHERE id=?',(category,'模型建议（需核对）',txid))
            return True

    def add_manual_transaction(self,when,merchant,amount_cents,kind,category,description=''):
        from .importer import parse_statement
        from .categories import CATEGORIES
        import csv,io,uuid
        if not isinstance(amount_cents,int) or amount_cents<=0:raise ValueError('金额必须大于零')
        if category not in CATEGORIES:raise ValueError('无效分类')
        if kind=='转账' and category!='转账':raise ValueError('转账须使用转账分类')
        external='MANUAL-'+uuid.uuid4().hex
        stream=io.StringIO();writer=csv.writer(stream);writer.writerow(['交易ID','日期','商户','描述','金额','类型','平台','币种'])
        writer.writerow([external,when,merchant,description,f'{amount_cents//100}.{amount_cents%100:02d}',kind,'手动','CNY'])
        result=parse_statement(stream.getvalue().encode('utf-8-sig'),'manual.csv')
        if not result.rows:raise ValueError(result.issues[0]['原因'] if result.issues else '无效交易')
        row=result.rows[0];row.update(category=category,category_source='用户修正',source_file='手动记账')
        self.import_rows([row]);return row['id']

    def merchant_rules(self):
        with self.connect() as db:return pd.read_sql_query('SELECT * FROM merchant_rules ORDER BY merchant,kind',db)

    def delete_merchant_rule(self,merchant,kind):
        with self.connect() as db:db.execute('DELETE FROM merchant_rules WHERE merchant=? AND kind=?',(merchant,kind))

    def batch_category(self,ids,category):
        from .categories import CATEGORIES
        import json
        if category not in CATEGORIES:raise ValueError('无效分类')
        snapshot=[]
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS transaction_edits(id INTEGER PRIMARY KEY,transaction_id TEXT,old_values TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP)')
            for txid in dict.fromkeys(ids):
                old=db.execute('SELECT * FROM transactions WHERE id=?',(txid,)).fetchone()
                if old is None:continue
                snapshot.append(dict(id=txid,category=old['category'],source=old['category_source'],new=category))
                db.execute('INSERT INTO transaction_edits(transaction_id,old_values) VALUES (?,?)',(txid,json.dumps(dict(old),ensure_ascii=False)))
                db.execute('UPDATE transactions SET category=?,category_source=? WHERE id=?',(category,'用户修正',txid))
        return snapshot

    def undo_category_batch(self,snapshot):
        count=0
        with self.connect() as db:
            for row in snapshot:
                count+=db.execute('UPDATE transactions SET category=?,category_source=? WHERE id=? AND category=? AND category_source=?',(row['category'],row['source'],row['id'],row['new'],'用户修正')).rowcount
        return count

    def copy_budgets(self,source,target,overwrite=False):
        import re
        if any(not re.fullmatch(r'20\d{2}-(0[1-9]|1[0-2])',m) for m in [source,target]):raise ValueError('无效月份')
        with self.connect() as db:
            rows=db.execute('SELECT category,amount_cents FROM budgets WHERE month=?',(source,)).fetchall()
            operation='REPLACE' if overwrite else 'IGNORE'
            db.executemany(f'INSERT OR {operation} INTO budgets(month,category,amount_cents) VALUES (?,?,?)',[(target,r['category'],r['amount_cents']) for r in rows])
        return len(rows)
