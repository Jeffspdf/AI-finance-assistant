from qt_finance.query_errors import (
    QueryFailure,
    classify_query_error,
    query_failure,
)


def test_query_failure_has_code_and_actionable_hint():
    result = query_failure(QueryFailure.MISSING_PERIOD, '要查询哪个月？')
    assert result['error']['code'] == 'missing_period'
    assert '缺少可确定的月份' in result['error']['hint']


def test_legacy_results_are_classified_by_failure_cause():
    timeout = classify_query_error(
        dict(status='unsupported', parser='模型不可用，回退本地解析',
             answer='连接超时：60 秒内没有完成响应。')
    )
    assert timeout['error']['code'] == 'model_timeout'

    bad_format = classify_query_error(
        dict(status='unsupported', parser='模型未完成条件解析',
             answer='目前无法可靠理解这个问题。')
    )
    assert bad_format['error']['code'] == 'model_bad_format'

    no_match = classify_query_error(
        dict(status='no_match', parser='本地解析', answer='没有符合条件的记录。')
    )
    assert no_match['error']['code'] == 'no_match'
