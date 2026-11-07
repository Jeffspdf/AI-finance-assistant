BEGIN TRANSACTION;
CREATE TABLE accounts(id TEXT PRIMARY KEY,channel TEXT NOT NULL,type TEXT NOT NULL CHECK(type IN ('wallet','debit','credit')),label TEXT NOT NULL,last4 TEXT NOT NULL DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO "accounts" VALUES('AC-W','微信','wallet','合成微信','','2026-11-07 15:39:32');
INSERT INTO "accounts" VALUES('AC-B','银行','debit','合成银行卡','1234','2026-11-07 15:39:32');
CREATE TABLE audit_events(id INTEGER PRIMARY KEY,event TEXT NOT NULL,entity_id TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO "audit_events" VALUES(1,'frozen_v4_fixture','GP','{"synthetic": true}','2026-11-07 15:39:32');
INSERT INTO "audit_events" VALUES(2,'refund_confirmed','RL-4e7707e518954dd7a8cc793c3b812278','{"old": null, "new": {"id": "RL-4e7707e518954dd7a8cc793c3b812278", "refund_txn_id": "R-W", "original_txn_id": "P-W", "amount_cents": 3000, "status": "confirmed", "reason": "用户核对独立退款与原消费来源后确认", "confidence": "B", "algorithm_version": "m2-refund-1", "context_json": "{\"purchase\": {\"event_key\": \"purchase_duplicate:GP\", \"group\": {\"algorithm_version\": \"m1a-2\", \"decided_at\": \"2026-10-02 12:01:00\", \"decided_by\": \"user\", \"id\": \"GP\", \"kind\": \"purchase_duplicate\", \"roles\": {\"P-B\": \"funding\", \"P-W\": \"purchase\"}, \"status\": \"confirmed\"}, \"member_ids\": [\"P-B\", \"P-W\"], \"members\": [{\"account_id\": \"AC-B\", \"amount_cents\": 10000, \"category\": \"餐饮\", \"channel\": \"银行\", \"currency\": \"CNY\", \"date\": \"2026-09-01 12:00:30\", \"description\": \"合成餐厅微信支付\", \"external_id\": \"EXT-P-B\", \"id\": \"P-B\", \"import_batch_id\": \"B-B\", \"kind\": \"支出\", \"merchant\": \"财付通\", \"raw_row_id\": \"DOC-B-1\", \"source_document_id\": \"DOC-B\", \"source_file\": \"合成银行.xlsx\", \"source_row\": 1, \"status\": \"normal\"}, {\"account_id\": \"AC-W\", \"amount_cents\": 10000, \"category\": \"餐饮\", \"channel\": \"微信\", \"currency\": \"CNY\", \"date\": \"2026-09-01 12:00:00\", \"description\": \"午餐\", \"external_id\": \"EXT-P-W\", \"id\": \"P-W\", \"import_batch_id\": \"B-W\", \"kind\": \"支出\", \"merchant\": \"合成餐厅\", \"raw_row_id\": \"DOC-W-1\", \"source_document_id\": \"DOC-W\", \"source_file\": \"合成微信.csv\", \"source_row\": 1, \"status\": \"normal\"}], \"representative_id\": \"P-W\"}, \"refund\": {\"event_key\": \"refund:R-W\", \"group\": null, \"member_ids\": [\"R-W\"], \"members\": [{\"account_id\": \"AC-W\", \"amount_cents\": 3000, \"category\": \"其他收入\", \"channel\": \"微信\", \"currency\": \"CNY\", \"date\": \"2026-10-01 09:00:00\", \"description\": \"部分退款\", \"external_id\": \"EXT-R-W\", \"id\": \"R-W\", \"import_batch_id\": \"B-W\", \"kind\": \"收入\", \"merchant\": \"合成餐厅\", \"raw_row_id\": \"DOC-W-2\", \"source_document_id\": \"DOC-W\", \"source_file\": \"合成微信.csv\", \"source_row\": 2, \"status\": \"refund_pending\"}], \"representative_id\": \"R-W\"}, \"version\": 1}", "created_at": "2026-11-07 15:39:32", "decided_at": "2026-11-07 15:39:32", "decided_by": "user"}, "algorithm_version": "m2-refund-1"}','2026-11-07 15:39:32');
CREATE TABLE budgets(month TEXT NOT NULL,category TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),PRIMARY KEY(month,category));
INSERT INTO "budgets" VALUES('2026-09','餐饮',6000);
CREATE TABLE funding_links(id TEXT PRIMARY KEY,wallet_account_id TEXT NOT NULL REFERENCES accounts(id),bank_account_id TEXT NOT NULL REFERENCES accounts(id),active_from TEXT NOT NULL,active_to TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO "funding_links" VALUES('FL-1','AC-W','AC-B','2026-01-01',NULL,'2026-01-01 00:00:00');
CREATE TABLE goal_contributions(id INTEGER PRIMARY KEY AUTOINCREMENT,goal_id INTEGER NOT NULL,date TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>0),created_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO "goal_contributions" VALUES(1,1,'2026-02-01',5000,'2026-11-07 15:39:32');
CREATE TABLE goals(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,target_cents INTEGER NOT NULL,saved_cents INTEGER NOT NULL,start_date TEXT NOT NULL,deadline TEXT NOT NULL,income_cents INTEGER NOT NULL,budget_cents INTEGER NOT NULL);
INSERT INTO "goals" VALUES(1,'合成旧目标',200000,10000,'2026-01-01','2027-01-01',300000,200000);
CREATE TABLE import_batches(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES source_documents(id),account_id TEXT REFERENCES accounts(id),parser_version TEXT NOT NULL,started_at TEXT DEFAULT CURRENT_TIMESTAMP,finished_at TEXT,status TEXT NOT NULL,added INTEGER NOT NULL DEFAULT 0,duplicates INTEGER NOT NULL DEFAULT 0,conflicts INTEGER NOT NULL DEFAULT 0,issues_json TEXT NOT NULL DEFAULT '[]');
INSERT INTO "import_batches" VALUES('B-W','DOC-W','AC-W','frozen-v4-synthetic','2026-10-02 12:00:00','2026-10-02 12:00:00','committed',3,0,0,'[]');
INSERT INTO "import_batches" VALUES('B-B','DOC-B','AC-B','frozen-v4-synthetic','2026-10-02 12:00:00','2026-10-02 12:00:00','committed',1,0,0,'[]');
CREATE TABLE ledger_settings (
      key TEXT PRIMARY KEY,value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CHECK(key<>'spending_basis' OR value IN ('cash','original_purchase_month')));
INSERT INTO "ledger_settings" VALUES('spending_basis','cash','2026-11-07 15:39:32');
CREATE TABLE link_groups(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,confidence TEXT NOT NULL,algorithm_version TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,decided_at TEXT,decided_by TEXT);
INSERT INTO "link_groups" VALUES('GP','purchase_duplicate','confirmed','合成已确认消费','A','m1a-2','2026-10-02 12:00:00','2026-10-02 12:01:00','user');
CREATE TABLE link_members(group_id TEXT NOT NULL REFERENCES link_groups(id),transaction_id TEXT NOT NULL REFERENCES transactions(id),role TEXT NOT NULL CHECK(role IN ('purchase','funding','refund')),PRIMARY KEY(group_id,transaction_id));
INSERT INTO "link_members" VALUES('GP','P-W','purchase');
INSERT INTO "link_members" VALUES('GP','P-B','funding');
CREATE TABLE merchant_rules(merchant TEXT NOT NULL,kind TEXT NOT NULL,category TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(merchant,kind));
INSERT INTO "merchant_rules" VALUES('合成餐厅','支出','餐饮','2026-11-07 15:39:32');
CREATE TABLE raw_rows(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES source_documents(id),row_no INTEGER NOT NULL,raw_json TEXT NOT NULL,parse_status TEXT NOT NULL,UNIQUE(document_id,row_no));
INSERT INTO "raw_rows" VALUES('DOC-W-1','DOC-W',1,'{"synthetic": true}','normal');
INSERT INTO "raw_rows" VALUES('DOC-W-2','DOC-W',2,'{"synthetic": true}','normal');
INSERT INTO "raw_rows" VALUES('DOC-W-3','DOC-W',3,'{"synthetic": true}','normal');
INSERT INTO "raw_rows" VALUES('DOC-B-1','DOC-B',1,'{"synthetic": true}','normal');
CREATE TABLE refund_links (
      id TEXT PRIMARY KEY,
      refund_txn_id TEXT NOT NULL UNIQUE REFERENCES transactions(id),
      original_txn_id TEXT NOT NULL REFERENCES transactions(id),
      amount_cents INTEGER NOT NULL CHECK(typeof(amount_cents)='integer' AND amount_cents>0),
      status TEXT NOT NULL CHECK(status IN ('candidate','confirmed','rejected','needs_review')),
      reason TEXT NOT NULL,
      confidence TEXT NOT NULL CHECK(confidence IN ('A','B','C')),
      algorithm_version TEXT NOT NULL,
      context_json TEXT NOT NULL DEFAULT '{}',
      created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      decided_at TEXT,
      decided_by TEXT,
      CHECK(refund_txn_id<>original_txn_id));
INSERT INTO "refund_links" VALUES('RL-4e7707e518954dd7a8cc793c3b812278','R-W','P-W',3000,'confirmed','用户核对独立退款与原消费来源后确认','B','m2-refund-1','{"purchase": {"event_key": "purchase_duplicate:GP", "group": {"algorithm_version": "m1a-2", "decided_at": "2026-10-02 12:01:00", "decided_by": "user", "id": "GP", "kind": "purchase_duplicate", "roles": {"P-B": "funding", "P-W": "purchase"}, "status": "confirmed"}, "member_ids": ["P-B", "P-W"], "members": [{"account_id": "AC-B", "amount_cents": 10000, "category": "餐饮", "channel": "银行", "currency": "CNY", "date": "2026-09-01 12:00:30", "description": "合成餐厅微信支付", "external_id": "EXT-P-B", "id": "P-B", "import_batch_id": "B-B", "kind": "支出", "merchant": "财付通", "raw_row_id": "DOC-B-1", "source_document_id": "DOC-B", "source_file": "合成银行.xlsx", "source_row": 1, "status": "normal"}, {"account_id": "AC-W", "amount_cents": 10000, "category": "餐饮", "channel": "微信", "currency": "CNY", "date": "2026-09-01 12:00:00", "description": "午餐", "external_id": "EXT-P-W", "id": "P-W", "import_batch_id": "B-W", "kind": "支出", "merchant": "合成餐厅", "raw_row_id": "DOC-W-1", "source_document_id": "DOC-W", "source_file": "合成微信.csv", "source_row": 1, "status": "normal"}], "representative_id": "P-W"}, "refund": {"event_key": "refund:R-W", "group": null, "member_ids": ["R-W"], "members": [{"account_id": "AC-W", "amount_cents": 3000, "category": "其他收入", "channel": "微信", "currency": "CNY", "date": "2026-10-01 09:00:00", "description": "部分退款", "external_id": "EXT-R-W", "id": "R-W", "import_batch_id": "B-W", "kind": "收入", "merchant": "合成餐厅", "raw_row_id": "DOC-W-2", "source_document_id": "DOC-W", "source_file": "合成微信.csv", "source_row": 2, "status": "refund_pending"}], "representative_id": "R-W"}, "version": 1}','2026-11-07 15:39:32','2026-11-07 15:39:32','user');
CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,checksum TEXT NOT NULL);
INSERT INTO "schema_migrations" VALUES(4,'2026-11-04 00:00:00','3d9f13acb3466e8dd9201768cad876f374292d9d4fbf2d0a79ebfb4f19053a93');
INSERT INTO "schema_migrations" VALUES(5,'2026-11-07 15:39:32','db4d84a66b90a0fd7cfa95f2af74a34c62290eb911301baff5904f2ba64243cc');
CREATE TABLE source_documents(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,channel TEXT NOT NULL,account_id TEXT REFERENCES accounts(id),parser_version TEXT NOT NULL,imported_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO "source_documents" VALUES('DOC-W','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa','合成微信.csv','微信','AC-W','frozen-v4-synthetic','2026-11-07 15:39:32');
INSERT INTO "source_documents" VALUES('DOC-B','bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb','合成银行.xlsx','银行','AC-B','frozen-v4-synthetic','2026-11-07 15:39:32');
CREATE TABLE transaction_edits(id INTEGER PRIMARY KEY,transaction_id TEXT,old_values TEXT,changed_at TEXT DEFAULT CURRENT_TIMESTAMP);
INSERT INTO "transaction_edits" VALUES(1,'P-W','{"category": "其他支出"}','2026-11-07 15:39:32');
CREATE TABLE transactions (
      id TEXT PRIMARY KEY, external_id TEXT NOT NULL, date TEXT NOT NULL,
      merchant TEXT NOT NULL, description TEXT NOT NULL,
      amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
      kind TEXT NOT NULL CHECK(kind IN ('收入','支出','转账')), category TEXT NOT NULL,
      category_source TEXT NOT NULL, channel TEXT NOT NULL, source_file TEXT NOT NULL,
      source_row INTEGER NOT NULL, signature TEXT NOT NULL UNIQUE, source_document_id TEXT REFERENCES source_documents(id), raw_row_id TEXT REFERENCES raw_rows(id), account_id TEXT REFERENCES accounts(id), link_group_id TEXT, currency TEXT NOT NULL DEFAULT 'CNY', status TEXT NOT NULL DEFAULT 'legacy', import_batch_id TEXT REFERENCES import_batches(id));
INSERT INTO "transactions" VALUES('P-W','EXT-P-W','2026-09-01 12:00:00','合成餐厅','午餐',10000,'支出','餐饮','用户修正','微信','合成微信.csv',1,'SIG-P-W','DOC-W','DOC-W-1','AC-W','GP','CNY','normal','B-W');
INSERT INTO "transactions" VALUES('P-B','EXT-P-B','2026-09-01 12:00:30','财付通','合成餐厅微信支付',10000,'支出','餐饮','用户修正','银行','合成银行.xlsx',1,'SIG-P-B','DOC-B','DOC-B-1','AC-B','GP','CNY','normal','B-B');
INSERT INTO "transactions" VALUES('R-W','EXT-R-W','2026-10-01 09:00:00','合成餐厅','部分退款',3000,'收入','其他收入','用户修正','微信','合成微信.csv',2,'SIG-R-W','DOC-W','DOC-W-2','AC-W',NULL,'CNY','refund_pending','B-W');
INSERT INTO "transactions" VALUES('T-W','EXT-T-W','2026-10-02 10:00:00','自己的账户','转账',50000,'转账','转账','用户修正','微信','合成微信.csv',3,'SIG-T-W','DOC-W','DOC-W-3','AC-W',NULL,'CNY','normal','B-W');
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
CREATE INDEX idx_refund_original_status ON refund_links(original_txn_id,status);
CREATE INDEX idx_refund_status ON refund_links(status);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('goals',1);
INSERT INTO "sqlite_sequence" VALUES('goal_contributions',1);
COMMIT;
PRAGMA user_version=5;
