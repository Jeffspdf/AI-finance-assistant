"""Frozen schema-4 fixture from v0.8.5 / d56850c, without real data."""
import sqlite3,json
from contextlib import closing

V4_CHECKSUM='3d9f13acb3466e8dd9201768cad876f374292d9d4fbf2d0a79ebfb4f19053a93'

SCHEMA_V4_SQL = r"""
CREATE TABLE accounts(id TEXT PRIMARY KEY,channel TEXT NOT NULL,type TEXT NOT NULL CHECK(type IN ('wallet','debit','credit')),label TEXT NOT NULL,last4 TEXT NOT NULL DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE audit_events(id INTEGER PRIMARY KEY,event TEXT NOT NULL,entity_id TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE budgets(month TEXT NOT NULL,category TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),PRIMARY KEY(month,category));
CREATE TABLE funding_links(id TEXT PRIMARY KEY,wallet_account_id TEXT NOT NULL REFERENCES accounts(id),bank_account_id TEXT NOT NULL REFERENCES accounts(id),active_from TEXT NOT NULL,active_to TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE goal_contributions(id INTEGER PRIMARY KEY AUTOINCREMENT,goal_id INTEGER NOT NULL,date TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>0),created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE goals(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,target_cents INTEGER NOT NULL,saved_cents INTEGER NOT NULL,start_date TEXT NOT NULL,deadline TEXT NOT NULL,income_cents INTEGER NOT NULL,budget_cents INTEGER NOT NULL);
CREATE TABLE import_batches(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES source_documents(id),account_id TEXT REFERENCES accounts(id),parser_version TEXT NOT NULL,started_at TEXT DEFAULT CURRENT_TIMESTAMP,finished_at TEXT,status TEXT NOT NULL,added INTEGER NOT NULL DEFAULT 0,duplicates INTEGER NOT NULL DEFAULT 0,conflicts INTEGER NOT NULL DEFAULT 0,issues_json TEXT NOT NULL DEFAULT '[]');
CREATE TABLE link_groups(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,confidence TEXT NOT NULL,algorithm_version TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,decided_at TEXT,decided_by TEXT);
CREATE TABLE link_members(group_id TEXT NOT NULL REFERENCES link_groups(id),transaction_id TEXT NOT NULL REFERENCES transactions(id),role TEXT NOT NULL CHECK(role IN ('purchase','funding','refund')),PRIMARY KEY(group_id,transaction_id));
CREATE TABLE merchant_rules(merchant TEXT NOT NULL,kind TEXT NOT NULL,category TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(merchant,kind));
CREATE TABLE raw_rows(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES source_documents(id),row_no INTEGER NOT NULL,raw_json TEXT NOT NULL,parse_status TEXT NOT NULL,UNIQUE(document_id,row_no));
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,checksum TEXT NOT NULL);
CREATE TABLE source_documents(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,channel TEXT NOT NULL,account_id TEXT REFERENCES accounts(id),parser_version TEXT NOT NULL,imported_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE transactions (
      id TEXT PRIMARY KEY, external_id TEXT NOT NULL, date TEXT NOT NULL,
      merchant TEXT NOT NULL, description TEXT NOT NULL,
      amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
      kind TEXT NOT NULL CHECK(kind IN ('收入','支出','转账')), category TEXT NOT NULL,
      category_source TEXT NOT NULL, channel TEXT NOT NULL, source_file TEXT NOT NULL,
      source_row INTEGER NOT NULL, signature TEXT NOT NULL UNIQUE, source_document_id TEXT REFERENCES source_documents(id), raw_row_id TEXT REFERENCES raw_rows(id), account_id TEXT REFERENCES accounts(id), link_group_id TEXT, currency TEXT NOT NULL DEFAULT 'CNY', status TEXT NOT NULL DEFAULT 'legacy', import_batch_id TEXT REFERENCES import_batches(id));
CREATE INDEX idx_batch ON transactions(import_batch_id);
CREATE INDEX idx_contribution_goal ON goal_contributions(goal_id);
CREATE INDEX idx_date ON transactions(date);
CREATE INDEX idx_link_transaction ON link_members(transaction_id);
CREATE INDEX idx_raw_document ON raw_rows(document_id,row_no);
CREATE INDEX idx_reconcile ON transactions(currency,kind,amount_cents,date);
CREATE TRIGGER group_cache_status AFTER UPDATE OF status ON link_groups BEGIN
      UPDATE transactions SET link_group_id=NULL WHERE link_group_id=NEW.id;
      UPDATE transactions SET link_group_id=NEW.id WHERE NEW.status='confirmed'
        AND id IN (SELECT transaction_id FROM link_members WHERE group_id=NEW.id); END;
CREATE TRIGGER group_unique_confirm BEFORE UPDATE OF status ON link_groups
    WHEN NEW.status='confirmed' BEGIN
      SELECT RAISE(ABORT,'transaction already confirmed') WHERE EXISTS (
        SELECT 1 FROM link_members a JOIN link_members b ON a.transaction_id=b.transaction_id
        JOIN link_groups g ON g.id=b.group_id
        WHERE a.group_id=NEW.id AND b.group_id<>NEW.id AND g.status='confirmed'); END;
CREATE TRIGGER member_cache_delete AFTER DELETE ON link_members
    BEGIN UPDATE transactions SET link_group_id=NULL WHERE id=OLD.transaction_id AND link_group_id=OLD.group_id; END;
CREATE TRIGGER member_cache_insert AFTER INSERT ON link_members
    WHEN (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'
    BEGIN UPDATE transactions SET link_group_id=NEW.group_id WHERE id=NEW.transaction_id; END;
CREATE TRIGGER member_cache_update AFTER UPDATE OF group_id,transaction_id ON link_members BEGIN
      UPDATE transactions SET link_group_id=NULL WHERE id=OLD.transaction_id AND link_group_id=OLD.group_id;
      UPDATE transactions SET link_group_id=NEW.group_id WHERE id=NEW.transaction_id
        AND (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'; END;
CREATE TRIGGER member_unique_insert BEFORE INSERT ON link_members
    WHEN (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'
    BEGIN SELECT RAISE(ABORT,'transaction already confirmed') WHERE EXISTS (
      SELECT 1 FROM link_members m JOIN link_groups g ON g.id=m.group_id
      WHERE m.transaction_id=NEW.transaction_id AND g.status='confirmed' AND g.id<>NEW.group_id); END;
CREATE TRIGGER member_unique_update BEFORE UPDATE OF group_id,transaction_id ON link_members
    WHEN (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'
    BEGIN SELECT RAISE(ABORT,'transaction already confirmed') WHERE EXISTS (
      SELECT 1 FROM link_members m JOIN link_groups g ON g.id=m.group_id
      WHERE m.transaction_id=NEW.transaction_id AND g.status='confirmed' AND g.id<>NEW.group_id); END;
INSERT INTO schema_migrations(version,applied_at,checksum) VALUES (4,'2026-11-04 00:00:00','3d9f13acb3466e8dd9201768cad876f374292d9d4fbf2d0a79ebfb4f19053a93');
PRAGMA user_version=4;
"""

def create_v4(path,*,populated=True):
    with closing(sqlite3.connect(path)) as db:
        db.executescript(SCHEMA_V4_SQL)
        if populated:
            with db:_populate(db)


def _populate(db):
    db.execute('PRAGMA foreign_keys=ON')
    db.executemany('INSERT INTO accounts(id,channel,type,label,last4) VALUES (?,?,?,?,?)',[
        ('AC-W','微信','wallet','合成微信',''),('AC-B','银行','debit','合成银行卡','1234')])
    db.execute("INSERT INTO funding_links VALUES ('FL-1','AC-W','AC-B','2026-01-01',NULL,'2026-01-01 00:00:00')")
    db.executemany('INSERT INTO source_documents(id,sha256,filename,channel,account_id,parser_version) VALUES (?,?,?,?,?,?)',[
        ('DOC-W','a'*64,'合成微信.csv','微信','AC-W','frozen-v4-synthetic'),
        ('DOC-B','b'*64,'合成银行.xlsx','银行','AC-B','frozen-v4-synthetic')])
    for doc,rows in [('DOC-W',3),('DOC-B',1)]:
        for number in range(1,rows+1):
            db.execute('INSERT INTO raw_rows VALUES (?,?,?,?,?)',(doc+'-'+str(number),doc,number,json.dumps({'synthetic':True}), 'normal'))
    db.executemany('''INSERT INTO import_batches(id,document_id,account_id,parser_version,started_at,finished_at,status,added)
        VALUES (?,?,?,'frozen-v4-synthetic','2026-10-02 12:00:00','2026-10-02 12:00:00','committed',?)''',[
        ('B-W','DOC-W','AC-W',3),('B-B','DOC-B','AC-B',1)])
    records=[
        ('P-W','2026-09-01 12:00:00','合成餐厅','午餐',10000,'支出','餐饮','微信','DOC-W','AC-W','B-W',1,'normal'),
        ('P-B','2026-09-01 12:00:30','财付通','合成餐厅微信支付',10000,'支出','餐饮','银行','DOC-B','AC-B','B-B',1,'normal'),
        ('R-W','2026-10-01 09:00:00','合成餐厅','部分退款',3000,'收入','其他收入','微信','DOC-W','AC-W','B-W',2,'refund_pending'),
        ('T-W','2026-10-02 10:00:00','自己的账户','转账',50000,'转账','转账','微信','DOC-W','AC-W','B-W',3,'normal'),
    ]
    for identifier,when,merchant,description,amount,kind,category,channel,doc,account,batch,number,status in records:
        db.execute('''INSERT INTO transactions(id,external_id,date,merchant,description,amount_cents,kind,category,category_source,
            channel,source_file,source_row,signature,source_document_id,raw_row_id,account_id,currency,status,import_batch_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier,'EXT-'+identifier,when,merchant,description,amount,kind,category,'用户修正',channel,
             '合成微信.csv' if doc=='DOC-W' else '合成银行.xlsx',number,'SIG-'+identifier,doc,doc+'-'+str(number),account,'CNY',status,batch))
    db.execute("INSERT INTO link_groups VALUES ('GP','purchase_duplicate','confirmed','合成已确认消费','A','m1a-2','2026-10-02 12:00:00','2026-10-02 12:01:00','user')")
    db.executemany("INSERT INTO link_members VALUES ('GP',?,?)",[('P-W','purchase'),('P-B','funding')])
    db.execute("INSERT INTO budgets VALUES ('2026-09','餐饮',6000)")
    db.execute("INSERT INTO goals VALUES (1,'合成旧目标',200000,10000,'2026-01-01','2027-01-01',300000,200000)")
    db.execute("INSERT INTO goal_contributions(goal_id,date,amount_cents) VALUES (1,'2026-02-01',5000)")
    db.execute("INSERT INTO merchant_rules(merchant,kind,category) VALUES ('合成餐厅','支出','餐饮')")
    db.execute('CREATE TABLE transaction_edits(id INTEGER PRIMARY KEY,transaction_id TEXT,old_values TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP)')
    db.execute("INSERT INTO transaction_edits(transaction_id,old_values) VALUES ('P-W',?)",(json.dumps({'category':'其他支出'},ensure_ascii=False),))
    db.execute("INSERT INTO audit_events(event,entity_id,detail) VALUES ('frozen_v4_fixture','GP',?)",(json.dumps({'synthetic':True}),))
