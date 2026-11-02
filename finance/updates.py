"""Public GitHub release lookup. No credentials or financial data are sent."""
import json
import re
import hashlib
import os
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

APP_VERSION = '0.7.3'
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
    installer = None;checksum=None
    for asset in data.get('assets', []):
        if asset.get('name') == name and asset.get('browser_download_url') == expected and asset.get('state') == 'uploaded':
            installer = expected
            digest=asset.get('digest','')
            if isinstance(digest,str) and re.fullmatch(r'sha256:[0-9a-fA-F]{64}',digest):checksum=digest[7:].lower()
            break
    return dict(version=tag.removeprefix('v'), newer=latest > version_tuple(current),
                page_url=page, installer_url=installer,
                notes=str(data.get('body') or '此版本暂无更新说明。')[:16000],sha256=checksum)


def check_latest():
    request = Request(API_URL, headers={'Accept': 'application/vnd.github+json',
                                      'User-Agent': 'AIFinanceHelper/' + APP_VERSION,
                                      'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with urlopen(request, timeout=20) as response:
            content = response.read(1024 * 1024 + 1)
    except HTTPError as exc:
        if exc.code not in (403,429,500,502,503,504):raise
        return _official_page_release()
    except (URLError,TimeoutError,ConnectionError):
        return _official_page_release()
    if len(content) > 1024 * 1024:
        raise ValueError('更新信息过大，请前往发布页面查看')
    return parse_release(json.loads(content))


def _official_page_release():
    # This public GitHub page is independent of the API quota; downloads still
    # require the official checksum manifest, never an unverified mirror.
    with urlopen(Request(RELEASES_URL,headers={'User-Agent':'AIFinanceHelper/'+APP_VERSION}),timeout=20) as response:
        final_url=response.geturl()
    match=re.fullmatch(r'https://github\.com/'+re.escape(REPO)+r'/releases/tag/(v?\d+\.\d+\.\d+)',final_url)
    if not match:raise ValueError('无法确认官方最新版本')
    tag=match.group(1);name=f'AIFinanceHelper-{tag.removeprefix("v")}-Setup-x64.exe'
    result=parse_release({'tag_name':tag,'assets':[],
                          'body':'已通过官方 GitHub 发布页确认版本。应用内仍可下载安装包，下载前读取官方 SHA256 清单并校验；无需转到浏览器。'})
    result.update(installer_url=f'https://github.com/{REPO}/releases/download/{tag}/{name}',source='official_release_page')
    return result


def _open_download(request,timeout):
    for attempt in range(2):
        try:return urlopen(request,timeout=timeout)
        except HTTPError as exc:
            if attempt or exc.code not in (500,502,503,504):raise
        except (URLError,TimeoutError,ConnectionError):
            if attempt:raise


def update_error_message(exc,action='下载'):
    if isinstance(exc,InterruptedError):return '下载已取消，未保存不完整安装包。'
    if isinstance(exc,ValueError):return f'{action}未完成：{exc}'
    if isinstance(exc,HTTPError):return f'{action}失败：HTTP {exc.code}。GitHub 可能限流或暂时不可用，可重试或使用官方浏览器链接。'
    if isinstance(exc,(TimeoutError,URLError,ConnectionError)):return f'{action}网络连接失败或超时，请检查代理并重试；仍可使用官方浏览器链接。'
    return f'{action}未完成，请检查网络和下载目录是否可写，再重试。'


def published_checksum(url):
    filename=url.rsplit('/',1)[-1]
    sums_url=url.rsplit('/',1)[0]+'/SHA256SUMS.txt'
    with _open_download(Request(sums_url,headers={'User-Agent':'AIFinanceHelper/'+APP_VERSION,'Cache-Control':'no-cache'}),timeout=30) as response:content=response.read(65537)
    if len(content)>65536:raise ValueError('校验清单异常')
    for line in content.decode('utf-8-sig').splitlines():
        parts=line.split()
        if len(parts)==2 and parts[1].lstrip('*')==filename and re.fullmatch(r'[0-9a-fA-F]{64}',parts[0]):return parts[0].lower()
    raise ValueError('官方发布缺少对应文件 SHA256，请查看发布页')

def download_installer(url, directory, progress=lambda received,total: None, cancelled=lambda: False, expected_sha256=None, verify_release=False):
    """Download to a partial file; expose the EXE only after a complete response."""
    match = re.fullmatch(r'https://github\.com/' + re.escape(REPO) + r'/releases/download/(v?(\d+\.\d+\.\d+))/AIFinanceHelper-\2-Setup-x64\.exe', url)
    if not match: raise ValueError('只允许下载本项目的官方安装包')
    if verify_release and not expected_sha256:expected_sha256=published_checksum(url)
    if expected_sha256 is not None and not re.fullmatch(r'[0-9a-fA-F]{64}',expected_sha256):raise ValueError('无效校验值')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    target = directory / url.rsplit('/',1)[-1]
    partial = target.with_suffix('.exe.part')
    received = 0; digest = hashlib.sha256()
    try:
        with _open_download(Request(url, headers={'User-Agent':'AIFinanceHelper/'+APP_VERSION,'Cache-Control':'no-cache'}),timeout=60) as response:
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
        if expected_sha256 and digest.hexdigest()!=expected_sha256.lower():raise ValueError('SHA256 不匹配，安装包已拒绝保存')
        os.replace(partial,target)
        return str(target), digest.hexdigest()
    finally:
        partial.unlink(missing_ok=True)
