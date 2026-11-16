from hashlib import sha256
from io import BytesIO
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

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
