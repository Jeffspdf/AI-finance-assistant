from datetime import date
from finance.qa import month_shift
from finance.storage import Store
from finance.analytics import budget_report


def test_future_budget_persists_without_inventing_transaction_coverage(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');future=month_shift(date.today(),1)
    store.save_budget(future,'餐饮',10000)
    reopened=Store(store.path);assert reopened.budgets(future)=={'餐饮':10000}
    frame=reopened.spending_view()
    assert frame.empty and future not in frame.attrs['source_months']
    report=budget_report(frame,future,reopened.budgets(future))
    assert report.iloc[0]['预算']==100 and report.iloc[0]['已支出']==0


def test_future_month_copy_does_not_change_current_month(tmp_path):
    store=Store(tmp_path/'ledger.sqlite3');current=date.today().strftime('%Y-%m');future=month_shift(date.today(),1)
    store.save_budget(current,'餐饮',10000);store.copy_budgets(current,future)
    store.save_budget(future,'餐饮',20000)
    assert store.budgets(current)=={'餐饮':10000} and store.budgets(future)=={'餐饮':20000}
