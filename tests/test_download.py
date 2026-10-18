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
    with patch('finance.updates.urlopen',return_value=response(b'MZtest')):
        path,sha=download_installer(URL,tmp_path)
        assert len(sha)==64
        from pathlib import Path
        assert Path(path).read_bytes()==b'MZtest'
    for payload,size in [(b'<html>',6),(b'MZbroken',100)]:
        with patch('finance.updates.urlopen',return_value=response(payload,size)):
            with pytest.raises(ValueError): download_installer(URL,tmp_path/'bad')
        assert not list((tmp_path/'bad').glob('*.part'))

def test_cancel_and_url_rejection(tmp_path):
    with pytest.raises(ValueError): download_installer('https://evil.example/a.exe',tmp_path)
    with patch('finance.updates.urlopen',return_value=response(b'MZtest')):
        with pytest.raises(InterruptedError): download_installer(URL,tmp_path,cancelled=lambda:True)
    assert not list(tmp_path.glob('*.part'))
