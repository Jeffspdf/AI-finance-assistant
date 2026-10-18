import json
from unittest.mock import patch
from urllib.error import HTTPError
from unittest.mock import MagicMock
import pytest
from finance.updates import parse_release, version_tuple, check_latest, REPO


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
    with patch('finance.updates.urlopen') as opener:
        opener.return_value.__enter__.return_value.read.return_value=json.dumps(release()).encode()
        assert check_latest()['version']=='0.3.0'
        request=opener.call_args.args[0]
        assert request.data is None
        assert 'Authorization' not in request.headers
    with patch('finance.updates.urlopen', side_effect=TimeoutError):
        with pytest.raises(TimeoutError): check_latest()


def test_rate_limit_fallback_and_untrusted_redirect():
    response=MagicMock()
    response.__enter__.return_value.geturl.return_value=f'https://github.com/{REPO}/releases/tag/v0.4.0'
    with patch('finance.updates.urlopen',side_effect=[HTTPError('',403,'rate limit',{},None),response]):
        result=check_latest()
        assert result['version']=='0.4.0' and result['newer']
        assert result['installer_url'] is None
    response.__enter__.return_value.geturl.return_value='https://evil.example/releases/tag/v9.0.0'
    with patch('finance.updates.urlopen',side_effect=[HTTPError('',429,'rate limit',{},None),response]):
        with pytest.raises(ValueError): check_latest()
