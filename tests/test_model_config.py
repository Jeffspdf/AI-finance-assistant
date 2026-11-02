import json
from unittest.mock import patch
import pytest
from finance.llm import ModelClient,ModelResponseError,connection_error_message
from urllib.error import HTTPError,URLError


@pytest.mark.parametrize('url',['http://remote.example/v1','https://x.example/v1?key=secret','https://user:pass@x.example','file:///tmp/x'])
def test_reject_unsafe_url(url):
    with pytest.raises(ValueError): ModelClient(url,'test',enabled=True)


def test_connection_request():
    with patch('finance.llm.urlopen') as request:
        response=request.return_value.__enter__.return_value
        response.read.return_value=json.dumps({'choices':[{'message':{'content':'{"ok": true}'}}]}).encode()
        assert ModelClient('https://example.com/v1','test','secret',enabled=True).test_connection()
        sent=request.call_args.args[0]
        assert sent.full_url=='https://example.com/v1/chat/completions'
        assert sent.get_header('Authorization')=='Bearer secret'
        assert json.loads(sent.data)['model']=='test'
        assert 'merchant' not in sent.data.decode()


def test_invalid_connection_response():
    with patch.object(ModelClient,'_json',return_value={'ok':False}):
        with pytest.raises(ValueError): ModelClient('http://localhost:11434/v1','test',enabled=True).test_connection()


def test_deepseek_uses_short_non_thinking_json_test_only_on_official_host():
    with patch('finance.llm.urlopen') as request:
        request.return_value.__enter__.return_value.read.return_value=json.dumps({'choices':[{'message':{'content':'{"ok": true}'},'finish_reason':'stop'}]}).encode()
        assert ModelClient('https://api.deepseek.com/','deepseek-flash','synthetic-test-only',enabled=True).test_connection()
        payload=json.loads(request.call_args.args[0].data)
        assert payload['thinking']=={'type':'disabled'} and payload['response_format']=={'type':'json_object'}
        assert payload['max_tokens']==128 and payload['stream'] is False
        assert request.call_args.kwargs['timeout']==60
        assert ModelClient('https://other.example/v1','deepseek-flash','synthetic-test-only',enabled=True).test_connection()
        payload=json.loads(request.call_args.args[0].data)
        assert 'thinking' not in payload and 'response_format' not in payload


@pytest.mark.parametrize('body,message',[
    ({'choices':[{'message':{'content':None}}]},'没有返回有效正文'),
    ({'choices':[{'message':{'content':'[1,2]'}}]},'不是 JSON 对象'),
    ({'choices':[{'message':{'content':'{"ok":'},'finish_reason':'length'}]},'长度限制'),
    ({'choices':[]},'返回格式'),
])
def test_response_failures_are_specific(body,message):
    with patch('finance.llm.urlopen') as request:
        request.return_value.__enter__.return_value.read.return_value=json.dumps(body).encode()
        with pytest.raises(ModelResponseError,match=message):
            ModelClient('https://example.com/v1','test',enabled=True).test_connection()


def test_error_messages_distinguish_auth_balance_timeout_and_format_without_secrets():
    assert '密钥' in connection_error_message(HTTPError('https://example.com',401,'secret-response',{},None))
    assert '余额不足' in connection_error_message(HTTPError('https://example.com',402,'secret-response',{},None))
    assert '超时' in connection_error_message(URLError(TimeoutError('private-proxy-token')))
    assert '有效正文' in connection_error_message(ModelResponseError('接口没有返回有效正文'))
    assert 'secret-response' not in connection_error_message(HTTPError('https://example.com',401,'secret-response',{},None))
