"""Course feedback contracts: local constraints, safe diagnostics, real Tk actions."""
from datetime import date
from urllib.error import HTTPError, URLError
from unittest.mock import patch
import json
import tkinter as tk
import pytest

from finance.qa import parse_question, answer_question, validate_model_intent
from finance.llm import ModelClient, ModelResponseError, model_status_text, connection_error_message
from scripts.m2_ui_fixture import seed_ui_refunds


@pytest.mark.parametrize('connector',['到','至',' 至 ','之间'])
def test_range_is_local_and_inclusive(tmp_path,connector):
    store,_=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    class NoNetwork:
        def parse_question(self,q):raise AssertionError('Local date queries must not use model')
    q=f'2026-09-01{connector}2026-09-01餐饮花了多少？'
    intent=parse_question(q)
    assert intent['date_from']==intent['date_to']=='2026-09-01'
    result=answer_question(store.spending_view(),q,model=NoNetwork())
    assert result['parser']=='本地解析' and result['status']=='ok'
    assert sum(r['amount_cents'] for r in result['evidence'])==10000
    assert result['trace']['inputs']['date_to']=='2026-09-01'


@pytest.mark.parametrize('q',[
    '2026-02-30到2026-03-01支出多少？','2026-09-02到2026-09-01支出多少？',
    '2026-09-01支出多少？','2026-09-01到2026-09-02到2026-09-03支出多少？',
    '2026-09-01到2026-09-30上个月支出多少？',
])
def test_invalid_or_conflicting_dates_are_not_broadened(q):
    assert parse_question(q) is None
    assert validate_model_intent(q,dict(metric='expense',category=None,period='all')) is None


def test_model_may_add_merchant_but_may_not_drop_dates(tmp_path):
    store,_=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    q='2026-09-01至2026-09-30在合成餐厅餐饮花了多少？'
    intent=dict(metric='expense',category='餐饮',period='all',merchant='合成餐厅')
    assert validate_model_intent(q,intent) is None
    intent.update(date_from='2026-09-01',date_to='2026-09-30')
    assert validate_model_intent(q,intent)==intent
    class Model:
        def parse_question(self,q):return intent
    result=answer_question(store.spending_view(),q,model=Model())
    assert result['status']=='ok' and result['parser']=='模型理解 + 代码计算'
    assert all(r['merchant']=='合成餐厅' for r in result['evidence'])


@pytest.mark.parametrize('code,hint',[(401,'Key'),(402,'余额'),(403,'权限'),(404,'路径或模型'),(429,'频繁'),(500,'服务商')])
def test_query_errors_keep_actionable_reason_without_sensitive_text(tmp_path,code,hint):
    store,_=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    class Model:
        def parse_question(self,q):raise HTTPError('https://secret.invalid?api_key=private',code,'Bearer private',{},None)
    result=answer_question(store.spending_view(),'上个月在合成餐厅花了多少？',model=Model())
    assert result['status']=='unsupported' and hint in result['answer']
    assert 'private' not in str(result) and 'Bearer' not in str(result)


def test_model_status_configuration_success_failure_and_recovery():
    client=ModelClient('https://example.com','demo','private',enabled=True)
    assert '本地模式' in model_status_text(None)
    assert '尚未验证' in model_status_text(client)
    with patch.object(client,'_request_json',return_value={'ok':True}):client.test_connection()
    assert '已验证' in model_status_text(client)
    with patch.object(client,'_request_json',side_effect=URLError(TimeoutError('private'))):
        with pytest.raises(URLError):client.test_connection()
    assert '超时' in model_status_text(client) and 'private' not in model_status_text(client)
    with patch.object(client,'_request_json',return_value={'ok':False}):
        with pytest.raises(ModelResponseError):client.test_connection()
    assert '暂不可用' in model_status_text(client)
    with patch.object(client,'_request_json',return_value={'ok':True}):client.test_connection()
    assert '已验证' in model_status_text(client)
    assert 'private' not in connection_error_message(ModelResponseError('private'))


def test_answer_preview_keeps_scope_and_formulas_in_details(tmp_path):
    store,_=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    result=answer_question(store.spending_view(),'2026-09餐饮花了多少？')
    lines=result['display_answer'].splitlines()
    assert len(lines)==2 and lines[0].startswith('餐饮净支出合计 100.00 元')
    assert '100.00%' in lines[1]
    assert '数据范围' not in result['display_answer'] and '计算方式' not in result['display_answer']
    assert '数据范围' in result['details'] and '计算方式' in result['details']
    assert '退款待处理' not in result['answer']
    gross=answer_question(store.spending_view(),'2026-09餐饮消费总额多少？')
    assert '消费总额' in gross['display_answer'] and '净支出' not in gross['display_answer']


def descendants(w):
    for child in w.winfo_children():
        yield child
        yield from descendants(child)


def button(w,text):
    return next(c for c in descendants(w) if c.winfo_class()=='Button' and c.cget('text')==text)


@pytest.fixture(scope='module')
def app(tmp_path_factory):
    import desktop
    tmp_path=tmp_path_factory.mktemp('feedback_gui')
    seed_ui_refunds(tmp_path/'demo.sqlite3')
    (tmp_path/'preferences.json').write_text(json.dumps({'space':'demo','month':'2026-09'}))
    with patch.object(desktop,'DATA_DIR',tmp_path):
        root=desktop.Desktop();root.withdraw()
        yield root
        root.destroy()


def test_real_ledger_selection_toolbar_and_single_settings_entry(app):
    app.show('records');app.update()
    assert not app.selection_bar.winfo_manager()
    app.tree.selection_set(app.tree.get_children()[0]);app.update_category_choices()
    assert app.selection_label.cget('text')=='已选 1 笔' and app.selection_bar.winfo_manager()=='pack'
    button(app.selection_bar,'取消选择').invoke();app.update_category_choices()
    assert not app.selection_bar.winfo_manager()
    assert all(not b.cget('text')[:2].isdigit() for b in app.nav.values())
    assert not any(w.winfo_class()=='Button' and w.cget('text')=='设置' for w in descendants(app))


def test_real_import_account_refresh_selects_new_account(app):
    with patch('finance.desktop_m1a.filedialog.askopenfilename',return_value='synthetic.csv'):
        app.select_import()
    window=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel))
    picker=[w for w in descendants(window) if w.winfo_class()=='TCombobox'][1]
    button(window,'管理账户').invoke()
    manager=next(w for w in app.winfo_children() if isinstance(w,tk.Toplevel) and w is not window)
    entry=next(w for w in descendants(manager) if w.winfo_class()=='TEntry')
    entry.delete(0,'end');entry.insert(0,'新建的合成账户')
    button(manager,'添加账户').invoke();manager.destroy();app.update()
    assert picker.get().startswith('新建的合成账户')
    assert picker.get() in picker.cget('values')
    window.destroy()


def test_estimate_cancellation_preserves_every_manual_value(app):
    app.show('plan')
    before={name:entry.get() for name,entry in app.inputs.items()}
    from finance.analytics import estimate_from_records
    with patch('finance.desktop_review.estimate_from_records',side_effect=lambda f:estimate_from_records(f,date(2026,11,4))), patch('finance.desktop_review.messagebox.askyesno',return_value=False) as confirmation:
        app.estimate_plan()
    confirmation.assert_called_once()
    assert {name:entry.get() for name,entry in app.inputs.items()}==before


def test_sidebar_body_settings_use_same_unverified_model_state(app):
    app.model_client=ModelClient('https://example.com','demo','private',enabled=True)
    app.show('assistant')
    expected=model_status_text(app.model_client)
    assert app.tip.cget('text')==app.model_status_label.cget('text')==expected
    app.show('settings')
    assert any(w.winfo_class()=='Label' and w.cget('text')==expected for w in descendants(app.main))
    app.model_client.last_error='连接超时'
    app.refresh_model_state()
    assert '连接超时' in app.settings_model_status_label.cget('text')
    app.show('assistant')
    assert app.tip.cget('text')==app.model_status_label.cget('text')==model_status_text(app.model_client)


@pytest.mark.parametrize('question',['2026-09支出最多的分类？','2026-09餐饮占比多少？','2026-09餐饮超支多少？','2026-09结余是多少？','2026-09有多少笔交易？'])
def test_other_answer_previews_are_two_lines_without_calculation_notes(tmp_path,question):
    store,_=seed_ui_refunds(tmp_path/'ledger.sqlite3')
    result=answer_question(store.spending_view(),question,budgets={'2026-09':{'餐饮':12000}})
    assert result['status']=='ok'
    assert len(result['display_answer'].splitlines())==2
    assert not any(word in result['display_answer'] for word in ('计算方式','数据范围','假设：','缺失信息：'))
