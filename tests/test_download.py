from unittest.mock import MagicMock,patch
from io import BytesIO
import pytest
from finance.updates import download_installer,REPO
URL=f'https://github.com/{REPO}/releases/download/v0.4.0/AIFinanceHelper-0.4.0-Setup-x64.exe'

def response(content,length=None):
    mock=MagicMock(); r=mock.__enter__.return_value
    r.headers={'Content-Length':str(len(content) if length is None else length)}
    r.read.side_effect=BytesIO(content).read
    return mock

def test_complete_and_partial_cleanup(tmp_path):
    with patch('finance.updates._request',return_value=response(b'MZtest')):
        path,sha=download_installer(URL,tmp_path)
        assert len(sha)==64
        from pathlib import Path
        assert Path(path).read_bytes()==b'MZtest'
    for payload,size in [(b'<html>',6),(b'MZbroken',100)]:
        with patch('finance.updates._request',return_value=response(payload,size)):
            with pytest.raises(ValueError): download_installer(URL,tmp_path/'bad')
        assert not list((tmp_path/'bad').glob('*.part'))

def test_cancel_and_url_rejection(tmp_path):
    with pytest.raises(ValueError): download_installer('https://evil.example/a.exe',tmp_path)
    with patch('finance.updates._request',return_value=response(b'MZtest')):
        with pytest.raises(InterruptedError): download_installer(URL,tmp_path,cancelled=lambda:True)
    assert not list(tmp_path.glob('*.part'))

def test_hash_mismatch_rejects_file(tmp_path):
    with patch('finance.updates._request',return_value=response(b'MZtest')):
        with pytest.raises(ValueError,match='SHA256'):download_installer(URL,tmp_path,expected_sha256='0'*64)
    assert not list(tmp_path.iterdir())

def test_verify_release_checksum(tmp_path):
    import hashlib
    digest=hashlib.sha256(b'MZtest').hexdigest()
    with patch('finance.updates.published_checksum',return_value=digest) as manifest,patch('finance.updates._request',return_value=response(b'MZtest')):
        _,actual=download_installer(URL,tmp_path,verify_release=True)
        assert actual==digest and manifest.called


def test_transient_connect_failure_retries_once_and_preserves_checksum(tmp_path):
    from urllib.error import URLError
    import hashlib
    digest=hashlib.sha256(b'MZtest').hexdigest()
    with patch('finance.updates._request',side_effect=[URLError(TimeoutError()),response(b'MZtest')]) as request:
        _,actual=download_installer(URL,tmp_path,expected_sha256=digest,verify_release=True)
        assert actual==digest and request.call_count==2
        assert request.call_args.kwargs['timeout']==45


def test_interrupted_proxy_transfer_restarts_on_direct_and_verifies(tmp_path):
    import hashlib
    payload=b'MZdirect-installation'
    broken=response(b'MZ',len(payload))
    broken.__enter__.return_value.read.side_effect=[b'MZ',ConnectionResetError()]
    events=[]
    with patch('finance.updates._request',side_effect=[broken,response(payload)]) as request:
        path,digest=download_installer(URL,tmp_path,expected_sha256=hashlib.sha256(payload).hexdigest(),diagnostic=events.append)
    from pathlib import Path
    assert Path(path).read_bytes()==payload and not list(tmp_path.glob('*.part'))
    assert [c.kwargs['route'] for c in request.call_args_list]==['system','direct']
    assert events[-1]['result']=='verified' and events[-1]['route']=='direct'


def test_cancel_during_transfer_never_opens_second_route(tmp_path):
    flag=[False]
    def progress(*_):flag[0]=True
    with patch('finance.updates._request',return_value=response(b'MZtest')) as request:
        with pytest.raises(InterruptedError):
            download_installer(URL,tmp_path,progress=progress,cancelled=lambda:flag[0])
    assert request.call_count<=1 and not list(tmp_path.glob('*.part'))
