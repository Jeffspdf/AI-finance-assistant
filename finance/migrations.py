"""Finite SQLite upgrades. Never overwrite a database from a newer program."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import sqlite3
import uuid
from contextlib import closing

SCHEMA_VERSION = 6

BASE = [
    '''CREATE TABLE IF NOT EXISTS transactions (
      id TEXT PRIMARY KEY, external_id TEXT NOT NULL, date TEXT NOT NULL,
      merchant TEXT NOT NULL, description TEXT NOT NULL,
      amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
      kind TEXT NOT NULL CHECK(kind IN ('收入','支出','转账')), category TEXT NOT NULL,
      category_source TEXT NOT NULL, channel TEXT NOT NULL, source_file TEXT NOT NULL,
      source_row INTEGER NOT NULL, signature TEXT NOT NULL UNIQUE)''',
    'CREATE TABLE IF NOT EXISTS budgets(month TEXT NOT NULL,category TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>=0),PRIMARY KEY(month,category))',
    'CREATE TABLE IF NOT EXISTS goals(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,target_cents INTEGER NOT NULL,saved_cents INTEGER NOT NULL,start_date TEXT NOT NULL,deadline TEXT NOT NULL,income_cents INTEGER NOT NULL,budget_cents INTEGER NOT NULL)',
    'CREATE TABLE IF NOT EXISTS goal_contributions(id INTEGER PRIMARY KEY AUTOINCREMENT,goal_id INTEGER NOT NULL,date TEXT NOT NULL,amount_cents INTEGER NOT NULL CHECK(amount_cents>0),created_at TEXT DEFAULT CURRENT_TIMESTAMP)',
    'CREATE TABLE IF NOT EXISTS merchant_rules(merchant TEXT NOT NULL,kind TEXT NOT NULL,category TEXT NOT NULL,updated_at TEXT DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(merchant,kind))',
    'CREATE INDEX IF NOT EXISTS idx_date ON transactions(date)',
    'CREATE INDEX IF NOT EXISTS idx_contribution_goal ON goal_contributions(goal_id)',
]

M1A = [
    "CREATE TABLE accounts(id TEXT PRIMARY KEY,channel TEXT NOT NULL,type TEXT NOT NULL CHECK(type IN ('wallet','debit','credit')),label TEXT NOT NULL,last4 TEXT NOT NULL DEFAULT '',created_at TEXT DEFAULT CURRENT_TIMESTAMP)",
    'CREATE TABLE funding_links(id TEXT PRIMARY KEY,wallet_account_id TEXT NOT NULL REFERENCES accounts(id),bank_account_id TEXT NOT NULL REFERENCES accounts(id),active_from TEXT NOT NULL,active_to TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP)',
    'CREATE TABLE source_documents(id TEXT PRIMARY KEY,sha256 TEXT NOT NULL UNIQUE,filename TEXT NOT NULL,channel TEXT NOT NULL,account_id TEXT REFERENCES accounts(id),parser_version TEXT NOT NULL,imported_at TEXT DEFAULT CURRENT_TIMESTAMP)',
    'CREATE TABLE raw_rows(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES source_documents(id),row_no INTEGER NOT NULL,raw_json TEXT NOT NULL,parse_status TEXT NOT NULL,UNIQUE(document_id,row_no))',
    "CREATE TABLE import_batches(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES source_documents(id),account_id TEXT REFERENCES accounts(id),parser_version TEXT NOT NULL,started_at TEXT DEFAULT CURRENT_TIMESTAMP,finished_at TEXT,status TEXT NOT NULL,added INTEGER NOT NULL DEFAULT 0,duplicates INTEGER NOT NULL DEFAULT 0,conflicts INTEGER NOT NULL DEFAULT 0,issues_json TEXT NOT NULL DEFAULT '[]')",
    'CREATE TABLE link_groups(id TEXT PRIMARY KEY,kind TEXT NOT NULL,status TEXT NOT NULL,reason TEXT NOT NULL,confidence TEXT NOT NULL,algorithm_version TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,decided_at TEXT,decided_by TEXT)',
    "CREATE TABLE link_members(group_id TEXT NOT NULL REFERENCES link_groups(id),transaction_id TEXT NOT NULL REFERENCES transactions(id),role TEXT NOT NULL CHECK(role IN ('purchase','funding','refund')),PRIMARY KEY(group_id,transaction_id))",
    'CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY,applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,checksum TEXT NOT NULL)',
    'CREATE TABLE audit_events(id INTEGER PRIMARY KEY,event TEXT NOT NULL,entity_id TEXT NOT NULL,detail TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP)',
    'CREATE INDEX idx_raw_document ON raw_rows(document_id,row_no)',
    'CREATE INDEX idx_link_transaction ON link_members(transaction_id)',
]

COLUMNS = {
    'source_document_id': 'TEXT REFERENCES source_documents(id)',
    'raw_row_id': 'TEXT REFERENCES raw_rows(id)',
    'account_id': 'TEXT REFERENCES accounts(id)',
    'link_group_id': 'TEXT',  # Cache only. link_members is the source of truth.
    'currency': "TEXT NOT NULL DEFAULT 'CNY'",
    'status': "TEXT NOT NULL DEFAULT 'legacy'",
    'import_batch_id': 'TEXT REFERENCES import_batches(id)',
}

TRIGGERS = [
    '''CREATE TRIGGER member_unique_insert BEFORE INSERT ON link_members
    WHEN (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'
    BEGIN SELECT RAISE(ABORT,'transaction already confirmed') WHERE EXISTS (
      SELECT 1 FROM link_members m JOIN link_groups g ON g.id=m.group_id
      WHERE m.transaction_id=NEW.transaction_id AND g.status='confirmed' AND g.id<>NEW.group_id); END''',
    '''CREATE TRIGGER group_unique_confirm BEFORE UPDATE OF status ON link_groups
    WHEN NEW.status='confirmed' BEGIN
      SELECT RAISE(ABORT,'transaction already confirmed') WHERE EXISTS (
        SELECT 1 FROM link_members a JOIN link_members b ON a.transaction_id=b.transaction_id
        JOIN link_groups g ON g.id=b.group_id
        WHERE a.group_id=NEW.id AND b.group_id<>NEW.id AND g.status='confirmed'); END''',
    '''CREATE TRIGGER member_cache_insert AFTER INSERT ON link_members
    WHEN (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'
    BEGIN UPDATE transactions SET link_group_id=NEW.group_id WHERE id=NEW.transaction_id; END''',
    '''CREATE TRIGGER member_cache_delete AFTER DELETE ON link_members
    BEGIN UPDATE transactions SET link_group_id=NULL WHERE id=OLD.transaction_id AND link_group_id=OLD.group_id; END''',
    '''CREATE TRIGGER member_unique_update BEFORE UPDATE OF group_id,transaction_id ON link_members
    WHEN (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'
    BEGIN SELECT RAISE(ABORT,'transaction already confirmed') WHERE EXISTS (
      SELECT 1 FROM link_members m JOIN link_groups g ON g.id=m.group_id
      WHERE m.transaction_id=NEW.transaction_id AND g.status='confirmed' AND g.id<>NEW.group_id); END''',
    '''CREATE TRIGGER member_cache_update AFTER UPDATE OF group_id,transaction_id ON link_members BEGIN
      UPDATE transactions SET link_group_id=NULL WHERE id=OLD.transaction_id AND link_group_id=OLD.group_id;
      UPDATE transactions SET link_group_id=NEW.group_id WHERE id=NEW.transaction_id
        AND (SELECT status FROM link_groups WHERE id=NEW.group_id)='confirmed'; END''',
    '''CREATE TRIGGER group_cache_status AFTER UPDATE OF status ON link_groups BEGIN
      UPDATE transactions SET link_group_id=NULL WHERE link_group_id=NEW.id;
      UPDATE transactions SET link_group_id=NEW.id WHERE NEW.status='confirmed'
        AND id IN (SELECT transaction_id FROM link_members WHERE group_id=NEW.id); END''',
]

# Version-4 statements/checksum above remain unchanged: this is an additive step.
M2 = [
    '''CREATE TABLE refund_links (
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
      CHECK(refund_txn_id<>original_txn_id))''',
    "CREATE INDEX idx_refund_original_status ON refund_links(original_txn_id,status)",
    "CREATE INDEX idx_refund_status ON refund_links(status)",
    '''CREATE TABLE ledger_settings (
      key TEXT PRIMARY KEY,value TEXT NOT NULL,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      CHECK(key<>'spending_basis' OR value IN ('cash','original_purchase_month')))''',
    "INSERT INTO ledger_settings(key,value) VALUES ('spending_basis','cash')",
]


def _upgrade_to_v4(db):
    for statement in BASE:db.execute(statement)
    for statement in M1A:db.execute(statement)
    for name,definition in COLUMNS.items():
        db.execute(f'ALTER TABLE transactions ADD COLUMN {name} {definition}')
    db.execute('CREATE INDEX idx_reconcile ON transactions(currency,kind,amount_cents,date)')
    db.execute('CREATE INDEX idx_batch ON transactions(import_batch_id)')
    for statement in TRIGGERS:db.execute(statement)
    checksum=sha256('\n'.join(BASE+M1A+[k+' '+v for k,v in COLUMNS.items()]+TRIGGERS).encode()).hexdigest()
    db.execute('INSERT INTO schema_migrations(version,checksum) VALUES (4,?)',(checksum,))
    db.execute('PRAGMA user_version=4')


M3 = [
    'ALTER TABLE accounts ADD COLUMN archived_at TEXT',
    'ALTER TABLE accounts ADD COLUMN updated_at TEXT',
]


def _require_v5_layout(db):
    _require_v4_layout(db)
    required={'refund_links':{'id','refund_txn_id','original_txn_id','amount_cents','status','context_json'},
              'ledger_settings':{'key','value','updated_at'},
              'accounts':{'id','channel','type','label','last4','created_at'}}
    for table,columns in required.items():
        if not columns<={r[1] for r in db.execute('PRAGMA table_info('+table+')')}:
            raise ValueError('模式5结构不完整，请核对或恢复备份；未执行模式6升级')
    if not db.execute("SELECT 1 FROM schema_migrations WHERE version=5 AND checksum<>''").fetchone():
        raise ValueError('模式5迁移记录缺失；未执行模式6升级')


def _require_v4_layout(db):
    required={'transactions','accounts','funding_links','source_documents','raw_rows',
              'import_batches','link_groups','link_members','schema_migrations','audit_events',
              'budgets','goals','goal_contributions','merchant_rules'}
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    columns={r[1] for r in db.execute('PRAGMA table_info(transactions)')}
    if not required<=tables or not set(COLUMNS)<=columns:
        raise ValueError('模式4结构不完整，请先核对或恢复备份；未执行模式5升级')
    if not db.execute('SELECT 1 FROM schema_migrations WHERE version=4 AND checksum<>?',('',)).fetchone():
        raise ValueError('模式4迁移记录缺失，请先核对备份；未执行模式5升级')


def assert_supported(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError(f'数据库版本 {version} 高于本程序支持的 {SCHEMA_VERSION}，请升级应用；未写入数据库。')
    return version


def inspect_database(path):
    path = Path(path).resolve()
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        version = assert_supported(db)
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('数据库完整性检查未通过')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {name: db.execute(f'SELECT COUNT(*) FROM {name}').fetchone()[0]
                  for name in ('transactions','budgets','goals','goal_contributions','import_batches','refund_links') if name in tables}
    return dict(version=version, bytes=path.stat().st_size, counts=counts)


def backup_database(path, destination=None):
    path = Path(path).resolve()
    if destination is None:
        folder = path.parent/'backups';folder.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        destination = folder/f'{path.stem}-v{inspect_database(path)["version"]}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3'
    destination = Path(destination).resolve()
    if destination == path or destination.exists():
        raise ValueError('备份目标必须是一个尚不存在的新文件')
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(destination)) as target:
            assert_supported(source)
            source.backup(target)
        inspect_database(destination)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def ensure_schema(path, hook=None):
    """hook is a test-only failure injector; DDL and data always roll back together."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    try:
        db.execute('PRAGMA foreign_keys=ON')
        version = assert_supported(db)
        if version == SCHEMA_VERSION:return None
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('迁移前数据库完整性检查失败')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise ValueError('迁移前外键完整性检查失败')
        db.execute('BEGIN IMMEDIATE')
        # Serialize migrations/writers before taking the consistent pre-DDL snapshot.
        version=assert_supported(db)
        if version==SCHEMA_VERSION:
            db.rollback();return None
        existing=db.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        backup=backup_database(path) if existing else None
        if version<4:_upgrade_to_v4(db);version=4
        if version<5:
            _require_v4_layout(db)
            for statement in M2:db.execute(statement)
            db.execute('INSERT INTO schema_migrations(version,checksum) VALUES (?,?)',(5,sha256('\n'.join(M2).encode()).hexdigest()))
            db.execute('PRAGMA user_version=5');version=5
        if version<6:
            _require_v5_layout(db)
            for statement in M3:db.execute(statement)
            db.execute('INSERT INTO schema_migrations(version,checksum) VALUES (?,?)',(6,sha256('\n'.join(M3).encode()).hexdigest()))
            db.execute('PRAGMA user_version=6')
        if hook:hook(db)
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':raise ValueError('迁移完整性检查失败')
        if db.execute('PRAGMA foreign_key_check').fetchone() is not None:raise ValueError('迁移外键完整性检查失败')
        db.commit()
        return backup
    except Exception:
        db.rollback();raise
    finally:db.close()
