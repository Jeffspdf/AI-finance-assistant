"""Public GitHub release lookup. No credentials or financial data are sent."""
import json
import re
import hashlib
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

APP_VERSION = '0.4.2'
REPO = 'Jeffspdf/AI-finance-assistant'
RELEASES_URL = f'https://github.com/{REPO}/releases/latest'
API_URL = f'https://api.github.com/repos/{REPO}/releases/latest'


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', value)
    if not match:
        raise ValueError('无法识别正式版本号')
    return tuple(int(part) for part in match.groups())


def parse_release(data, current=APP_VERSION):
    if not isinstance(data, dict) or data.get('draft') or data.get('prerelease'):
        raise ValueError('没有可用的正式版本')
    tag = data.get('tag_name', '')
    latest = version_tuple(tag)
    page = f'https://github.com/{REPO}/releases/tag/{tag}'
    name = f'AIFinanceHelper-{tag.removeprefix("v")}-Setup-x64.exe'
    expected = f'https://github.com/{REPO}/releases/download/{tag}/{name}'
    installer = None
    for asset in data.get('assets', []):
        if asset.get('name') == name and asset.get('browser_download_url') == expected and asset.get('state') == 'uploaded':
            installer = expected
            break
    return dict(version=tag.removeprefix('v'), newer=latest > version_tuple(current),
                page_url=page, installer_url=installer,
                notes=str(data.get('body') or '此版本暂无更新说明。')[:16000])


def check_latest():
    request = Request(API_URL, headers={'Accept': 'application/vnd.github+json',
                                      'User-Agent': 'AIFinanceHelper/' + APP_VERSION,
                                      'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urlopen(request, timeout=8) as response:
            content = response.read(1024 * 1024 + 1)
    except HTTPError as exc:
        if exc.code not in (403, 429): raise
        # Shared networks can exhaust GitHub's unauthenticated API quota.
        # The public latest-release redirect still identifies a formal release.
        with urlopen(Request(RELEASES_URL, headers={'User-Agent': 'AIFinanceHelper/'+APP_VERSION}), timeout=8) as response:
            final_url = response.geturl()
        match = re.fullmatch(r'https://github\.com/' + re.escape(REPO) + r'/releases/tag/(v?\d+\.\d+\.\d+)', final_url)
        if not match: raise ValueError('无法确认官方最新版本')
        tag = match.group(1)
        name = f'AIFinanceHelper-{tag.removeprefix("v")}-Setup-x64.exe'
        result = parse_release({'tag_name': tag, 'assets': [],
                              'body': 'GitHub API 当前限流，已通过官方发布页确认版本。可尝试官方安装包下载链接，若尚未上传请打开发布页面。'})
        result['installer_url'] = f'https://github.com/{REPO}/releases/download/{tag}/{name}'
        return result
    if len(content) > 1024 * 1024:
        raise ValueError('更新信息过大，请前往发布页面查看')
    return parse_release(json.loads(content))


def download_installer(url, directory, progress=lambda received,total: None, cancelled=lambda: False):
    """Download to a partial file; expose the EXE only after a complete response."""
    match = re.fullmatch(r'https://github\.com/' + re.escape(REPO) + r'/releases/download/(v?(\d+\.\d+\.\d+))/AIFinanceHelper-\2-Setup-x64\.exe', url)
    if not match: raise ValueError('只允许下载本项目的官方安装包')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    target = directory / url.rsplit('/',1)[-1]
    partial = target.with_suffix('.exe.part')
    received = 0; digest = hashlib.sha256()
    try:
        with urlopen(Request(url, headers={'User-Agent':'AIFinanceHelper/'+APP_VERSION}),timeout=20) as response:
            total = int(response.headers.get('Content-Length',0))
            if total > 512*1024*1024: raise ValueError('安装包大小超出限制')
            with partial.open('wb') as stream:
                while True:
                    if cancelled(): raise InterruptedError('下载已取消')
                    chunk=response.read(128*1024)
                    if not chunk: break
                    received += len(chunk)
                    if received > 512*1024*1024: raise ValueError('安装包大小超出限制')
                    stream.write(chunk); digest.update(chunk); progress(received,total)
        if received < 2 or (total and received != total): raise ValueError('下载不完整，请重试')
        with partial.open('rb') as stream:
            if stream.read(2) != b'MZ': raise ValueError('服务器未返回 Windows 安装包')
        if cancelled(): raise InterruptedError('下载已取消')
        os.replace(partial,target)
        return str(target), digest.hexdigest()
    finally:
        partial.unlink(missing_ok=True)
