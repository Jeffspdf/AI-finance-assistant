from pathlib import Path
import pytest
from finance.projection_contract import projection_violations

ROOT=Path(__file__).resolve().parents[1]


def test_product_projection_contract():
    assert projection_violations(ROOT)==[]


@pytest.mark.parametrize('code',[
    "def budget(store):\n    frame=store.transactions()\n    return frame.amount_cents.sum()\n",
    "def overview(store):\n    getter=store.transactions\n    return getter().amount_cents.sum()\n",
    "def plan(store):\n    return getattr(store,'transactions')()\n",
    "def page(store):\n    return store.source_transactions().amount_cents.sum()\n",
    "def page(db):\n    return db.execute('SELECT SUM(amount_cents) FROM transactions')\n",
    "def budget(store):\n    store.spending_view()\n    raw=store.transactions()\n    return raw.amount_cents.sum()\n",
    "def page(store,db):\n    return store._source_frame(db).amount_cents.sum()\n",
    "def page(store):\n    method='transactions'\n    return getattr(store,method)()\n",
    "def page(pd,db):\n    return pd.read_sql_query('SELECT * FROM transactions',db)\n",
])
def test_new_product_pages_cannot_bypass_the_projection(tmp_path,code):
    folder=tmp_path/'finance';folder.mkdir();(folder/'new_page.py').write_text(code,encoding='utf-8')
    assert projection_violations(tmp_path,check_entrypoints=False)


@pytest.mark.parametrize('calculation',[
    'raw.amount_cents.sum()',
    "alias[alias.kind=='支出'].amount_cents.sum()",
    'overview(alias)',
    "alias.groupby('category').amount_cents.aggregate('sum')",
    'alias.amount_cents.mean()',
    'sum(row.amount_cents for row in alias.itertuples())',
])
def test_permitted_source_reader_cannot_start_calculating_money(tmp_path,calculation):
    (tmp_path/'desktop.py').write_text('class Desktop:\n    def records(self):\n        raw=self.store.source_transactions()\n        alias=raw.copy()\n        return '+calculation+'\n',encoding='utf-8')
    violations=projection_violations(tmp_path,check_entrypoints=False)
    assert any('源记录入口不得' in row for row in violations)


def test_permitted_source_display_and_net_summary_are_distinct(tmp_path):
    (tmp_path/'desktop.py').write_text('class Desktop:\n    def records(self):\n        raw=self.store.source_transactions()\n        self.table(raw.to_dict("records"))\n        return spending_totals(self.store.spending_view())\n',encoding='utf-8')
    assert projection_violations(tmp_path,check_entrypoints=False)==[]


def test_source_helper_alias_cannot_feed_a_web_statistic(tmp_path):
    (tmp_path/'app.py').write_text("def _source_records(store):\n    return store.source_transactions()\nraw=_source_records(store)\nsubset=raw[raw.kind=='支出']\nvalue=subset.amount_cents.sum()\n",encoding='utf-8')
    assert any('源记录入口不得' in row for row in projection_violations(tmp_path,check_entrypoints=False))


def test_deleting_net_entrypoint_is_a_contract_failure(tmp_path):
    assert any('缺少受保护' in row for row in projection_violations(tmp_path))
