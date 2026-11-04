"""Release-blocking refund UI regressions, before packaged M2 acceptance."""
from pathlib import Path
from unittest.mock import patch
import sqlite3
import importlib.util
import pytest
from test_m2_4_web import web_fixture, page, widget, healthy
from test_m2_1 import ledger, add_refund
from m2_v4_fixture import create_v4
from finance.m2_release_acceptance import verify_m2_migration


@pytest.mark.parametrize('fault',['write_warning','exit_failure','missing_marker'])
def test_packaging_smoke_rejects_unknown_write_warning_and_failed_acceptance(tmp_path,monkeypatch,fault):
    script=Path(__file__).resolve().parents[1]/'packaging/smoke_executable.py'
    spec=importlib.util.spec_from_file_location('m2_smoke_gate',script);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    import subprocess,sys
    monkeypatch.setattr(sys,'argv',[str(script),'--exe',str(tmp_path/'synthetic.exe'),'--data-dir',str(tmp_path/'smoke')])
    def simulated_process(args,**kwargs):
        folder=Path(kwargs['env']['FINANCE_DESKTOP_DATA_DIR'])
        if fault!='missing_marker':(folder/'self-test.ok').write_text('marker')
        return subprocess.CompletedProcess(args,1 if fault=='exit_failure' else 0,b'',b"can't open file for write !" if fault=='write_warning' else b'')
    monkeypatch.setattr(module.subprocess,'run',simulated_process)
    with pytest.raises(RuntimeError,match='installer build stopped'):module.main()
    assert (tmp_path/'smoke/self-test/process.stderr.log').is_file()


def test_release_migration_rehearsal_preserves_old_data_and_rolls_back_faults(tmp_path):
    create_v4(tmp_path/'personal.sqlite3')
    report=verify_m2_migration(tmp_path)
    assert all(value is True for key,value in report.items() if key not in ('data','migration'))


def test_repaired_refund_group_leaves_blockers_but_preserves_explicit_reopen(tmp_path):
    store=ledger(tmp_path);add_refund(store,'R-B',3000,channel='银行')
    identifier=store.create_refund_group(['R-W','R-B'],reason='合成同一退款')
    store.decide_group(identifier,'confirmed')
    with store.connect() as db:db.execute("UPDATE transactions SET amount_cents=2000 WHERE id='R-B'")
    assert any(r['id']==identifier for r in store.refund_work_items())
    assert store.repair_invalid_groups(identifier=identifier)==1
    before=Path(store.path).read_bytes()
    assert not store.refund_work_items() and Path(store.path).read_bytes()==before
    group=next(g for g in store.reconciliation_groups() if g['id']==identifier)
    assert group['stored_status']=='needs_review' and not group['repair_pending']
    assert store.repair_invalid_groups(identifier=identifier)==0
    with pytest.raises(ValueError):store.reopen_group(identifier)
    store.edit_transaction('R-B',dict(date='2026-10-02 12:00:00',merchant='合成餐厅',description='修复来源',
                           amount_cents=3000,kind='收入',category='其他收入'))
    store.reopen_group(identifier)
    assert next(g for g in store.reconciliation_groups() if g['id']==identifier)['stored_status']=='candidate'
    assert not store.refund_links()  # Reopening never invents a money decision.


def test_web_repaired_group_has_reopen_only_and_does_not_remain_a_blocker(tmp_path,monkeypatch):
    app,store,ids=web_fixture(tmp_path,monkeypatch);page(app,'退款与口径')
    group=next(g for g in store.reconciliation_groups() if g['kind']=='refund_duplicate')
    assert widget(app,'selectbox','退款组动作').options==['拆分']
    with store.connect() as db:db.execute('UPDATE transactions SET amount_cents=2000 WHERE id=?',(ids['R-B'],))
    store.repair_refund_links();store.repair_invalid_groups(identifier=group['id']);app.run();healthy(app)
    assert not store.refund_work_items()
    assert not any(w.label=='选择待复核事项' for w in app.selectbox)
    assert widget(app,'selectbox','退款组动作').options==['重新核对']
    # User repairs the source, then uses the actual Web reopen and confirmation controls.
    store.edit_transaction(ids['R-B'],dict(date='2026-10-01',merchant='财付通',description='来源修正',
                           amount_cents=3000,kind='收入',category='其他收入'))
    app.run();widget(app,'checkbox','已核对为同一退款；若无钱包，另行确认并非两笔退款').check().run()
    widget(app,'button','保存退款组决定').click().run();healthy(app)
    assert widget(app,'selectbox','退款组动作').options==['确认']
    widget(app,'button','保存退款组决定').click().run();healthy(app)
    assert widget(app,'selectbox','退款组动作').options==['拆分']


@pytest.mark.parametrize('operation',['basis','repair','revoke'])
def test_web_mutation_audit_failure_is_friendly_and_rolls_back(tmp_path,monkeypatch,operation):
    app,store,ids=web_fixture(tmp_path,monkeypatch);page(app,'退款与口径')
    if operation=='basis':
        widget(app,'selectbox','净支出口径').set_value('original_purchase_month').run()
        widget(app,'checkbox','我确认重算当前空间净额，原始账单不变').check().run()
        button='应用净支出口径';prefix='口径未保存'
    elif operation=='repair':
        with store.connect() as db:db.execute('UPDATE transactions SET amount_cents=2000 WHERE id=?',(ids['R-B'],))
        app.run();widget(app,'checkbox','确认保存所选组件的复核状态，不自动重新确认金额').check().run()
        button='保存定点修复';prefix='修复未保存'
    else:
        widget(app,'checkbox','确认撤销所选关联，保留来源和审计').check().run()
        button='撤销选中关联';prefix='撤销未保存'
    before=Path(store.path).read_bytes()
    with patch('finance.refunds.audit',side_effect=sqlite3.OperationalError('合成审计写入失败')):
        widget(app,'button',button).click().run()
    healthy(app)
    assert any(prefix in w.value for w in app.error)
    assert Path(store.path).read_bytes()==before
