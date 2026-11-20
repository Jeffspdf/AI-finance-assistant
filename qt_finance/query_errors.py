"""Machine-readable query failure reasons and user-facing recovery hints."""
from enum import Enum
import re


class QueryFailure(Enum):
    MISSING_PERIOD = 'missing_period'
    AMBIGUOUS_PERIOD = 'ambiguous_period'
    NO_DATA = 'no_data'
    NO_MATCH = 'no_match'
    UNSUPPORTED_CONDITION = 'unsupported_condition'
    MODEL_TIMEOUT = 'model_timeout'
    MODEL_NETWORK = 'model_network'
    MODEL_BAD_FORMAT = 'model_bad_format'
    LEDGER_STALE = 'ledger_stale'


HINTS = {
    QueryFailure.MISSING_PERIOD: '缺少可确定的月份；请补充 YYYY-MM、本月或上个月。',
    QueryFailure.AMBIGUOUS_PERIOD: '同时出现多个时间范围，请只保留一个。',
    QueryFailure.NO_DATA: '该范围还没有已导入记录；没有记录不等于没有消费。',
    QueryFailure.NO_MATCH: '当前筛选条件下没有匹配记录，请调整分类、商户或月份。',
    QueryFailure.UNSUPPORTED_CONDITION: '当前查账能力暂不支持这个条件，请换一种更明确的问法。',
    QueryFailure.MODEL_TIMEOUT: '模型请求超时；可以重试，或关闭联网后使用本地查询。',
    QueryFailure.MODEL_NETWORK: '模型服务连接失败；请检查接口、网络或代理设置。',
    QueryFailure.MODEL_BAD_FORMAT: '模型返回格式不合格；该结果未被采用。',
    QueryFailure.LEDGER_STALE: '账本已变化；请重新说明查询或分析范围。',
}


def query_failure(code, message, *, choices=(), clarify=None):
    result = dict(
        status='unsupported',
        parser='本地解析',
        answer=message,
        error=dict(code=code.value, hint=HINTS[code]),
        choices=dict(choices),
        evidence=[],
    )
    if clarify:
        result['clarify'] = clarify
    return result


def add_error(result, code):
    """Attach a stable failure code without changing the verified local text."""
    if result is None:
        return None
    result['error'] = dict(code=code.value, hint=HINTS[code])
    return result


def classify_query_error(result):
    """Infer a useful failure class for legacy local and model result paths."""
    if not isinstance(result, dict) or result.get('status') == 'ok' or result.get('error'):
        return result
    status = result.get('status')
    answer = result.get('answer', '')
    parser = result.get('parser', '')
    if status == 'no_match':
        code = QueryFailure.NO_MATCH
    elif status == 'missing':
        code = QueryFailure.NO_DATA
    elif re.search(r'超时|timeout', answer, re.I):
        code = QueryFailure.MODEL_TIMEOUT
    elif re.search(r'格式|有效正文|长度限制', answer):
        code = QueryFailure.MODEL_BAD_FORMAT
    elif re.search(r'连接失败|网络|域名|证书|连接被拒绝|HTTP\s*\d+', answer, re.I):
        code = QueryFailure.MODEL_NETWORK
    elif '模型未完成条件解析' in parser:
        code = QueryFailure.MODEL_BAD_FORMAT
    elif '模型不可用' in parser:
        code = QueryFailure.MODEL_NETWORK
    elif re.search(r'多个年份|同时识别到|同时出现|哪一个', answer):
        code = QueryFailure.AMBIGUOUS_PERIOD
    elif re.search(r'请先指定|请明确.*月份|要查询哪个月|需要确认.*月份|月份', answer):
        code = QueryFailure.MISSING_PERIOD
    else:
        code = QueryFailure.UNSUPPORTED_CONDITION
    return add_error(result, code)
