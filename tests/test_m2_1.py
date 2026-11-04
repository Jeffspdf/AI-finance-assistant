"""M2-1: additive migration safety and read-only logical refund invariants."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import json,sqlite3
import pytest
import finance.migrations as migrations
from finance.migrations import ensure_schema,inspect_database,SCHEMA_VERSION
from finance.storage import Store
from m2_v4_fixture import create_v4,V4_CHECKSUM


def snapshot(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        version=db.execute('PRAGMA user_version').fetchone()[0]
        objects={r[0]:(r[1],r[2],r[3]) for r in db.execute('SELECT name,type,tbl_name,sql FROM sqlite_master')}
        rows={name:db.execute('SELECT * FROM "'+name+'" ORDER BY rowid').fetchall()
              for name,(kind,_,_) in objects.items() if kind=='table'}
    return dict(version=version,objects=objects,rows=rows)


def ledger(tmp_path):
    path=tmp_path/'ledger.sqlite3';create_v4(path);return Store(path)


def add_refund(store,identifier,amount,when='2026-10-02 12:00:00',channel='微信'):
    with store.connect() as db:
        db.execute('''INSERT INTO transactions(id,external_id,date,merchant,description,amount_cents,kind,category,
            category_source,channel,source_file,source_row,signature,currency,status)
            VALUES (?,?,?,'合成餐厅','独立退款',?,'收入','其他收入','待确认',?,'合成补充.csv',1,?,'CNY','refund_pending')''',
                   (identifier,'EXT-'+identifier,when,amount,channel,'SIG-'+identifier))
    return identifier


def save_confirmed(store,result,identifier,*,refund_id=None,original_id=None):
    with store.connect() as db:
        db.execute('''INSERT INTO refund_links(id,refund_txn_id,original_txn_id,amount_cents,status,reason,confidence,algorithm_version,context_json)
            VALUES (?,?,?,?,'confirmed','合成夹具确认','B',?,?)''',
                   (identifier,refund_id or result['refund_txn_id'],original_id or result['original_txn_id'],result['amount_cents'],result['algorithm_version'],result['context_json']))


def refund_group(store):
    add_refund(store,'R-B',3000,channel='银行')
    with store.connect() as db:
        db.execute("INSERT INTO link_groups VALUES ('GR','refund_duplicate','confirmed','合成同一退款','A','m2-refund-1','2026-10-02 12:00:00','2026-10-02 12:01:00','user')")
        db.executemany("INSERT INTO link_members VALUES ('GR',?,'refund')",[('R-W',),('R-B',)])


def test_v4_to_v5_preserves_all_old_rows_objects_history_and_gross(tmp_path):
    path=tmp_path/'ledger.sqlite3';create_v4(path);before=snapshot(path)
    backup=ensure_schema(path);after=snapshot(path)
    assert before['version']==4 and after['version']==SCHEMA_VERSION==5
    assert backup and snapshot(backup)==before
    assert set(after['rows'])-set(before['rows'])=={'refund_links','ledger_settings'}
    for name,obj in before['objects'].items():assert after['objects'][name]==obj
    for name,rows in before['rows'].items():
        if name=='schema_migrations':assert after['rows'][name][0]==rows[0]
        else:assert after['rows'][name]==rows
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()==[(4,),(5,)]
        assert db.execute('SELECT checksum FROM schema_migrations WHERE version=4').fetchone()[0]==V4_CHECKSUM
        assert db.execute("SELECT value FROM ledger_settings WHERE key='spending_basis'").fetchone()[0]=='cash'
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
    store=Store(path);frame=store.transactions()
    assert int(frame.loc[frame.kind=='支出','amount_cents'].sum())==10000
    assert store.pending_refunds()==dict(count=1,amount_cents=3000) and len(store.source_transactions())==4
    assert store.migration_backup is None and store.semantic_backup is None


def test_new_database_retains_frozen_v4_checksum_then_records_v5(tmp_path):
    store=Store(tmp_path/'new.sqlite3')
    with store.connect(read_only=True) as db:
        rows=dict(db.execute('SELECT version,checksum FROM schema_migrations'))
        assert set(rows)=={4,5} and rows[4]==V4_CHECKSUM
        assert len(rows[5])==64 and rows[5]!=rows[4]
    assert store.transactions().empty and store.migration_backup is None


def test_existing_v4_history_is_never_recomputed_or_replaced(tmp_path):
    path=tmp_path/'old.sqlite3';create_v4(path)
    with closing(sqlite3.connect(path)) as db:
        with db:db.execute("UPDATE schema_migrations SET checksum='historical-v4-signature',applied_at='2026-01-01' WHERE version=4")
    ensure_schema(path)
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT checksum,applied_at FROM schema_migrations WHERE version=4').fetchone()==('historical-v4-signature','2026-01-01')


@pytest.mark.parametrize('after_statement',range(len(migrations.M2)+1))
def test_each_m2_ddl_failure_rolls_back_to_complete_v4(tmp_path,monkeypatch,after_statement):
    path=tmp_path/'old.sqlite3';create_v4(path);before=snapshot(path)
    statements=migrations.M2
    monkeypatch.setattr(migrations,'M2',statements[:after_statement]+['SELECT m2_injected_failure()']+statements[after_statement:])
    with pytest.raises(sqlite3.OperationalError):ensure_schema(path)
    assert snapshot(path)==before
    backups=list((tmp_path/'backups').glob('*.sqlite3'));assert len(backups)==1 and snapshot(backups[0])==before


def test_failure_after_ddl_and_old_row_edit_rolls_back_everything(tmp_path):
    path=tmp_path/'old.sqlite3';create_v4(path);before=snapshot(path)
    def fail(db):
        db.execute("UPDATE transactions SET amount_cents=999 WHERE id='P-W'")
        raise RuntimeError('synthetic migration fault')
    with pytest.raises(RuntimeError,match='synthetic'):ensure_schema(path,hook=fail)
    assert snapshot(path)==before


def test_backup_failure_stops_before_any_ddl(tmp_path,monkeypatch):
    path=tmp_path/'old.sqlite3';create_v4(path);before=snapshot(path)
    def fail(*args,**kwargs):raise OSError('synthetic backup failure')
    monkeypatch.setattr(migrations,'backup_database',fail)
    with pytest.raises(OSError):ensure_schema(path)
    assert snapshot(path)==before


def test_corrupt_file_is_refused_without_changing_or_backing_it_up(tmp_path):
    path=tmp_path/'bad.sqlite3';path.write_bytes(b'synthetic non-database bytes');before=path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):ensure_schema(path)
    assert path.read_bytes()==before and not (tmp_path/'backups').exists()


@pytest.mark.parametrize('damage',['missing_table','missing_history','foreign_key'])
def test_incomplete_v4_is_not_partially_upgraded(tmp_path,damage):
    path=tmp_path/'old.sqlite3';create_v4(path)
    with closing(sqlite3.connect(path)) as db:
        with db:
            if damage=='missing_table':db.execute('DROP TABLE funding_links')
            elif damage=='missing_history':db.execute('DELETE FROM schema_migrations WHERE version=4')
            else:db.execute("UPDATE transactions SET account_id='missing-account' WHERE id='P-W'")
    before=snapshot(path)
    with pytest.raises(ValueError):ensure_schema(path)
    assert snapshot(path)==before


def test_future_v6_and_readonly_v4_refused_before_writes(tmp_path):
    path=tmp_path/'old.sqlite3';create_v4(path);before=path.read_bytes()
    with pytest.raises(ValueError,match='可写模式'):Store(path,read_only=True)
    assert path.read_bytes()==before and not (tmp_path/'backups').exists()
    with closing(sqlite3.connect(path)) as db:db.execute('PRAGMA user_version=6')
    before=path.read_bytes()
    for read_only in (False,True):
        with pytest.raises(ValueError,match='升级'):Store(path,read_only=read_only)
    assert path.read_bytes()==before and not (tmp_path/'backups').exists()


def test_v5_reopen_is_byte_idempotent_and_readonly_supported(tmp_path):
    store=ledger(tmp_path);path=Path(store.path);before=path.read_bytes()
    assert ensure_schema(path) is None
    reopened=Store(path);assert reopened.migration_backup is None
    readonly=Store(path,read_only=True)
    with readonly.connect() as db:
        assert db.execute('PRAGMA query_only').fetchone()[0]==1
        with pytest.raises(sqlite3.OperationalError):db.execute("UPDATE ledger_settings SET value='original_purchase_month'")
    assert path.read_bytes()==before and len(list((tmp_path/'backups').glob('*.sqlite3')))==1


def test_restore_v4_snapshot_into_v5_uses_staged_upgrade(tmp_path):
    store=ledger(tmp_path);old=store.migration_backup;original=snapshot(old)
    save_confirmed(store,store.validate_refund_link('R-W','P-W'),'RL-1')
    recovery=store.restore(old,confirmed=True)
    assert inspect_database(recovery)['version']==5 and inspect_database(recovery)['counts']['refund_links']==1
    assert inspect_database(store.path)['version']==5
    with store.connect(read_only=True) as db:assert db.execute('SELECT COUNT(*) FROM refund_links').fetchone()[0]==0
    current=snapshot(store.path)
    for name,rows in original['rows'].items():
        if name!='schema_migrations':assert current['rows'][name]==rows


def test_two_migrations_serialize_and_create_one_v4_backup(tmp_path):
    path=tmp_path/'old.sqlite3';create_v4(path)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:ensure_schema(path),range(2)))
    assert sum(r is not None for r in results)==1
    assert len(list((tmp_path/'backups').glob('*.sqlite3')))==1
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT version FROM schema_migrations ORDER BY version').fetchall()==[(4,),(5,)]


@pytest.mark.parametrize('field,value',[
    ('amount_cents',0),('amount_cents',-1),('amount_cents',1.5),('status','unknown'),('confidence','D'),
    ('refund_txn_id','missing'),('original_txn_id','missing'),('original_txn_id','R-W')])
def test_refund_link_sql_constraints_are_enforced(tmp_path,field,value):
    store=ledger(tmp_path)
    row=dict(id='RL',refund_txn_id='R-W',original_txn_id='P-W',amount_cents=3000,status='candidate',reason='synthetic',confidence='B',algorithm_version='m2-refund-1')
    row[field]=value
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db:
            db.execute('INSERT INTO refund_links('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')',list(row.values()))


def test_unique_refund_source_and_valid_basis_constraint(tmp_path):
    store=ledger(tmp_path);result=store.validate_refund_link('R-W','P-W');save_confirmed(store,result,'RL')
    with pytest.raises(sqlite3.IntegrityError):save_confirmed(store,result,'RL-duplicate')
    with pytest.raises(sqlite3.IntegrityError):
        with store.connect() as db:db.execute("UPDATE ledger_settings SET value='invalid' WHERE key='spending_basis'")


def test_refund_validation_is_readonly_and_resolves_purchase_funding_leg(tmp_path):
    store=ledger(tmp_path);before=Path(store.path).read_bytes();gross=store.transactions().to_json()
    result=store.validate_refund_link('R-W','P-B')
    assert result['original_txn_id']=='P-W' and result['amount_cents']==3000
    assert result['remaining_before_cents']==10000 and result['remaining_after_cents']==7000
    assert result['context']['purchase']['member_ids']==['P-B','P-W']
    assert Store(store.path,read_only=True).validate_refund_link('R-W','P-W')==result
    assert Path(store.path).read_bytes()==before and store.transactions().to_json()==gross


def test_refund_validation_uses_one_snapshot_across_concurrent_commits(tmp_path,monkeypatch):
    import finance.refund_rules as rules
    store=ledger(tmp_path)
    with store.connect() as db:assert db.execute('PRAGMA journal_mode=WAL').fetchone()[0]=='wal'
    resolve=rules.resolve_event;written=False
    def concurrent_edit(db,identifier,kind):
        nonlocal written
        event=resolve(db,identifier,kind)
        if not written:
            written=True
            with store.connect() as writer:
                writer.execute("UPDATE transactions SET amount_cents=2000 WHERE id IN ('P-W','P-B')")
        return event
    monkeypatch.setattr(rules,'resolve_event',concurrent_edit)
    result=store.validate_refund_link('R-W','P-W')
    assert written and result['remaining_before_cents']==10000 and result['remaining_after_cents']==7000
    with store.connect(read_only=True) as db:
        assert db.execute("SELECT amount_cents FROM transactions WHERE id='P-W'").fetchone()[0]==2000
        assert db.execute('SELECT COUNT(*) FROM refund_links').fetchone()[0]==0
    # A subsequent validation must see the newer committed amount, not a cached preview.
    with pytest.raises(ValueError,match='累计退款超过'):store.validate_refund_link('R-W','P-W')


@pytest.mark.parametrize('amount',[0,-1,2999,3001,True,1.5,'3000'])
def test_only_whole_positive_integer_refund_amount_is_allowed(tmp_path,amount):
    store=ledger(tmp_path);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.validate_refund_link('R-W','P-W',amount)
    assert Path(store.path).read_bytes()==before


def test_partial_multiple_refunds_share_one_consumption_cap(tmp_path):
    store=ledger(tmp_path);save_confirmed(store,store.validate_refund_link('R-W','P-W'),'RL-30')
    add_refund(store,'R-20',2000);result=store.validate_refund_link('R-20','P-B')
    assert result['confirmed_before_cents']==3000 and result['remaining_after_cents']==5000
    save_confirmed(store,result,'RL-20');add_refund(store,'R-50',5000)
    result=store.validate_refund_link('R-50','P-W');assert result['remaining_after_cents']==0
    save_confirmed(store,result,'RL-50');add_refund(store,'R-1',1)
    with pytest.raises(ValueError,match='累计退款超过'):store.validate_refund_link('R-1','P-B')


@pytest.mark.parametrize('selected_original',['P-W','P-B'])
def test_existing_funding_leg_link_cannot_create_a_second_cap(tmp_path,selected_original):
    store=ledger(tmp_path);add_refund(store,'R-80',8000)
    save_confirmed(store,store.validate_refund_link('R-80','P-B'),'RL-80',original_id='P-B')
    with pytest.raises(ValueError,match='累计退款超过'):store.validate_refund_link('R-W',selected_original)


def test_refund_wallet_and_bank_resolve_to_one_event(tmp_path):
    store=ledger(tmp_path);refund_group(store)
    wallet=store.validate_refund_link('R-W','P-W');bank=store.validate_refund_link('R-B','P-B')
    assert wallet==bank and wallet['refund_txn_id']=='R-W'
    save_confirmed(store,wallet,'RL-30')
    with pytest.raises(ValueError,match='同一逻辑退款事件'):store.validate_refund_link('R-B','P-W')


@pytest.mark.parametrize('damage',['two_wallets','amount','role','missing_member'])
def test_invalid_confirmed_refund_group_is_rejected_without_repair_writes(tmp_path,damage):
    store=ledger(tmp_path);refund_group(store)
    with store.connect() as db:
        if damage=='two_wallets':db.execute("UPDATE transactions SET channel='支付宝' WHERE id='R-B'")
        elif damage=='amount':db.execute("UPDATE transactions SET amount_cents=2999 WHERE id='R-B'")
        elif damage=='role':db.execute("UPDATE link_members SET role='funding' WHERE group_id='GR' AND transaction_id='R-B'")
        else:db.execute("DELETE FROM link_members WHERE group_id='GR' AND transaction_id='R-B'")
    before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.validate_refund_link('R-W','P-W')
    assert Path(store.path).read_bytes()==before


def test_old_refund_decisions_do_not_silently_move_after_deduplication(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    save_confirmed(store,store.validate_refund_link('R-W','P-W'),'RL-W')
    save_confirmed(store,store.validate_refund_link('R-B','P-W'),'RL-B')
    with store.connect() as db:
        db.execute("INSERT INTO link_groups VALUES ('GR','refund_duplicate','confirmed','合成同一退款','A','m2-refund-1','2026-10-02','2026-10-02','user')")
        db.executemany("INSERT INTO link_members VALUES ('GR',?,'refund')",[('R-W',),('R-B',)])
    add_refund(store,'R-10',1000);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='需要先复核'):store.validate_refund_link('R-10','P-W')
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('damage',['purchase_amount','refund_date','refund_channel','purchase_split','bad_context'])
def test_stale_confirmed_context_requires_review_without_writing(tmp_path,damage):
    store=ledger(tmp_path);save_confirmed(store,store.validate_refund_link('R-W','P-W'),'RL-30')
    with store.connect() as db:
        if damage=='purchase_amount':db.execute("UPDATE transactions SET amount_cents=8000 WHERE id IN ('P-W','P-B')")
        elif damage=='refund_date':db.execute("UPDATE transactions SET date='2026-11-01' WHERE id='R-W'")
        elif damage=='refund_channel':db.execute("UPDATE transactions SET channel='银行' WHERE id='R-W'")
        elif damage=='purchase_split':db.execute("UPDATE link_groups SET status='split' WHERE id='GP'")
        else:db.execute("UPDATE refund_links SET context_json='not-json' WHERE id='RL-30'")
    add_refund(store,'R-20',2000);before=Path(store.path).read_bytes()
    with pytest.raises(ValueError,match='需要先复核'):store.validate_refund_link('R-20','P-W')
    assert Path(store.path).read_bytes()==before


@pytest.mark.parametrize('status',['candidate','rejected','needs_review'])
def test_unconfirmed_decisions_do_not_reserve_the_cap(tmp_path,status):
    store=ledger(tmp_path);add_refund(store,'R-80',8000)
    with store.connect() as db:
        db.execute('''INSERT INTO refund_links(id,refund_txn_id,original_txn_id,amount_cents,status,reason,confidence,algorithm_version)
            VALUES ('RL-80','R-80','P-W',8000,?,'synthetic','B','m2-refund-1')''',(status,))
    assert store.validate_refund_link('R-W','P-W')['confirmed_before_cents']==0


@pytest.mark.parametrize('damage',['refund_before_purchase','currency','ordinary_income','refund_transfer','original_income','original_review','deleted_refund_batch'])
def test_invalid_source_type_currency_time_or_state_is_rejected(tmp_path,damage):
    store=ledger(tmp_path)
    with store.connect() as db:
        if damage=='refund_before_purchase':db.execute("UPDATE transactions SET date='2026-08-01' WHERE id='R-W'")
        elif damage=='currency':db.execute("UPDATE transactions SET currency='USD' WHERE id='R-W'")
        elif damage=='ordinary_income':db.execute("UPDATE transactions SET status='normal',description='退款文字也不能代替流水类型' WHERE id='R-W'")
        elif damage=='refund_transfer':db.execute("UPDATE transactions SET kind='转账',category='转账' WHERE id='R-W'")
        elif damage=='original_income':db.execute("UPDATE transactions SET kind='收入',category='其他收入' WHERE id='P-W'")
        elif damage=='original_review':db.execute("UPDATE transactions SET status='review',description='已退款但缺独立凭据' WHERE id='P-W'")
        else:db.execute("UPDATE import_batches SET status='deleted' WHERE id='B-W'")
    before=Path(store.path).read_bytes()
    with pytest.raises(ValueError):store.validate_refund_link('R-W','P-W')
    assert Path(store.path).read_bytes()==before


def test_missing_or_same_sources_are_not_refund_evidence(tmp_path):
    store=ledger(tmp_path)
    for refund,original in [('missing','P-W'),('R-W','missing'),('P-W','P-W')]:
        with pytest.raises(ValueError):store.validate_refund_link(refund,original)
