import json
from unittest.mock import patch
from urllib.error import HTTPError
from unittest.mock import MagicMock
import pytest
from finance.updates import parse_release, version_tuple, check_latest, REPO,version_status,can_download,UpdateDiagnostics


def release(version='0.3.0'):
    name=f'AIFinanceHelper-{version}-Setup-x64.exe'
    return {'tag_name': 'v'+version, 'body': '更新说明', 'assets': [
        {'name': name, 'state': 'uploaded', 'browser_download_url': f'https://github.com/{REPO}/releases/download/v{version}/{name}'}]}


def test_numeric_comparison_and_current():
    assert parse_release(release('0.10.0'), '0.9.0')['newer']
    assert not parse_release(release('0.2.3'), '0.2.3')['newer']
    assert not parse_release(release('0.2.2'), '0.2.3')['newer']


@pytest.mark.parametrize('tag', ['v0.3.0-beta', 'bad', '1.2', '../0.3.0'])
def test_bad_version(tag):
    with pytest.raises(ValueError): version_tuple(tag)


def test_reject_draft_and_prerelease():
    for field in ['draft', 'prerelease']:
        with pytest.raises(ValueError): parse_release({**release(), field: True})


def test_untrusted_or_missing_download():
    data=release(); data['assets'][0]['browser_download_url']='https://evil.example/install.exe'
    assert parse_release(data)['installer_url'] is None
    assert parse_release({**release(), 'assets': []})['installer_url'] is None
    assert parse_release(release())['installer_url'].startswith(f'https://github.com/{REPO}/')


def test_network_success_and_failure():
    with patch('finance.updates._request') as opener:
        opener.return_value.__enter__.return_value.read.return_value=json.dumps(release()).encode()
        assert check_latest()['version']=='0.3.0'
        request=opener.call_args.args[0]
        assert request.data is None
        assert 'Authorization' not in request.headers
    with patch('finance.updates._request', side_effect=TimeoutError):
        with pytest.raises(ValueError,match='未能确认'): check_latest()


def test_rate_limit_fallback_and_untrusted_redirect():
    response=MagicMock()
    response.__enter__.return_value.geturl.return_value=f'https://github.com/{REPO}/releases/tag/v9.4.0'
    with patch('finance.updates._request',side_effect=[HTTPError('',403,'rate limit',{},None),HTTPError('',403,'rate limit',{},None),response]):
        result=check_latest()
        assert result['version']=='9.4.0' and result['newer']
        assert result['installer_url']==f'https://github.com/{REPO}/releases/download/v9.4.0/AIFinanceHelper-9.4.0-Setup-x64.exe'
        assert result['source']=='official_release_page'
    response.__enter__.return_value.geturl.return_value='https://evil.example/releases/tag/v9.0.0'
    with patch('finance.updates._request',side_effect=[HTTPError('',429,'rate limit',{},None),HTTPError('',429,'rate limit',{},None),response,response]):
        with pytest.raises(ValueError): check_latest()


def test_api_timeout_still_allows_verified_official_page_download():
    response=MagicMock()
    response.__enter__.return_value.geturl.return_value=f'https://github.com/{REPO}/releases/tag/v9.4.0'
    with patch('finance.updates._request',side_effect=[TimeoutError(),TimeoutError(),response]):
        result=check_latest()
    assert result['installer_url'] and result['source']=='official_release_page'


def test_proxy_limit_switches_to_direct_api_without_changing_settings():
    response=MagicMock();response.__enter__.return_value.read.return_value=json.dumps(release('9.4.0')).encode()
    events=[]
    with patch('finance.updates._request',side_effect=[HTTPError('',403,'sensitive-error',{},None),response]) as request:
        result=check_latest(diagnostic=events.append)
    assert result['version']=='9.4.0' and result['route']=='direct'
    assert [c.kwargs['route'] for c in request.call_args_list]==['system','direct']
    assert 'check=' in request.call_args.args[0].full_url
    assert 'sensitive-error' not in json.dumps(events)


def test_invalid_proxy_json_uses_direct_instead_of_aborting():
    bad=MagicMock();bad.__enter__.return_value.read.return_value=b'<html>Proxy unavailable</html>'
    good=MagicMock();good.__enter__.return_value.read.return_value=json.dumps(release('9.4.0')).encode()
    with patch('finance.updates._request',side_effect=[bad,good]):
        assert check_latest()['route']=='direct'


def test_version_messages_never_call_failure_or_older_release_latest():
    same=parse_release(release('0.7.3'),'0.7.3')
    assert version_status(same,'0.7.3')=='当前已是最新版本：v0.7.3。'
    assert can_download(same,'0.7.3')
    assert not can_download(same,'0.7.4')
    assert '无需降级' in version_status(same,'0.7.4')
    assert '发现新版本' in version_status(same,'0.7.2')


def test_local_diagnostics_are_nonfatal_if_not_writable(tmp_path):
    log=UpdateDiagnostics(tmp_path)
    log({'stage':'release_api','result':'ok','version':'0.7.3'})
    assert json.loads(log.path.read_text())['version']=='0.7.3'
    with patch('pathlib.Path.open',side_effect=PermissionError):log({'result':'failed'})


@pytest.mark.parametrize('suffix',['?check=123','#release','?check=123#release'])
def test_official_page_redirect_can_preserve_query(suffix):
    from finance.updates import _official_page_release
    response=MagicMock()
    response.__enter__.return_value.geturl.return_value=f'https://github.com/{REPO}/releases/tag/v9.4.0'+suffix
    with patch('finance.updates._request',return_value=response):
        assert _official_page_release()['version']=='9.4.0'
