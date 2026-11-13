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
    'description': ['描述','商品','商品名称','商品说明','交易说明','备注','摘要','description'],
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
    raw_rows: list = field(default_factory=list)
    document: dict = field(default_factory=dict)
    adapter: str = 'generic'


WECHAT_COLUMNS={'交易时间','交易对方','收/支','金额(元)','当前状态','交易单号'}
BANK_COLUMNS={'流水号','交易时间','借方发生额','贷方发生额','对方名称','摘要','币种','业务类型'}


def detect_adapter(columns,filename):
    columns={str(c).strip() for c in columns}
    if filename.lower().endswith('.csv') and WECHAT_COLUMNS<=columns:return 'wechat_csv'
    if filename.lower().endswith('.xlsx') and BANK_COLUMNS<=columns:return 'bank_template_v1'
    return 'generic'

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
            if mapping['date'] and (mapping['amount'] or {'借方发生额','贷方发生额'}<=set(raw.iloc[idx])):
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

def parse_statement(content, filename, *, retain_source=False, adapter='generic', account_id=None):
    frame, first_row = read_statement(content, filename)
    original_frame=frame.copy() if retain_source or adapter!='generic' else None
    if len(frame)>50000: raise ValueError('单次最多导入 50000 行')
    if adapter=='auto':
        adapter=detect_adapter(frame.columns,filename);retain_source=True
    if adapter not in ('generic','wechat_csv','bank_template_v1'):raise ValueError('不支持的账单格式')
    if adapter=='wechat_csv':
        if not filename.lower().endswith('.csv') or not WECHAT_COLUMNS<=set(frame.columns):
            raise ValueError('微信格式需含交易时间、交易对方、收/支、金额(元)、当前状态、交易单号列')
        frame=frame.copy();frame['平台']='微信'
    if adapter=='bank_template_v1':
        required=BANK_COLUMNS
        if not filename.lower().endswith('.xlsx') or not required<=set(frame.columns):
            raise ValueError('请选择银行模板 v1 XLSX（借方发生额/贷方发生额），其他银行格式需先转换')
        frame=frame.copy();amounts=[];kinds=[]
        for _,r in frame.iterrows():
            try:
                debit=money_to_cents(r['借方发生额'] or '0',True);credit=money_to_cents(r['贷方发生额'] or '0',True)
                if debit<0 or credit<0 or bool(debit)==bool(credit):raise ValueError('借贷必须且仅有一项为正')
                value=debit or credit;amounts.append(f'{value//100}.{value%100:02d}')
                kinds.append('退款' if r['业务类型']=='退款' else '转账' if r['业务类型'] in ('转账','信用卡还款','充值','提现') else '支出' if debit else '收入')
            except ValueError:amounts.append('借贷金额无效');kinds.append('')
        frame['金额']=amounts;frame['类型']=kinds;frame['平台']='银行'
    if adapter!='generic':retain_source=True
    mapping = map_columns(frame.columns)
    wechat_layout = WECHAT_COLUMNS <= set(frame.columns)
    result = ImportResult(adapter=adapter)
    if retain_source:
        result.document=dict(sha256=sha256(content).hexdigest(),filename=__import__('pathlib').Path(filename).name,
                             channel={'wechat_csv':'微信','bank_template_v1':'银行'}.get(adapter,'通用'),
                             account_id=account_id,parser_version='m1a-'+adapter+'-1')
    if not mapping['kind']:
        result.warnings.append('缺少收支类型列：正金额按收入、负金额按支出。请核对导入预览。')
    if not mapping['external_id']:
        result.warnings.append('缺少交易 ID：按日期时间、金额、收支、商户、描述和平台识别重复；完全相同的真实交易可能被合并。')
    for offset, (_, source) in enumerate(frame.iterrows()):
        row_number = first_row + offset
        wechat_type = str(source.get('交易类型', '')).strip() if wechat_layout else ''
        def get(key):
            col=mapping.get(key)
            return str(source[col]).strip() if col is not None else ''
        raw=None
        if retain_source:
            # Allowlist only the fields needed for reconciliation. Never retain arbitrary
            # exported columns (full card numbers, names, account balances, etc.).
            raw=dict(row_no=row_number,fields={k:get(k)[:500] for k in ALIASES if mapping.get(k) is not None},parse_status='parsed')
            if wechat_type:
                raw['fields']['交易类型']=wechat_type[:500]
            if adapter=='bank_template_v1':
                raw['fields']={k:str(original_frame.iloc[offset][k])[:500] for k in ('流水号','交易时间','借方发生额','贷方发生额','对方名称','摘要','币种','业务类型')}
            result.raw_rows.append(raw)
        try:
            if not get('date') and not get('amount'):
                if raw:raw['parse_status']='skipped'
                result.skipped += 1; continue
            status = get('status')
            kind_text = get('kind')
            # 微信 exports split the transaction type and the direction into two columns.
            explicit_wechat_refund = wechat_type in {
                '退款', '退款到账', '商户退款',
            } and kind_text in ('收入', '收', '/')
            refund_marker = explicit_wechat_refund or '退款' in kind_text or ('退款' in status and kind_text in ('收入','收'))
            refund = retain_source and refund_marker
            review=retain_source and not refund and '退款' in status
            if any(w in status for w in (['失败','关闭','已撤销','退款中','退款处理中'] if retain_source else ['失败','关闭','已撤销','已退款','退款成功'])):
                if raw:raw['parse_status']='skipped'
                result.skipped += 1; continue
            if not retain_source and (refund_marker or '退款' in status):
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
            if refund:kind='收入'
            elif kind_text in ['收入','收','income','Income']: kind='收入'
            elif kind_text in ['支出','支','expense','Expense']: kind='支出'
            elif kind_text in ['转账','不计收支','中性交易','transfer','Transfer','/']: kind='转账'
            elif not kind_text: kind='收入' if signed>0 else '支出'
            else: raise ValueError('收支类型无法识别；请改为收入、支出或转账')
            merchant, description=get('merchant'),get('description')
            if not merchant and not description: raise ValueError('商户与描述至少填写一项')
            ext=get('external_id'); channel=get('channel') or '未指定'
            if retain_source and kind!='转账' and any(w in merchant+description for w in ('信用卡还款','余额充值','提现')):
                review=True
            category, origin=classify(merchant,description,kind)
            if ext:
                identity=f'external|{channel}|{ext}'
            else:
                identity='fingerprint|'+'|'.join([date,str(abs(signed)),kind,merchant,description,channel])
            if retain_source:identity+='|account|'+(account_id or 'unspecified')
            signature=sha256(identity.encode()).hexdigest()
            result.rows.append(dict(id='TX-'+signature[:20],external_id=ext,date=date,merchant=merchant,description=description,amount_cents=abs(signed),kind=kind,category=category,category_source=origin,channel=channel,source_file=filename,source_row=row_number,signature=signature,
                                    currency='CNY',status='refund_pending' if refund else 'review' if review else 'normal',account_id=account_id))
            if raw:raw['parse_status']=result.rows[-1]['status']
        except (ValueError, TypeError) as exc:
            if raw:raw['parse_status']='issue'
            result.issues.append({'行号':row_number,'原因':str(exc),'日期':get('date'),'金额':get('amount'),'商户':get('merchant')})
    return result
