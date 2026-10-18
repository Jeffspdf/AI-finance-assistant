import json
from unittest.mock import patch
import pytest
from finance.llm import ModelClient


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
