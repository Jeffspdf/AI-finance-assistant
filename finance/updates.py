"""Public GitHub release lookup. No credentials or financial data are sent."""
import json
import re
import hashlib
import os
import time
import socket
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request, build_opener,ProxyHandler
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

APP_VERSION = '0.9.1'
REPO = 'Jeffspdf/AI-finance-assistant'
RELEASES_URL = f'https://github.com/{REPO}/releases/latest'
API_URL = f'https://api.github.com/repos/{REPO}/releases/latest'


def _report(diagnostic,**event):
    if diagnostic:
        diagnostic(dict(time=datetime.now(timezone.utc).isoformat(),client=APP_VERSION,**event))


def _request(request,timeout,route='system'):
    # Build a fresh opener: urllib.urlopen caches proxy settings for the life of
    # a process. A desktop app can outlive changes to the system proxy.
    opener=build_opener() if route=='system' else build_opener(ProxyHandler({}))
    return opener.open(request,timeout=timeout)


class UpdateDiagnostics:
    """Bounded local updater events only; no credentials, ledger data or headers."""
    def __init__(self,directory):self.path=Path(directory)/'update-diagnostics.txt'
    def __call__(self,event):
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            if self.path.exists() and self.path.stat().st_size>65536:
                os.replace(self.path,self.path.with_suffix('.previous.txt'))
            with self.path.open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        except OSError:pass


class _IncompleteDownload(ValueError):
    pass


def _fresh_request(url,headers=None):
    return Request(url+('?' if '?' not in url else '&')+'check='+str(time.time_ns()),
                   headers={'User-Agent':'AIFinanceHelper/'+APP_VERSION,'Cache-Control':'no-cache',
                            'Pragma':'no-cache',**(headers or {})})


def _failure(diagnostic,stage,route,exc):
    reason=getattr(exc,'reason',exc)
    _report(diagnostic,stage=stage,route=route,result='failed',error=type(exc).__name__,
            reason=type(reason).__name__,http=getattr(exc,'code',None),os_error=getattr(reason,'winerror',None))


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


def check_latest(diagnostic=None):
    for route in ('system','direct'):
        try:
            with _request(_fresh_request(API_URL,{'Accept':'application/vnd.github+json',
                           'X-GitHub-Api-Version':'2022-11-28'}),timeout=15,route=route) as response:
                content=response.read(1024*1024+1)
            if len(content)>1024*1024:raise ValueError('更新信息过大')
            result=parse_release(json.loads(content))
            result.update(source='github_api',route=route)
            _report(diagnostic,stage='release_api',route=route,result='ok',version=result['version'])
            return result
        except (HTTPError,URLError,TimeoutError,ConnectionError,ValueError,TypeError) as exc:
            _failure(diagnostic,'release_api',route,exc)
    for route in ('system','direct'):
        try:return _official_page_release(route,diagnostic)
        except (HTTPError,URLError,TimeoutError,ConnectionError,ValueError,TypeError) as exc:
            _failure(diagnostic,'release_page',route,exc)
    raise ValueError('未能确认 GitHub 最新版本，不能判断是否需要更新。请重试或打开官方发布页。')


def _official_page_release(route='system',diagnostic=None):
    # This public GitHub page is independent of the API quota; downloads still
    # require the official checksum manifest, never an unverified mirror.
    with _request(_fresh_request(RELEASES_URL),timeout=15,route=route) as response:
        final_url=response.geturl()
    parsed=urlparse(final_url)
    match=re.fullmatch('/'+re.escape(REPO)+r'/releases/tag/(v?\d+\.\d+\.\d+)',parsed.path)
    if parsed.scheme!='https' or parsed.netloc!='github.com':match=None
    if not match:raise ValueError('无法确认官方最新版本')
    tag=match.group(1);name=f'AIFinanceHelper-{tag.removeprefix("v")}-Setup-x64.exe'
    result=parse_release({'tag_name':tag,'assets':[],
                          'body':'已通过官方 GitHub 发布页确认版本。应用内仍可下载安装包，下载前读取官方 SHA256 清单并校验；无需转到浏览器。'})
    result.update(installer_url=f'https://github.com/{REPO}/releases/download/{tag}/{name}',source='official_release_page',route=route)
    _report(diagnostic,stage='release_page',route=route,result='ok',version=result['version'])
    return result


def _open_download(request,timeout,diagnostic=None,stage='checksum'):
    last=None
    for route in ('system','direct'):
        try:
            response=_request(request,timeout=timeout,route=route)
            _report(diagnostic,stage=stage,route=route,result='connected')
            return response
        except (HTTPError,URLError,TimeoutError,ConnectionError) as exc:
            _failure(diagnostic,stage,route,exc);last=exc
    raise last


def version_status(result,current=APP_VERSION):
    comparison=version_tuple(result['version'])
    if comparison>version_tuple(current):return f'发现新版本 v{result["version"]}（当前 v{current}）。'
    if comparison==version_tuple(current):return f'当前已是最新版本：v{current}。'
    return f'当前版本 v{current} 高于公开发布版 v{result["version"]}，无需降级。'


def can_download(result,current=APP_VERSION):
    return bool(result['installer_url']) and version_tuple(result['version'])>=version_tuple(current)


def update_error_message(exc,action='下载'):
    if isinstance(exc,FileNotFoundError):return f'{action}失败：应用运行资源或下载目录缺失，请完整安装后重试。'
    if isinstance(exc,InterruptedError):return '下载已取消，未保存不完整安装包。'
    if isinstance(exc,ValueError):return f'{action}未完成：{exc}'
    if isinstance(exc,HTTPError):return f'{action}失败：HTTP {exc.code}。GitHub 可能限流或暂时不可用，可重试或使用官方浏览器链接。'
    if isinstance(exc,(TimeoutError,URLError,ConnectionError)):return f'{action}网络连接失败或超时，请检查代理并重试；仍可使用官方浏览器链接。'
    return f'{action}未完成，请检查网络和下载目录是否可写，再重试。'


def published_checksum(url,diagnostic=None):
    filename=url.rsplit('/',1)[-1]
    sums_url=url.rsplit('/',1)[0]+'/SHA256SUMS.txt'
    with _open_download(_fresh_request(sums_url),timeout=30,diagnostic=diagnostic) as response:content=response.read(65537)
    if len(content)>65536:raise ValueError('校验清单异常')
    for line in content.decode('utf-8-sig').splitlines():
        parts=line.split()
        if len(parts)==2 and parts[1].lstrip('*')==filename and re.fullmatch(r'[0-9a-fA-F]{64}',parts[0]):return parts[0].lower()
    raise ValueError('官方发布缺少对应文件 SHA256，请查看发布页')

def download_installer(url, directory, progress=lambda received,total: None, cancelled=lambda: False, expected_sha256=None, verify_release=False,diagnostic=None,phase=lambda message:None):
    """Download to a partial file; expose the EXE only after a complete response."""
    match = re.fullmatch(r'https://github\.com/' + re.escape(REPO) + r'/releases/download/(v?(\d+\.\d+\.\d+))/AIFinanceHelper-\2-Setup-x64\.exe', url)
    if not match: raise ValueError('只允许下载本项目的官方安装包')
    if cancelled():raise InterruptedError('下载已取消')
    if verify_release and not expected_sha256:
        phase('正在读取官方校验清单…')
        expected_sha256=published_checksum(url,diagnostic=diagnostic)
    if expected_sha256 is not None and not re.fullmatch(r'[0-9a-fA-F]{64}',expected_sha256):raise ValueError('无效校验值')
    directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
    target = directory / url.rsplit('/',1)[-1]
    partial = target.with_suffix('.exe.part')
    try:
        for index,route in enumerate(('system','direct')):
            received=0;digest=hashlib.sha256();total=0
            if cancelled():raise InterruptedError('下载已取消')
            phase('正在通过系统网络连接 GitHub…' if index==0 else '系统网络未完成，正在直连 GitHub 重试…')
            progress(0,0)
            try:
                with _request(_fresh_request(url),timeout=45,route=route) as response:
                    total=int(response.headers.get('Content-Length',0))
                    if total>512*1024*1024:raise ValueError('安装包大小超出限制')
                    with partial.open('wb') as stream:
                        while True:
                            if cancelled():raise InterruptedError('下载已取消')
                            chunk=response.read(128*1024)
                            if not chunk:break
                            received+=len(chunk)
                            if received>512*1024*1024:raise ValueError('安装包大小超出限制')
                            stream.write(chunk);digest.update(chunk);progress(received,total)
                if received<2 or (total and received!=total):raise _IncompleteDownload('下载不完整，请重试')
                break
            except InterruptedError:raise
            except (HTTPError,URLError,TimeoutError,ConnectionError,_IncompleteDownload) as exc:
                _failure(diagnostic,'installer',route,exc)
                if index:raise
        if received < 2 or (total and received != total): raise ValueError('下载不完整，请重试')
        with partial.open('rb') as stream:
            if stream.read(2) != b'MZ': raise ValueError('服务器未返回 Windows 安装包')
        if cancelled(): raise InterruptedError('下载已取消')
        if expected_sha256 and digest.hexdigest()!=expected_sha256.lower():raise ValueError('SHA256 不匹配，安装包已拒绝保存')
        os.replace(partial,target)
        _report(diagnostic,stage='installer',route=route,result='verified',bytes=received,sha256=digest.hexdigest())
        return str(target), digest.hexdigest()
    finally:
        partial.unlink(missing_ok=True)


def diagnose_updates(directory,download=False):
    """Same updater used by the GUI, callable from the actual frozen EXE."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    events=[];report={'client':APP_VERSION,'check_ok':False,'download_ok':False,'events':events}
    try:
        result=check_latest(diagnostic=events.append)
        report.update(check_ok=True,version=result['version'],route=result.get('route'),
                      status=version_status(result),source=result.get('source'))
        if download and result['installer_url']:
            path,digest=download_installer(result['installer_url'],directory/'downloads',
                expected_sha256=result.get('sha256'),verify_release=True,diagnostic=events.append)
            report.update(download_ok=True,bytes=Path(path).stat().st_size,sha256=digest)
    except Exception as exc:
        report['error']=update_error_message(exc,'更新诊断')
        import traceback
        report['error_type']=type(exc).__name__
        report['frames']=[{'file':Path(f.filename).name,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(exc.__traceback__)]
    (directory/'update-check-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
