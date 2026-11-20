from datetime import date
from hashlib import sha256
from io import BytesIO
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from finance.analytics import goal_progress
from finance.storage import Store
from qt_finance import updates
from qt_finance.services import query_ledger


QUESTION = '我计划在11月比10月多存1000元，收入不变的情况下，我的开支里面有哪些部分可以再优化'


def test_savings_question_uses_baseline_and_leaves_ledger_unchanged(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    ledger = Store(path)
    for when, name, cents, kind, category in (
        ('2026-10-01', '公司财务', 800000, '收入', '工资'),
        ('2026-10-02', '房租', 250000, '支出', '居住'),
        ('2026-10-03', '餐厅', 150000, '支出', '餐饮'),
        ('2026-10-04', '购物', 50000, '支出', '购物'),
        ('2026-11-04', '其他月', 10000, '支出', '餐饮'),
    ):
        ledger.add_manual_transaction(when, name, cents, kind, category)
    before = path.read_bytes()
    with patch('qt_finance.services.answer_question', side_effect=AssertionError('Unexpected model/query path')):
        result = query_ledger(path, QUESTION, object(), Event())
    assert result['scope'] == '2026-10 → 2026-11'
    assert result['route'] == 'plan' and '3,500.00 元' in result['answer']
    assert '餐饮 1,500.00 元、购物 500.00 元' in result['answer']
    assert '还差 800.00 元' in result['answer']
    assert {row['merchant'] for row in result['evidence']} == {'房租', '餐厅', '购物'}
    assert path.read_bytes() == before
    ledger.add_manual_transaction('2025-10-04', '旧账单', 10000, '支出', '餐饮')
    assert '请写明' in query_ledger(path, QUESTION, None, Event())['answer']
    assert '没有已导入记录' in query_ledger(
        path, QUESTION.replace('比10月', '比2024年10月'), None, Event(),
    )['answer']


@pytest.mark.parametrize('code', [403, 429])
def test_update_uses_public_page_and_keeps_hash_check(tmp_path, monkeypatch, code):
    base = f'/Jeffspdf/AI-finance-assistant/releases'
    tag = 'qt-v0.10.0-preview.13'
    name = 'AIFinanceHelperQt-0.10.0-preview.13-Setup-x64.exe'
    sums = 'SHA256SUMS-qt-0.10.0-preview.13.txt'
    payload = b'test installer'
    digest = sha256(payload).hexdigest()

    def open_release(request, *args, **kwargs):
        url = request.full_url.split('?')[0]
        if url.startswith('https://api.github.com/'):
            raise HTTPError(url, code, 'rate limit', {}, None)
        if url == updates.RELEASES:
            return BytesIO(f'<a href="{base}/tag/{tag}">Qt</a><a href="{base}/tag/v9.0.0">Tk</a>'.encode())
        if url.endswith(f'/expanded_assets/{tag}'):
            return BytesIO(''.join(f'<a href="{base}/download/{tag}/{asset}">asset</a>' for asset in (name, sums)).encode())
        if url.endswith('/' + sums):
            return BytesIO(f'{digest}  {name}\n'.encode())
        if url.endswith('/' + name):
            return BytesIO(payload)
        raise AssertionError(url)

    monkeypatch.setattr(updates, '_open_download', open_release)
    release = updates.check(installed=True, current='0.10.0-preview.12')
    assert release.tag == tag and release.digest == digest and release.size is None
    assert updates.check(installed=True, current='0.10.0-preview.13') is None
    assert updates.check(installed=True, current='0.9.0') is None
    target = tmp_path / name
    assert updates.download(release, target, Event()).read_bytes() == payload
    payload = b'changed'
    with pytest.raises(ValueError, match='SHA256'):
        updates.download(release, target, Event())
    assert target.read_bytes() == b'test installer'


def test_report_queries_preserve_both_amounts_and_read_only(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    ledger = Store(path)
    for merchant, amount, kind, category in (
        ('早餐', 1250, '支出', '餐饮'), ('地铁', 2200, '支出', '交通'), ('工资', 100000, '收入', '工资'),
    ):
        ledger.add_manual_transaction('2026-08-12', merchant, amount, kind, category)
    before = path.read_bytes()
    comparison = query_ledger(path, '2026年8月餐饮和交通各花多少？', None, Event())
    assert comparison['amounts'] == [('餐饮', 1250), ('交通', 2200)]
    assert comparison['amount_note'] == '差额（餐饮 − 交通）为 -9.50 元。'
    combined = query_ledger(path, '2026年8月的收入和净支出分别是多少？', None, Event())
    assert combined['amounts'] == [('收入', 100000), ('净支出', 3450)]
    assert combined['amount_note'] == '结余 965.50 元。'
    assert path.read_bytes() == before


def test_current_goal_forecast_uses_actual_and_remaining_dates(tmp_path):
    store = Store(tmp_path / 'ledger.sqlite3')
    goal = dict(name='测试目标', target_cents=600000, saved_cents=0, income_cents=250000,
                budget_cents=165000, start_date='2026-11-16', deadline='2027-05-16')
    identifier = store.save_goal(goal)
    store.add_contribution(identifier, '2026-11-16', 50000)
    progress = goal_progress(goal, store.contributions(identifier), date(2026, 11, 16))
    assert len(progress['future_dates']) == 6 and progress['future_dates'][0] == '2026-12-16'
    assert progress['plan']['deficit'] == 90000 and progress['current_deficit'] == 40000
    due = goal_progress(goal, store.contributions(identifier), date(2027, 5, 16))
    assert not due['future_dates'] and due['current_deficit'] == 550000


def test_batch_soft_delete_rolls_back_on_missing_id(tmp_path):
    store = Store(tmp_path / 'ledger.sqlite3')
    for amount in (1000, 2000):
        store.add_manual_transaction('2026-08-12', str(amount), amount, '支出', '餐饮')
    first, second = store.source_transactions().id
    with pytest.raises(ValueError, match='交易不存在'):
        store.soft_delete_transactions([first, 'missing', second], confirmed=True)
    assert set(store.source_transactions().id) == {first, second}
    assert store.soft_delete_transactions([first, second, first], confirmed=True) == 2
    assert store.source_transactions().empty
    for item in (first, second):
        assert store.transaction_source(item)['status'] == 'deleted'

def test_category_breakdown_and_follow_up_use_local_net_projection(tmp_path):
    path = tmp_path / 'ledger.sqlite3'
    ledger = Store(path)
    for category, cents in (('餐饮', 1250), ('交通', 2200), ('娱乐', 1550)):
        ledger.add_manual_transaction('2026-10-02', category, cents, '支出', category)
    ledger.add_manual_transaction('2026-11-02', '地铁', 9900, '支出', '交通')
    before = path.read_bytes()
    result = query_ledger(path, '2026-10 餐饮、购物和交通各花多少？', None, Event())
    assert result['breakdown'] == [
        dict(category='餐饮', cents=1250, count=1, share='25.0%'),
        dict(category='购物', cents=0, count=0, share='0.0%'),
        dict(category='交通', cents=2200, count=1, share='44.0%'),
    ]
    assert {row['category'] for row in result['evidence']} == {'餐饮', '交通'}
    follow = query_ledger(path, '那交通呢？', None, Event(), previous=result)
    assert follow['scope'] == '2026-10' and '22.00 元' in follow['answer']
    assert query_ledger(path, '那交通呢？', None, Event())['status'] == 'unsupported'
    missing = query_ledger(path, '2026-09 餐饮、购物和交通各花多少？', None, Event())
    assert missing['status'] == 'missing' and 'breakdown' not in missing
    assert path.read_bytes() == before

def test_conversation_query_filters_and_month_difference(tmp_path):
    ledger = Store(tmp_path / 'consult.sqlite3')
    for month, starbucks, other in (('09', 900, 300), ('10', 1700, 100)):
        ledger.add_manual_transaction(f'2026-{month}-08', '星巴克', starbucks, '支出', '餐饮')
        ledger.add_manual_transaction(f'2026-{month}-09', '饭馆', other, '支出', '餐饮')
        ledger.add_manual_transaction(f'2026-{month}-10', '星巴克', 9900, '支出', '交通')
    path = tmp_path / 'consult.sqlite3'
    before = path.read_bytes()

    def ask(text, previous=None, client=None):
        return query_ledger(path, text, client, Event(), previous=previous)

    first = ask('2026-10 餐饮净支出多少？')
    second = ask('那九月呢？', first)
    assert second['scope'] == '2026-09' and '12.00 元' in second['answer']
    third = ask('只看星巴克', second)
    assert third['scope'] == '2026-09' and '9.00 元' in third['answer']
    diff = ask('这两个月差多少？', third)
    assert diff['status'] == 'ok' and '-8.00 元' in diff['answer']
    assert {row['category'] for row in diff['evidence']} == {'餐饮'}
    assert {row['merchant'] for row in diff['evidence']} == {'星巴克'}
    assert ask('那八月呢？', third)['status'] == 'missing'
    assert ask('这两个月差多少？', first)['status'] == 'unsupported'
    assert ask('只看星巴克')['status'] == 'unsupported'

    class Model:
        def plan_ledger_follow_up(self, payload):
            assert payload == {'question': '换个观察月份用九月看看', 'period': '2026-10', 'metric': 'expense'}
            return {'action': 'month', 'value': '九月'}

    modeled = ask('换个观察月份用九月看看', first, Model())
    assert modeled['parser'] == '模型理解追问 + 代码计算' and modeled['scope'] == '2026-09'

    class Invalid:
        def plan_ledger_follow_up(self, payload):
            return {'action': 'delete', 'value': '九月'}

    assert ask('那能不能用九月的来看看', first, Invalid())['status'] == 'unsupported'
    assert path.read_bytes() == before
