from pathlib import Path
from streamlit.testing.v1 import AppTest

APP=Path(__file__).resolve().parents[1]/'app.py'

def run_app(tmp_path, monkeypatch):
    monkeypatch.setenv('FINANCE_DATA_DIR',str(tmp_path))
    return AppTest.from_file(str(APP),default_timeout=20).run()

def test_all_pages_and_spaces(tmp_path,monkeypatch):
    at=run_app(tmp_path,monkeypatch)
    assert not at.exception
    assert len(at.metric)==4 and at.metric[3].value=='32'
    for page in ['导入与分类','预算与提醒','储蓄方案','有依据问答','使用说明','财务概览']:
        at.sidebar.radio[1].set_value(page).run()
        assert not at.exception, (page,at.exception)
    at.sidebar.radio[0].set_value('我的账单').run()
    assert not at.exception and len(at.metric)==0
    assert any('还没有账单' in x.value for x in at.info)

def test_ui_qa_and_savings(tmp_path,monkeypatch):
    at=run_app(tmp_path,monkeypatch)
    at.sidebar.radio[1].set_value('有依据问答').run()
    at.button[0].click().run()
    assert not at.exception
    assert any('145.00' in x.value for x in at.markdown)
    assert len(at.dataframe)==1
    at.sidebar.radio[1].set_value('储蓄方案').run()
    at.button[0].click().run()
    assert not at.exception
    table=at.dataframe[0].value
    assert table['到期可存总额'].tolist()==[4800,7200]
    assert table['按假设可达成'].tolist()==['否','是']
    at.button[2].click().run()
    assert not at.exception and len(at.dataframe)==2

def test_ui_budget_persistence(tmp_path,monkeypatch):
    at=run_app(tmp_path,monkeypatch)
    at.sidebar.radio[1].set_value('预算与提醒').run()
    assert any('45.00' in x.value for x in at.warning)
    at.number_input[0].set_value(200.0)
    at.button[0].click().run()
    assert not at.exception and not at.warning
    fresh=run_app(tmp_path,monkeypatch)
    fresh.sidebar.radio[1].set_value('预算与提醒').run()
    report=fresh.dataframe[0].value
    assert report[report['分类']=='餐饮'].iloc[0]['预算']==200
