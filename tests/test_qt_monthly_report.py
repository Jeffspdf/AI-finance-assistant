from threading import Event
from finance.storage import Store
from qt_finance.services import query_ledger


def test_investigate_chain(tmp_path):
    path = tmp_path/'ledger.sqlite3'
    store = Store(path)
    for month in ('09','10'):
        for name,category,amount in [('食堂','餐饮',6000),('咖啡','餐饮',2500),('地铁','交通',1000)]:
            store.add_manual_transaction(f'2026-{month}-02',name,amount,'支出',category)
    store.save_budget('2026-10','餐饮',10000)
    original = path.read_bytes()
    def ask(q,p=None):
        return query_ledger(path,q,None,Event(),previous=p)
    rank = ask('十月钱主要花哪儿了')
    assert rank['amounts'][0] == ('餐饮',8500)
    merchant = ask('餐饮里哪家店最多？',rank)
    assert merchant['merchant_leaders'] == ['食堂']
    excl = ask('除了这家还花了多少？',merchant)
    assert excl['amounts'] == [('净支出',2500)]
    rows = ask('把这几笔列给我',excl)
    assert [r['merchant'] for r in rows['detail_rows']] == ['咖啡']
    follow = ask('那九月呢？',rows)
    assert follow['scope']=='2026-09' and follow['detail_rows'][0]['merchant']=='咖啡'
    assert ask('十月花了多少，不包含餐饮')['amounts'][0][1]==1000
    assert ask('十月餐饮除了食堂以外花了多少')['amounts'][0][1]==2500
    assert ask('十月预算还剩多少')['amounts'][-1][1]==1500
    assert ask('十月收入和支出各多少')['amounts'][1][1]==9500
    assert '实际退款发生月' in ask('十月退款有多少')['answer']
    assert path.read_bytes()==original

def test_list_limit_preserves_total_and_all_evidence(tmp_path):
    path = tmp_path/'detail.sqlite3'
    store = Store(path)
    for number in range(24):
        store.add_manual_transaction('2026-10-08',f'食堂窗口{number}',100,'支出','餐饮')
    before = path.read_bytes()
    result = query_ledger(path,'十月餐饮交易明细有哪些',None,Event())
    assert result['amounts'] == [('净支出',2400)]
    assert len(result['detail_rows']) == 20 and len(result['evidence']) == 24
    assert '20 / 24' in result['amount_note']
    assert path.read_bytes() == before


def test_tied_merchants_require_explicit_exclusion(tmp_path):
    path = tmp_path/'tie.sqlite3'
    store = Store(path)
    for name in ('甲','乙'):
        store.add_manual_transaction('2026-10-01',name,900,'支出','餐饮')
    ranked = query_ledger(path,'十月哪家店花得最多',None,Event())
    assert set(ranked['merchant_leaders']) == {'甲','乙'}
    ambiguous = query_ledger(path,'除了这家还花了多少',None,Event(),previous=ranked)
    assert ambiguous['status'] == 'unsupported' and not ambiguous['evidence']


def test_category_share_and_position_use_local_net_expense(tmp_path):
    path = tmp_path/'share.sqlite3'
    store = Store(path)
    rows = [('餐饮', 23880), ('购物', 270660), ('居住', 120000),
            ('娱乐', 25900), ('交通', 5300)]
    for index, (category, cents) in enumerate(rows):
        store.add_manual_transaction(f'2026-10-{index + 1:02d}', '合成店', cents, '支出', category)

    share = query_ledger(path, '2026年10月餐饮占全部净支出多少？', None, Event())
    assert share['status'] == 'ok'
    assert '5.36%' in share['answer']
    assert share['amounts'] == [('餐饮', 23880), ('总净支出', 445740)]
    assert share['query_state']['category'] == '餐饮'
    assert share['query_text'] == '2026年10月餐饮占全部净支出多少？'

    position = query_ledger(path, '2026-10 交通在支出分类里排第几？', None, Event())
    assert position['status'] == 'ok'
    assert '第 5 位' in position['answer']
    assert position['query_state']['category'] == '交通'
