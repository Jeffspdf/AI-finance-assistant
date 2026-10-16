from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO, StringIO
import re
import pandas as pd
from .categories import classify

ALIASES = {
    'date': ['日期','交易时间','交易日期','记账日期','date','datetime'],
    'amount': ['金额','金额(元)','金额（元）','交易金额','收支金额','amount'],
    'merchant': ['商户','交易对方','对方名称','商户名称','merchant'],
    'description': ['描述','商品','商品名称','交易说明','备注','摘要','description'],
    'kind': ['类型','收支','收/支','收支类型','交易类型','kind','type'],
    'external_id': ['交易ID','交易id','交易单号','交易订单号','订单号','流水号','transaction_id','id'],
    'channel': ['平台','渠道','支付平台','channel','platform'],
    'status': ['状态','当前状态','交易状态','status'],
    'currency': ['币种','货币','currency'],
}

def money_to_cents(value, allow_zero=False):
    text = str(value).strip().replace(',', '').replace('￥', '').replace('¥', '').replace('元', '')
    try:
        amount = Decimal(text)
        if not amount.is_finite() or amount != amount.quantize(Decimal('.01')):
            raise ValueError('金额须为最多两位小数的有限数字')
        cents = int(amount * 100)
        if abs(cents) > 10**12 or (not allow_zero and cents == 0):
            raise ValueError('金额为零或超出支持范围')
        return cents
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError('非法金额') from exc

@dataclass
class ImportResult:
    rows: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    skipped: int = 0
    warnings: list = field(default_factory=list)

def map_columns(columns):
    clean = {str(c).strip().lstrip('\ufeff'):c for c in columns}
    return {key: next((clean[a] for a in aliases if a in clean), None) for key,aliases in ALIASES.items()}

def read_statement(content: bytes, filename: str):
    if len(content) > 20 * 1024 * 1024: raise ValueError('文件超过 20 MB')
    suffix = filename.rsplit('.',1)[-1].lower()
    if suffix == 'xlsx':
        raw = pd.read_excel(BytesIO(content), header=None, dtype=str, keep_default_na=False, engine='openpyxl')
        for idx in range(min(60,len(raw))):
            mapping = map_columns(raw.iloc[idx].tolist())
            if mapping['date'] and mapping['amount']:
                frame = raw.iloc[idx+1:].copy()
                frame.columns = [str(v).strip() for v in raw.iloc[idx]]
                return frame.reset_index(drop=True), idx+2
        raise ValueError('Excel 未找到日期和金额列；支持第一个工作表前 60 行内的表头')
    if suffix != 'csv': raise ValueError('只支持 CSV 和 XLSX 文件')
    text = None
    for encoding in ['utf-8-sig', 'gb18030']:
        try: text = content.decode(encoding); break
        except UnicodeDecodeError: continue
    if text is None: raise ValueError('文件编码无法识别，请导出为 UTF-8 或 GB18030')
    lines = text.splitlines()
    for idx,line in enumerate(lines[:60]):
        delimiter = '\t' if '\t' in line and ',' not in line else ','
        import csv
        cols = next(csv.reader([line], delimiter=delimiter), [])
        mapping = map_columns(cols)
        if mapping['date'] and mapping['amount']:
            try:
                frame = pd.read_csv(StringIO('\n'.join(lines[idx:])), sep=delimiter, dtype=str, keep_default_na=False)
            except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
                raise ValueError('CSV 行结构不一致，请按模板修正后重试') from exc
            return frame, idx+2
    raise ValueError('缺少日期或金额列，请使用模板；银行自定义格式需先转换列名')

def parse_statement(content, filename):
    frame, first_row = read_statement(content, filename)
    if len(frame)>50000: raise ValueError('单次最多导入 50000 行')
    mapping = map_columns(frame.columns)
    result = ImportResult()
    if not mapping['kind']:
        result.warnings.append('缺少收支类型列：正金额按收入、负金额按支出。请核对导入预览。')
    if not mapping['external_id']:
        result.warnings.append('缺少交易 ID：按日期时间、金额、收支、商户、描述和平台识别重复；完全相同的真实交易可能被合并。')
    for offset, (_, source) in enumerate(frame.iterrows()):
        row_number = first_row + offset
        def get(key):
            col=mapping.get(key)
            return str(source[col]).strip() if col is not None else ''
        try:
            if not get('date') and not get('amount'):
                result.skipped += 1; continue
            status = get('status')
            if any(w in status for w in ['失败','关闭','已撤销','已退款','退款成功']):
                result.skipped += 1; continue
            if '退款' in status or '退款' in get('kind'):
                raise ValueError('退款记录需人工核对，当前不自动计入')
            currency=get('currency').upper()
            if currency and currency not in ['CNY','RMB','人民币','元','¥','￥']:
                raise ValueError('仅支持人民币，不能混入外币')
            date_text=get('date')
            if not date_text: raise ValueError('缺少日期')
            if re.fullmatch(r'\d+(\.\d+)?',date_text):
                if not re.fullmatch(r'20\d{6}',date_text): raise ValueError('日期格式不明确，请使用 YYYY-MM-DD')
                date_text=f'{date_text[:4]}-{date_text[4:6]}-{date_text[6:]}'
            dt = pd.to_datetime(date_text, errors='coerce')
            if pd.isna(dt) or dt.tzinfo is not None or not (2000<=dt.year<=2100):
                raise ValueError('非法日期或时区，支持 2000 至 2100 年本地日期')
            date = dt.strftime('%Y-%m-%d %H:%M:%S')
            signed = money_to_cents(get('amount'))
            kind_text=get('kind')
            if kind_text in ['收入','收','income','Income']: kind='收入'
            elif kind_text in ['支出','支','expense','Expense']: kind='支出'
            elif kind_text in ['转账','不计收支','中性交易','transfer','Transfer','/']: kind='转账'
            elif not kind_text: kind='收入' if signed>0 else '支出'
            else: raise ValueError('收支类型无法识别；请改为收入、支出或转账')
            merchant, description=get('merchant'),get('description')
            if not merchant and not description: raise ValueError('商户与描述至少填写一项')
            ext=get('external_id'); channel=get('channel') or '未指定'
            category, origin=classify(merchant,description,kind)
            if ext:
                identity=f'external|{channel}|{ext}'
            else:
                identity='fingerprint|'+'|'.join([date,str(abs(signed)),kind,merchant,description,channel])
            signature=sha256(identity.encode()).hexdigest()
            result.rows.append(dict(id='TX-'+signature[:20],external_id=ext,date=date,merchant=merchant,description=description,amount_cents=abs(signed),kind=kind,category=category,category_source=origin,channel=channel,source_file=filename,source_row=row_number,signature=signature))
        except (ValueError, TypeError) as exc:
            result.issues.append({'行号':row_number,'原因':str(exc),'日期':get('date'),'金额':get('amount'),'商户':get('merchant')})
    return result
