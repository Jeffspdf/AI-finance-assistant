"""Synthetic regressions for every round-five correctness boundary."""
from dataclasses import replace
from datetime import date
from io import StringIO
from pathlib import Path
from unittest.mock import patch
import csv
import pytest
from finance.storage import Store
from finance.importer import parse_statement
from finance.demo import initialize_demo
from finance.demo_generator import DemoOptions,generate_demo
from finance.m1a_demo import seed_six

SAMPLES=Path(__file__).resolve().parents[1]/'samples/acceptance'


def csv_data(rows):
    stream=StringIO();writer=csv.writer(stream)
    writer.writerow(['交易ID','日期','商户','描述','金额','类型','平台','币种']);writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')


def expense(store):
    f=store.transactions();return int(f.loc[f.kind=='支出','amount_cents'].sum())


def alipay_pair(store,bank_text='支付宝',bound=True):
    wallet=store.add_account('支付宝','wallet','合成支付宝')
    bank=store.add_account('银行','debit','合成银行卡','1234')
    if bound:store.add_funding_link(wallet,bank,'2026-01-01')
    rows=parse_statement(csv_data([
        ['A1','2026-09-01 12:00:00','校园食堂','午餐','20','支出','支付宝','CNY'],
        ['B1','2026-09-01 12:00:30','结算平台',bank_text+'商户支付','20','支出','银行','CNY']]),'pair.csv').rows
    rows[0]['account_id']=wallet;rows[1]['account_id']=bank;store.import_rows(rows)
    return rows


@pytest.mark.parametrize('text',['支付宝','ALIPAY','Alipay','aLiPaY'])
def test_alipay_bound_and_unique_is_A(tmp_path,text):
    store=Store(tmp_path/'db.sqlite3');alipay_pair(store,text)
    store.generate_candidates();groups=store.reconciliation_groups()
    assert len(groups)==1 and groups[0]['confidence']=='A' and expense(store)==4000
    store.decide_group(groups[0]['id'],'confirmed');assert expense(store)==2000


def test_alipay_without_binding_never_A(tmp_path):
    store=Store(tmp_path/'db.sqlite3');alipay_pair(store,bound=False)
    store.generate_candidates();groups=store.reconciliation_groups()
    assert groups and all(g['confidence'] in ('B','C') for g in groups) and expense(store)==4000


def insert_old_group(store,rows,gid='old',confidence='B',confirmed=False):
    with store.connect() as db:
        db.execute("INSERT INTO link_groups(id,kind,status,reason,confidence,algorithm_version) VALUES (?,'purchase_duplicate','candidate','合成旧版组',?,'m1a-1')",(gid,confidence))
        db.executemany('INSERT INTO link_members(group_id,transaction_id,role) VALUES (?,?,?)',[(gid,r['id'],'purchase' if i==0 else 'funding') for i,r in enumerate(rows)])
        if confirmed:db.execute("UPDATE link_groups SET status='confirmed' WHERE id=?",(gid,))


@pytest.mark.parametrize('channel,three',[('支付宝',False),('其他钱包',False),('支付宝',True)])
def test_two_wallets_never_confirm_even_in_legacy_three_member_groups(tmp_path,channel,three):
    store=Store(tmp_path/'db.sqlite3')
    a=store.add_account('微信','wallet','合成微信');b=store.add_account(channel,'wallet','合成第二钱包')
    rows=parse_statement(csv_data([
        ['W1','2026-09-01 12:00:00','同店','午餐','20','支出','微信','CNY'],
        ['W2','2026-09-01 12:00:30','同店','午餐','20','支出',channel,'CNY'],
        *([['B1','2026-09-01 12:01:00','财付通','微信支付','20','支出','银行','CNY']] if three else [])]),'wallets.csv').rows
    rows[0]['account_id']=a;rows[1]['account_id']=b;store.import_rows(rows)
    insert_old_group(store,rows)
    with pytest.raises(ValueError,match='最多一个钱包'):store.decide_group('old','confirmed')
    assert expense(store)==2000*len(rows)
    store.generate_candidates()
    assert not any({rows[0]['id'],rows[1]['id']}<={m['id'] for m in g['members']} and g['status']=='candidate' for g in store.reconciliation_groups())


def test_legacy_bad_confirmation_is_repaired_once_and_unlocked(tmp_path):
    store=Store(tmp_path/'db.sqlite3');rows=alipay_pair(store)
    with store.connect() as db:db.execute("UPDATE transactions SET channel='微信' WHERE id=?",(rows[1]['id'],))
    insert_old_group(store,rows,confirmed=True)
    reopened=Store(store.path)
    assert expense(reopened)==4000
    group=reopened.reconciliation_groups()[0];assert group['status']=='needs_review'
    with reopened.connect() as db:
        assert all(r[0] is None for r in db.execute('SELECT link_group_id FROM transactions'))
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='link_invalidated'").fetchone()[0]==1
    reopened.generate_candidates()
    with reopened.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='link_invalidated'").fetchone()[0]==1


@pytest.mark.parametrize('route',['single','batch','edit','model','undo'])
def test_all_category_writes_invalidate_groups_and_clear_cache(tmp_path,route):
    store=Store(tmp_path/'db.sqlite3');seed_six(store);store.generate_candidates()
    groups=store.reconciliation_groups()
    for group in groups:store.decide_group(group['id'],'confirmed')
    group=next(g for g in groups if g['members'][0]['amount_cents']==2000)
    row=next(r for r in group['members'] if r['role']=='purchase')
    assert expense(store)==6000
    if route=='single':store.correct_categories([(row['id'],'转账')])
    elif route=='batch':store.batch_category([row['id']],'转账')
    elif route=='edit':store.edit_transaction(row['id'],dict(row,category='转账'))
    elif route=='model':
        with store.connect() as db:db.execute("UPDATE transactions SET category_source='待确认' WHERE id=?",(row['id'],))
        assert store.apply_model_category(row['id'],'教育')
    else:
        snapshot=store.batch_category([row['id']],'教育')
        store.reopen_group(group['id']);store.generate_candidates();store.decide_group(group['id'],'confirmed')
        assert expense(store)==6000
        assert store.undo_category_batch(snapshot)==1
    assert expense(store)==8000
    assert next(g for g in store.reconciliation_groups() if g['id']==group['id'])['status']=='needs_review'
    with store.connect() as db:
        assert not db.execute('SELECT 1 FROM transactions WHERE link_group_id=?',(group['id'],)).fetchone()
    store.generate_candidates();assert expense(store)==8000
    assert any('待复核' in note for note in store.source_transactions().counting_note)


def test_unchanged_category_does_not_invalidate(tmp_path):
    store=Store(tmp_path/'db.sqlite3');seed_six(store);store.generate_candidates()
    for g in store.reconciliation_groups():store.decide_group(g['id'],'confirmed')
    row=store.transactions().query("kind=='支出'").iloc[0]
    store.correct_categories([(row.id,row.category)])
    assert expense(store)==6000 and all(g['status']=='confirmed' for g in store.reconciliation_groups())


@pytest.mark.parametrize('name,expected',[('m1a_wechat.csv','wechat_csv'),('m1a_bank_template_v1.xlsx','bank_template_v1')])
def test_auto_detect_exact_supported_headers(name,expected):
    parsed=parse_statement((SAMPLES/name).read_bytes(),name,adapter='auto')
    assert parsed.adapter==expected and not parsed.issues and parsed.document
    assert set(r['channel'] for r in parsed.rows)==({'微信'} if expected=='wechat_csv' else {'银行'})


def test_same_file_parser_and_account_changes_conflict_instead_of_double_count(tmp_path):
    store=Store(tmp_path/'db.sqlite3');content=(SAMPLES/'m1a_wechat.csv').read_bytes()
    generic=parse_statement(content,'wx.csv',retain_source=True);store.import_statement(generic)
    original=store.source_transactions().to_dict('records');assert expense(store)==6000
    wallet=store.add_account('微信','wallet','合成微信')
    for account in (None,wallet):
        changed=parse_statement(content,'renamed.csv',adapter='wechat_csv',account_id=account)
        added,duplicates,conflicts=store.import_statement(changed)
        assert added==0 and len(conflicts)==4 and expense(store)==6000
    assert store.source_transactions().to_dict('records')==original
    assert store.import_statement(generic)==(0,4,[])


def test_same_file_changed_explicit_account_conflicts_but_distinct_scoped_ids_survive(tmp_path):
    store=Store(tmp_path/'db.sqlite3');content=(SAMPLES/'m1a_wechat.csv').read_bytes()
    a=store.add_account('微信','wallet','账户A');b=store.add_account('微信','wallet','账户B')
    store.import_statement(parse_statement(content,'one.csv',adapter='wechat_csv',account_id=a))
    result=store.import_statement(parse_statement(content,'two.csv',adapter='wechat_csv',account_id=b))
    assert result[0]==0 and len(result[2])==4 and expense(store)==6000
    # A global external-ID constraint would wrongly lose this genuinely different source.
    altered=content.replace(b'20.00',b'21.00')
    assert store.import_statement(parse_statement(altered,'other.csv',adapter='wechat_csv',account_id=b))[0]==4
    assert expense(store)==12100


def test_no_id_origin_cannot_be_imported_into_a_second_account(tmp_path):
    store=Store(tmp_path/'db.sqlite3');content=b'date,amount,merchant\n2026-09-01,-20,shop\n'
    a=store.add_account('银行','debit','A');b=store.add_account('银行','debit','B')
    store.import_statement(parse_statement(content,'first.csv',retain_source=True,account_id=a))
    outcome=store.import_statement(parse_statement(content,'renamed.csv',retain_source=True,account_id=b))
    assert outcome[0]==0 and len(outcome[2])==1 and expense(store)==2000


def test_C_candidate_requires_more_evidence_and_can_be_regraded(tmp_path):
    store=Store(tmp_path/'db.sqlite3');alipay_pair(store,bound=False)
    store.generate_candidates();group=store.reconciliation_groups()[0];assert group['confidence']=='C'
    with pytest.raises(ValueError,match='C级'):store.decide_group(group['id'],'confirmed')
    assert expense(store)==4000
    accounts=store.accounts();wallet=next(a['id'] for a in accounts if a['type']=='wallet');bank=next(a['id'] for a in accounts if a['type']=='debit')
    store.add_funding_link(wallet,bank,'2026-01-01');store.reopen_group(group['id']);store.generate_candidates()
    assert store.reconciliation_groups()[0]['confidence']=='A'
    store.decide_group(group['id'],'confirmed');assert expense(store)==2000


def test_stale_cleanup_preserves_human_decisions_and_raw_evidence(tmp_path):
    store=Store(tmp_path/'db.sqlite3');seed_six(store);store.generate_candidates();groups=store.reconciliation_groups()
    store.decide_group(groups[0]['id'],'rejected')
    with store.connect() as db:db.execute("UPDATE transactions SET date='2026-10-01' WHERE id=?",(groups[1]['members'][0]['id'],))
    store.generate_candidates();before=store.source_transactions().to_dict('records')
    assert store.clean_stale_candidates()==1
    assert any(g['status']=='rejected' for g in store.reconciliation_groups())
    assert store.source_transactions().to_dict('records')==before


def test_demo_empty_history_not_reseeded_after_deletion_or_refund_only(tmp_path):
    store=Store(tmp_path/'demo.sqlite3')
    config=DemoOptions('2026-09',1,2,100,100,0,transfers=False)
    generate_demo(store,config,space='demo')
    store.delete_import_batch(store.import_batches()[0]['id']);assert store.transactions().empty
    assert initialize_demo(store,date(2026,11,3)) is False and store.source_transactions().empty
    assert initialize_demo(Store(store.path),date(2026,11,3)) is False
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='demo_initialized'").fetchone()[0]==1


def test_fresh_demo_is_seeded_exactly_once(tmp_path):
    store=Store(tmp_path/'demo.sqlite3')
    assert initialize_demo(store,date(2026,11,3)) and len(store.transactions())==100
    assert not initialize_demo(Store(store.path),date(2026,12,3)) and len(store.transactions())==100


def test_declared_accounts_resolve_generic_channels_for_alipay_A(tmp_path):
    store=Store(tmp_path/'db.sqlite3');rows=alipay_pair(store,'ALIPAY')
    with store.connect() as db:db.execute("UPDATE transactions SET channel='未指定'")
    store.generate_candidates()
    assert store.reconciliation_groups()[0]['confidence']=='A'


def test_reexport_with_unknown_platform_does_not_evade_conflict_by_hash_change(tmp_path):
    store=Store(tmp_path/'db.sqlite3');content=(SAMPLES/'m1a_wechat.csv').read_bytes()
    store.import_statement(parse_statement(content,'first.csv',retain_source=True))
    changed=content+b'\n'  # Different file hash, same original IDs and business content.
    result=store.import_statement(parse_statement(changed,'second.csv',adapter='auto'))
    assert result[0]==0 and len(result[2])==4 and expense(store)==6000


def test_category_invalidation_and_cache_rollback_are_atomic(tmp_path):
    store=Store(tmp_path/'db.sqlite3');seed_six(store);store.generate_candidates()
    groups=store.reconciliation_groups()
    for group in groups:store.decide_group(group['id'],'confirmed')
    row=store.transactions().query("kind=='支出'").iloc[0]
    with pytest.raises(ValueError):store.correct_categories([(row.id,'转账'),(row.id,'不存在')])
    assert expense(store)==6000 and all(g['status']=='confirmed' for g in store.reconciliation_groups())
    assert store.source_transactions().link_group_id.notna().sum()==4


def test_refund_only_demo_does_not_seed_into_empty_projection(tmp_path):
    store=Store(tmp_path/'demo.sqlite3')
    content='交易ID,日期,商户,金额,类型,状态\nREFUND,2026-09-01,店,10,收入,退款成功\n'.encode()
    store.import_statement(parse_statement(content,'refund.csv',retain_source=True))
    assert store.transactions().empty and store.pending_refunds()['count']==1
    assert not initialize_demo(store) and len(store.source_transactions())==1


def test_identical_scoped_external_ids_in_distinct_known_accounts_are_not_lost(tmp_path):
    store=Store(tmp_path/'db.sqlite3');a=store.add_account('银行','debit','合成A');b=store.add_account('银行','debit','合成B')
    content=csv_data([['LOCAL-001','2026-09-01 12:00:00','同店','消费','20','支出','银行','CNY']])
    for account,preamble in ((a,'合成账户A\n'),(b,'合成账户B\n')):
        result=parse_statement(preamble.encode()+content,'bank.csv',retain_source=True,account_id=account)
        assert store.import_statement(result)==(1,0,[])
    assert expense(store)==4000 and len(store.source_transactions())==2
