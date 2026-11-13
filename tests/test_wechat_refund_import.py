import pytest

from finance.importer import parse_statement
from finance.storage import Store


WECHAT = """交易时间,交易类型,交易对方,商品,收/支,金额(元),当前状态,交易单号
2026-09-01 12:00:00,商户消费,淘宝,商品,支出,100,已退款,BUY
2026-09-02 12:00:00,转账,同学,代垫回款,收入,120,支付成功,IN
2026-09-03 12:00:00,退款,淘宝,退款到账,收入,29.90,支付成功,WX011
2026-09-04 12:00:00,退款,淘宝,退款失败,收入,19,退款失败,FAILED
2026-09-05 12:00:00,转账,同学,退款相关转账备注,收入,10,支付成功,NOTE
""".encode()


@pytest.mark.parametrize('adapter', ['auto', 'generic', 'wechat_csv'])
def test_wechat_explicit_type_with_income_direction(adapter):
    parsed = parse_statement(WECHAT, '微信账单.csv', retain_source=True, adapter=adapter)
    rows = {row['external_id']: row for row in parsed.rows}
    assert not parsed.issues and parsed.skipped == 1
    assert rows['WX011']['kind'] == '收入'
    assert rows['WX011']['status'] == 'refund_pending'
    assert rows['BUY']['status'] == 'review'
    assert rows['NOTE']['status'] == 'normal'


@pytest.mark.parametrize('wechat', [True, False])
def test_refund_excluded_from_income_and_only_deducted_after_link(tmp_path, wechat):
    alipay = ('交易时间,交易类型,交易对方,商品说明,收/支,金额,交易状态,交易订单号\n'
              '2026-09-01,商户消费,淘宝,商品,支出,100,交易成功,BUY\n'
              '2026-09-02,转账,同学,代垫回款,收入,130,交易成功,IN\n'
              '2026-09-03,退款,淘宝,退款到账,收入,29.90,退款成功,WX011\n').encode()
    content = WECHAT.replace('已退款'.encode(), '支付成功'.encode()) if wechat else alipay
    parsed = parse_statement(content, '账单.csv', retain_source=True, adapter='auto')
    store = Store(tmp_path / 'ledger.sqlite3')
    store.import_statement(parsed)
    frame = store.spending_view()
    assert frame.loc[frame.kind == '收入', 'amount_cents'].sum() == 13000
    refund, = store.refund_sources()
    assert refund['amount_cents'] == 2990 and refund['association_status'] == 'pending'
    ids = {row['external_id']: row['id'] for row in parsed.rows}
    store.confirm_refund_link(ids['WX011'], ids['BUY'])
    assert store.spending_view().query("kind == '支出'").amount_cents.sum() == 7010
    assert store.import_statement(parsed).refunds_corrected == 0


def test_exact_original_reimport_repairs_already_imported_refund(tmp_path):
    parsed = parse_statement(WECHAT, '微信账单.csv', adapter='wechat_csv')
    refund = next(row for row in parsed.rows if row['external_id'] == 'WX011')
    identifier = refund['id']
    store = Store(tmp_path / 'ledger.sqlite3')
    refund['status'] = 'normal'
    store.import_statement(parsed)
    refund['status'] = 'refund_pending'
    before = (tmp_path / 'ledger.sqlite3').read_bytes()
    assert store.preview_import(parsed)['refunds_to_correct'] == 1
    assert (tmp_path / 'ledger.sqlite3').read_bytes() == before
    assert store.import_statement(parsed).refunds_corrected == 1
    assert store.transaction_source(identifier)['status'] == 'refund_pending'
    assert store.import_statement(parsed).refunds_corrected == 0
    with store.connect(read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM audit_events WHERE event='import_refund_corrected'").fetchone()[0] == 1
