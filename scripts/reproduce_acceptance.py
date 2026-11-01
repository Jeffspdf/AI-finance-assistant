"""Deterministic functional evidence; uses a temporary database only."""
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
from io import BytesIO
import json,sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from openpyxl import Workbook
from finance.storage import Store
from finance.importer import parse_statement
from finance.demo import demo_bytes
from finance.analytics import overview,budget_report,estimate_from_records
from finance.qa import answer_question

def main():
    directory=ROOT/'samples'/'acceptance';directory.mkdir(parents=True,exist_ok=True)
    normal=demo_bytes(date(2026,10,9));errors=demo_bytes(date(2026,10,9),errors=True)
    (directory/'review_demo.csv').write_bytes(normal);(directory/'review_errors.csv').write_bytes(errors)
    with TemporaryDirectory(prefix='.test_acceptance_',dir=ROOT) as temp:
        store=Store(Path(temp)/'acceptance.sqlite3')
        result=parse_statement(normal,'review_demo.csv');assert len(result.rows)==100
        assert store.import_rows(result.rows)==(100,0,[])
        assert store.import_rows(result.rows)==(0,100,[])
        invalid=parse_statement(errors,'review_errors.csv');assert len(invalid.issues)==4
        store.save_budget('2026-09','餐饮',10000)
        report=budget_report(store.transactions(),'2026-09',store.budgets('2026-09')).iloc[0]
        assert report['已支出']==145 and report['超支']==45
        answer=answer_question(store.transactions(),'上个月餐饮超支多少？',date(2026,10,9),budgets={'2026-09':store.budgets('2026-09')})
        assert answer['status']=='ok' and len(answer['evidence'])==6
        estimate=estimate_from_records(store.transactions(),date(2026,10,9));assert estimate['average_expense_cents']==340333
        book=Workbook();sheet=book.active
        sheet.append(['交易ID','日期','商户','金额','类型','平台','币种']);sheet.append(['XLSX-1','2026-09-01','书店','10','支出','验收','CNY'])
        buffer=BytesIO();book.save(buffer);book.close()
        assert len(parse_statement(buffer.getvalue(),'sample.xlsx').rows)==1
        output=dict(reference_date='2026-10-09',synthetic_records=100,duplicate_rows=100,rejected_rows=4,
                    september_food_expense=145,september_food_budget=100,september_food_overrun=45,september_food_evidence_count=6,
                    months=estimate['months'],average_expense_cents=estimate['average_expense_cents'],xlsx='passed')
    (directory/'review_results.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(output,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
