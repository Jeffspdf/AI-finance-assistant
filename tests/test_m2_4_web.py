from pathlib import Path
import json,subprocess,sys
import pytest
from streamlit.testing.v1 import AppTest
from scripts.m2_ui_fixture import seed_ui_refunds
from finance.analytics import estimate_from_records
from datetime import date

ROOT=Path(__file__).resolve().parents[1]


def widget(app,kind,label):return next(w for w in getattr(app,kind) if w.label==label)


def healthy(app):assert not app.exception,[str(e.value) for e in app.exception]


def web_fixture(tmp_path,monkeypatch,*,linked=True):
    store,ids=seed_ui_refunds(tmp_path/'demo.sqlite3')
    if linked:
        group=store.create_refund_group([ids['R-W'],ids['R-B']],reason='测试核对跨源退款');store.decide_group(group,'confirmed')
        store.confirm_refund_link(ids['R-W'],ids['P-W'])
    monkeypatch.setenv('FINANCE_DATA_DIR',str(tmp_path))
    app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=25).run();healthy(app)
    return app,store,ids


def page(app,title):app.sidebar.radio[1].set_value(title).run();healthy(app)


def test_web_actual_refund_group_and_money_controls_obey_b_contract(tmp_path,monkeypatch):
    app,store,ids=web_fixture(tmp_path,monkeypatch,linked=False);page(app,'退款与口径')
    widget(app,'selectbox','退款来源一').set_value(ids['R-W']).run()
    widget(app,'selectbox','退款来源二').set_value(ids['R-B']).run()
    widget(app,'button','提出同退款候选').click().run();healthy(app)
    widget(app,'checkbox','已核对为同一退款；若无钱包，另行确认并非两笔退款').check().run()
    widget(app,'button','保存退款组决定').click().run();healthy(app)
    assert len(store.refund_sources())==1
    widget(app,'checkbox','已核对完整退款及原消费来源').check().run()
    widget(app,'button','确认退款关联').click().run();healthy(app)
    assert store.refund_sources()[0]['association_status']=='linked'
    assert widget(app,'button','确认退款关联').disabled
    assert not any('退款待处理' in w.value for w in app.warning)
    widget(app,'checkbox','确认撤销所选关联，保留来源和审计').check().run()
    widget(app,'button','撤销选中关联').click().run();healthy(app)
    assert store.refund_links()[0]['stored_status']=='rejected'
    widget(app,'button','确认退款关联').click().run();healthy(app)
    assert store.refund_links()[0]['stored_status']=='confirmed'


def test_web_basis_controls_and_all_pages_use_the_same_fixture(tmp_path,monkeypatch):
    app,store,ids=web_fixture(tmp_path,monkeypatch)
    widget(app,'selectbox','查看月份').set_value('2026-10').run();healthy(app)
    assert app.metric[1].value=='¥ -30.00'
    page(app,'预算与提醒');widget(app,'selectbox','预算月份').set_value('2026-10').run();healthy(app)
    row=app.dataframe[0].value.query('分类=="餐饮"').iloc[0];assert row['已支出']==-30 and row['剩余']==130
    page(app,'导入与分类');assert any('净支出 ¥70.00' in w.value for w in app.caption)
    page(app,'储蓄方案');assert any('35.00' in w.value for w in app.caption)
    page(app,'有依据问答');widget(app,'text_input','你想知道什么？').set_value('2026-10 支出多少？').run()
    widget(app,'button','查询我的记录').click().run();healthy(app)
    assert any('-30.00' in w.value and '现金口径' in w.value for w in app.markdown)
    page(app,'退款与口径');before=Path(store.path).read_bytes()
    widget(app,'selectbox','净支出口径').set_value('original_purchase_month').run();healthy(app)
    assert Path(store.path).read_bytes()==before and widget(app,'button','应用净支出口径').disabled
    widget(app,'checkbox','我确认重算当前空间净额，原始账单不变').check().run()
    widget(app,'button','应用净支出口径').click().run();healthy(app)
    assert store.spending_basis()=='original_purchase_month'
    page(app,'财务概览');widget(app,'selectbox','查看月份').set_value('2026-10').run();healthy(app)
    assert app.metric[1].value=='¥ 0.00'
    widget(app,'selectbox','查看月份').set_value('2026-09').run();assert app.metric[1].value=='¥ 70.00'
    page(app,'预算与提醒');widget(app,'selectbox','预算月份').set_value('2026-10').run()
    assert app.dataframe[0].value.query('分类=="餐饮"').iloc[0]['已支出']==0
    page(app,'有依据问答');widget(app,'text_input','你想知道什么？').set_value('2026-10 支出多少？').run()
    widget(app,'button','查询我的记录').click().run();healthy(app)
    assert any('0.00' in w.value and '原消费月' in w.value for w in app.markdown)
    page(app,'储蓄方案');assert any('70.00' in w.value for w in app.caption)


def test_web_blocker_inspect_repair_and_revoke_are_operable_without_startup_side_effects(tmp_path,monkeypatch):
    app,store,ids=web_fixture(tmp_path,monkeypatch);page(app,'退款与口径')
    with store.connect() as db:db.execute('UPDATE transactions SET amount_cents=2000 WHERE id=?',(ids['R-B'],))
    before=Path(store.path).read_bytes();app.run();healthy(app)
    assert Path(store.path).read_bytes()==before
    picker=widget(app,'selectbox','选择待复核事项')
    assert picker.value['kind']=='link'
    widget(app,'button','查看阻塞来源与证据').click().run();healthy(app)
    assert Path(store.path).read_bytes()==before and app.json
    widget(app,'checkbox','确认保存所选组件的复核状态，不自动重新确认金额').check().run()
    widget(app,'button','保存定点修复').click().run();healthy(app)
    assert store.refund_links()[0]['stored_status']=='needs_review'
    assert widget(app,'selectbox','选择待复核事项').value['kind']=='group'
    widget(app,'button','保存定点修复').click().run();healthy(app)
    assert next(g for g in store.reconciliation_groups() if g['kind']=='refund_duplicate')['stored_status']=='needs_review'
    widget(app,'checkbox','确认撤销所选关联，保留来源和审计').check().run()
    widget(app,'button','撤销选中关联').click().run();healthy(app)
    assert store.refund_links()[0]['stored_status']=='rejected'


def test_actual_tk_workflow_in_a_separate_process(tmp_path):
    report_dir=tmp_path/'tk-results'
    result=subprocess.run([sys.executable,str(ROOT/'scripts/verify_m2_4_ui.py'),'--output',str(report_dir)],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    assert result.returncode==0,result.stdout+'\n'+result.stderr
    report=json.loads((report_dir/'gui-results.json').read_text(encoding='utf-8'))
    assert report['callback_errors']==[] and report['cash']['home']==report['cash']['ledger']==report['cash']['qa']==-3000
    assert report['original']['october_home']==report['original']['october_budget']==report['original']['october_qa']==0
    assert report['basis_switch_audits']==2
