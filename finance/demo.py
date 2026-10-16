from datetime import date
import csv
from io import StringIO
from .qa import month_shift

HEADER=['交易ID','日期','商户','描述','金额','类型','平台','币种','预期分类']
CASES=[
 ('麦当劳','午餐',45,'支出','餐饮'),('星巴克','咖啡',32,'支出','餐饮'),
 ('地铁出行','通勤',6,'支出','交通'),('滴滴出行','回家',35,'支出','交通'),
 ('京东商城','日用品',120,'支出','购物'),('服装店','衣服',150,'支出','购物'),
 ('公寓管理','房租',1200,'支出','居住'),('电费服务','电费',45,'支出','生活缴费'),
 ('图书书店','教材',65,'支出','教育'),('药店','感冒药',28,'支出','医疗'),
 ('电影院','电影票',40,'支出','娱乐'),('公司财务','工资',5000,'收入','工资'),
 ('家人','生活费',1800,'收入','生活费'),('自己的账户','账户转账',200,'转账','转账'),
 ('食堂','晚饭',18,'支出','餐饮'),('奶茶店','奶茶',16,'支出','餐饮'),
 ('公交集团','公交',2,'支出','交通'),('超市','日用品',60,'支出','购物'),
 ('宽带服务','宽带',80,'支出','生活缴费'),('健身中心','健身',100,'支出','娱乐'),
]

def demo_rows(today=None):
    today=today or date.today(); rows=[]
    for i in range(100):
        month=month_shift(today,-3+i//34)
        merchant,description,amount,kind,label=CASES[i%len(CASES)]
        rows.append([f'DEMO-{i+1:04d}',f'{month}-{i%28+1:02d} 12:{i%60:02d}:00',merchant,description,f'{amount:.2f}',kind,'样例','CNY',label])
    return rows

def demo_bytes(today=None, errors=False):
    rows=demo_rows(today)
    if errors:
        rows += [rows[0].copy(),rows[5].copy(),['BAD-1','bad-date','商店','日期错误','30','支出','样例','CNY',''],['BAD-2',rows[0][1],'商店','非法金额','abc','支出','样例','CNY',''],['BAD-3',rows[0][1],'商店','外币','50','支出','样例','USD',''],['BAD-4',rows[0][1],'','','50','支出','样例','CNY','']]
    stream=StringIO(); writer=csv.writer(stream); writer.writerow(HEADER); writer.writerows(rows)
    return stream.getvalue().encode('utf-8-sig')

if __name__=='__main__':
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'samples'; root.mkdir(exist_ok=True)
    today=date(2026,10,9)
    (root/'labeled_100.csv').write_bytes(demo_bytes(today))
    (root/'demo_with_errors.csv').write_bytes(demo_bytes(today,errors=True))
    stream=StringIO(); writer=csv.writer(stream); writer.writerow(HEADER[:-1]); writer.writerow(['MY-001','2026-09-01 12:00:00','食堂','午餐','25.50','支出','我的账单','CNY'])
    (root/'template.csv').write_bytes(stream.getvalue().encode('utf-8-sig'))
