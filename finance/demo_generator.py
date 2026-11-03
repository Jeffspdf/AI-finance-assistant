"""Configurable, local-only synthetic data. Writes only to the demo ledger."""
from calendar import monthrange
from dataclasses import dataclass
from io import StringIO
import csv
import random
import re
import uuid

from .demo import CASES
from .importer import parse_statement
from .reconciliation import audit


@dataclass(frozen=True)
class DemoOptions:
    start_month: str
    months: int = 3
    expenses_per_month: int = 30
    expense_min_cents: int = 1000
    expense_max_cents: int = 20000
    income_cents: int = 300000
    income_kind: str = '生活费'
    transfers: bool = True
    refunds: bool = False
    replace_months: bool = False

    def month_list(self):
        if not isinstance(self.start_month,str) or not re.fullmatch(r'(?:20\d{2}|2100)-(?:0[1-9]|1[0-2])',self.start_month):
            raise ValueError('起始月份请填写 YYYY-MM（2000 至 2100 年）')
        if type(self.months) is not int or not 1<=self.months<=12:raise ValueError('生成月数须为 1 至 12')
        if type(self.expenses_per_month) is not int or not 1<=self.expenses_per_month<=500:raise ValueError('每月支出笔数须为 1 至 500')
        if any(type(x) is not int for x in (self.expense_min_cents,self.expense_max_cents,self.income_cents)):
            raise ValueError('金额须使用整数分')
        if not 0<self.expense_min_cents<=self.expense_max_cents<=100000000:
            raise ValueError('单笔支出范围须大于零，上限不超过 100 万元，最小值不能大于最大值')
        if not 0<=self.income_cents<=1000000000:raise ValueError('每月收入须为 0 至 1000 万元，0 表示不生成收入')
        if self.income_kind not in ('生活费','工资'):raise ValueError('请选择生活费或工资')
        if any(type(x) is not bool for x in (self.transfers,self.refunds,self.replace_months)):raise ValueError('无效的生成选项')
        year,month=map(int,self.start_month.split('-'));start=year*12+month-1
        result=[f'{index//12:04d}-{index%12+1:02d}' for index in range(start,start+self.months)]
        if int(result[-1][:4])>2100:raise ValueError('生成范围不能超出 2100 年')
        return result

    def summary(self):
        months=self.month_list()
        count=self.months*(self.expenses_per_month+(self.income_cents>0)+self.transfers+self.refunds)
        return dict(months=months,count=count,mode='replace' if self.replace_months else 'append')


def statement(options,*,seed=None):
    """Return an import preview with unique identities for this generation batch."""
    months=options.month_list();rng=random.Random(seed);run=uuid.uuid4().hex
    expenses=[r for r in CASES if r[3]=='支出']
    stream=StringIO();writer=csv.writer(stream)
    writer.writerow(['交易ID','日期','商户','描述','金额','类型','平台','币种','状态'])
    def write(identifier,when,merchant,description,cents,kind,status='成功'):
        writer.writerow([identifier,when,merchant,description,f'{cents//100}.{cents%100:02d}',kind,'演示生成','CNY',status])
    for month in months:
        year,number=map(int,month.split('-'));days=monthrange(year,number)[1];first=None
        for i in range(options.expenses_per_month):
            merchant,description,_,_,_=rng.choice(expenses)
            cents=rng.randint(options.expense_min_cents,options.expense_max_cents)
            identifier=f'GEN-{run}-{month}-E{i+1}'
            when=f'{month}-{rng.randint(1,days):02d} {rng.randint(8,22):02d}:{rng.randint(0,59):02d}:00'
            write(identifier,when,merchant,'演示消费 · '+description,cents,'支出')
            if first is None:first=(identifier,when,merchant,cents)
        if options.income_cents:
            write(f'GEN-{run}-{month}-IN',month+'-01 09:00:00','家人' if options.income_kind=='生活费' else '公司财务',
                  '演示'+options.income_kind,options.income_cents,'收入')
        if options.transfers:
            write(f'GEN-{run}-{month}-TR',month+'-02 10:00:00','自己的账户','演示账户转账',50000,'转账')
        if options.refunds:
            identifier,when,merchant,cents=first
            write(f'GEN-{run}-{month}-RF',when,merchant,'演示退款 · 原单 '+identifier,max(1,cents//2),'收入','退款成功')
    filename=f'演示生成_{months[0]}至{months[-1]}_{run[:8]}.csv'
    result=parse_statement(stream.getvalue().encode('utf-8-sig'),filename,retain_source=True)
    if result.issues or len(result.rows)!=options.summary()['count']:raise ValueError('生成的演示记录未通过校验，未保存')
    return result


def generate_demo(store,options,*,space,seed=None):
    from pathlib import Path
    if space!='demo' or Path(store.path).name.lower()!='demo.sqlite3':
        raise ValueError('仅允许在演示空间生成账单')
    summary=options.summary();result=statement(options,seed=seed)
    # The caller can preview this count before confirming replacement.
    removed=0
    with store.connect() as db:
        if options.replace_months:
            placeholders=','.join('?' for _ in summary['months'])
            ids=[r[0] for r in db.execute(f"SELECT id FROM transactions WHERE substr(date,1,7) IN ({placeholders}) AND status<>'deleted'",summary['months'])]
            for txid in ids:store.invalidate_groups(db,txid,'演示生成替换了所选月份')
            if ids:
                db.execute(f"UPDATE transactions SET status='deleted' WHERE substr(date,1,7) IN ({placeholders}) AND status<>'deleted'",summary['months'])
            removed=len(ids)
        added,duplicates,conflicts=store._import_statement(db,result)
        if duplicates or conflicts:raise ValueError('生成的记录存在身份冲突，整批操作已撤销')
        audit(db,'demo_generated',result.document['sha256'],dict(months=summary['months'],added=added,removed=removed,replace=options.replace_months))
    return dict(**summary,added=added,removed=removed)


def verify_generator(directory):
    """Acceptance path used only by the explicitly isolated executable self-test."""
    from dataclasses import replace
    from pathlib import Path
    from .storage import Store
    folder=Path(directory)/('generator-self-test-'+uuid.uuid4().hex)
    store=Store(folder/'demo.sqlite3')
    options=DemoOptions('2024-02',2,4,1234,1234,200000,refunds=True)
    first=generate_demo(store,options,space='demo',seed=3)
    assert first['added']==14 and set(store.source_transactions().date.str[:7])=={'2024-02','2024-03'}
    assert store.pending_refunds()['count']==2
    projected=store.transactions()
    assert int(projected.loc[projected.kind=='支出','amount_cents'].sum())==9872
    second=generate_demo(store,replace(options,months=1,expenses_per_month=5,replace_months=True),space='demo',seed=4)
    assert second['removed']==7 and second['added']==8 and len(store.source_transactions())==15
    personal=Store(folder/'personal.sqlite3')
    try:generate_demo(personal,options,space='demo')
    except ValueError:pass
    else:raise AssertionError('Personal ledger accepted demo generation')
    assert personal.source_transactions().empty
    return dict(data='synthetic only',months=first['months'],generated_rows=14,expense_cents=9872,
                replacement_removed=7,replacement_added=8,personal_unchanged=True)
