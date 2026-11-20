"""Independent Qt release channel for portable and installed clients."""

from configparser import ConfigParser, Error as ConfigError
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError

from finance.updates import REPO, _fresh_request, _open_download


RELEASES = f'https://github.com/{REPO}/releases'
FEED = f'https://api.github.com/repos/{REPO}/releases?per_page=30'
VERSION = '0.10.0-preview.36'
VERSION_PATTERN = r'(\d+)\.(\d+)\.(\d+)(?:-preview\.(\d+))?'
MAX_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple
    name: str
    url: str
    size: int | None
    digest: str


def version_key(version):
    match = re.fullmatch(VERSION_PATTERN, version)
    if not match:
        raise ValueError('无效的 Qt 版本号。')
    preview = match[4]
    return (*map(int, match.groups()[:3]), preview is None, int(preview or 0))


def select_release(records, current=VERSION, *, installed=False):
    if not isinstance(records, list):
        raise ValueError('GitHub 更新列表格式不兼容。')
    candidates = []
    for record in records:
        tag = record.get('tag_name', '')
        match = re.fullmatch(r'qt-v(' + VERSION_PATTERN + ')', tag)
        if not match or record.get('draft') or bool(record.get('prerelease')) != bool(match[5]):
            continue
        if match[5] and '-preview.' not in current:
            continue
        version = version_key(match[1])
        if version <= version_key(current):
            continue
        suffix = 'Setup-x64.exe' if installed else 'Windows-x64.zip'
        name = f'AIFinanceHelperQt-{match[1]}-{suffix}'
        url = f'{RELEASES}/download/{tag}/{name}'
        for asset in record.get('assets', ()):
            digest, size = asset.get('digest') or '', asset.get('size')
            if (asset.get('name') == name and asset.get('state') == 'uploaded'
                    and asset.get('browser_download_url') == url and re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
                    and type(size) is int and 0 < size <= MAX_BYTES):
                candidates.append(Release(tag, version, name, url, size, digest[7:]))
                break
    return max(candidates, default=None, key=lambda release: release.version)


def read_release_text(url, *, accept, limit=4 * 1024 * 1024):
    with _open_download(_fresh_request(url, {'Accept': accept}), 8, stage='qt-release') as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('GitHub 更新资料超过预期大小。')
    return payload.decode('utf-8-sig')


def check_release_page(current, installed):
    page = read_release_text(RELEASES, accept='text/html')
    path = f'/{REPO}/releases'
    tags = set(re.findall(rf'href="(?:https://github\.com)?{re.escape(path)}/tag/(qt-v{VERSION_PATTERN})"', page))
    if not tags:
        raise ValueError('无法从官方发布页确认 Qt 版本，请打开 GitHub 发布页查看。')
    releases = {}
    for tag, major, minor, patch, preview in tags:
        version = tag.removeprefix('qt-v')
        if (not preview or '-preview.' in current) and version_key(version) > version_key(current):
            releases[version] = tag
    if not releases:
        return None
    version = max(releases, key=version_key)
    tag = releases[version]
    suffix = 'Setup-x64.exe' if installed else 'Windows-x64.zip'
    name = f'AIFinanceHelperQt-{version}-{suffix}'
    assets = read_release_text(f'{RELEASES}/expanded_assets/{tag}', accept='text/html')
    prefix = f'{path}/download/{tag}/'
    links = set(re.findall(rf'href="(?:https://github\.com)?{re.escape(prefix)}([^"/]+)"', assets))
    checksum_name = f'SHA256SUMS-qt-{version}.txt'
    if not {name, checksum_name} <= links:
        raise ValueError(f'发现 {tag}，但官方发布页缺少所需安装文件或校验清单。')
    url = f'{RELEASES}/download/{tag}'
    checksums = read_release_text(f'{url}/{checksum_name}', accept='text/plain', limit=65536)
    entries = re.findall(rf'^([0-9a-fA-F]{{64}})[ \t]+\*?{re.escape(name)}[ \t\r]*$', checksums, re.MULTILINE)
    if len(entries) != 1:
        raise ValueError(f'{tag} 的官方校验清单缺少唯一的文件摘要。')
    return Release(tag, version_key(version), name, f'{url}/{name}', None, entries[0].lower())


def check(*, installed=False, current=VERSION):
    try:
        payload = read_release_text(FEED, accept='application/vnd.github+json')
        return select_release(json.loads(payload), current=current, installed=installed)
    except HTTPError as error:
        if error.code not in (403, 429, 500, 502, 503, 504):
            raise
    except (URLError, TimeoutError, ConnectionError, ValueError):
        pass
    return check_release_page(current, installed)


def installation(directory):
    config = ConfigParser()
    try:
        config.read(Path(directory) / 'qt-install.ini', encoding='utf-8-sig')
        mode = config.get('installation', 'registration', fallback='')
    except (ConfigError, OSError, UnicodeError):
        return ''
    return mode if mode in ('standalone', 'windows') else ''


def installation_directory():
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else None


def launch_installer(release, target, directory, mode):
    target = Path(target).resolve()
    if not release.name.endswith('-Setup-x64.exe') or target.name != release.name:
        raise ValueError('请选择已下载的原名 Qt 安装包。')
    with target.open('rb') as stream:
        size = target.stat().st_size
        matches_size = 0 < size <= MAX_BYTES and (release.size is None or size == release.size)
        digest = hashlib.file_digest(stream, 'sha256').hexdigest() if matches_size else ''
    if digest != release.digest:
        raise ValueError('安装包已变化，请重新下载。')
    command = [str(target), f'/DIR={directory}']
    if mode == 'standalone':
        command.append('/NOREGISTRATION=1')
    elif mode == 'windows':
        command.append('/MERGETASKS=!noregistration')
    subprocess.Popen(command)


def download(release, target, stop):
    target = Path(target)
    partial = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix='.qt-update-', suffix='.part', delete=False) as output:
            partial = Path(output.name)
            received = 0
            digest = hashlib.sha256()
            with _open_download(_fresh_request(release.url), 15, stage='qt-download') as stream:
                while not stop.is_set():
                    block = stream.read(256 * 1024)
                    if not block:
                        break
                    received += len(block)
                    if received > (release.size if release.size is not None else MAX_BYTES):
                        raise ValueError('更新文件超过预期大小。')
                    digest.update(block)
                    output.write(block)
            if stop.is_set():
                raise InterruptedError('下载已停止。')
            if not received or (release.size is not None and received != release.size) or digest.hexdigest() != release.digest:
                raise ValueError('Qt 更新文件未通过大小或 SHA256 校验。')
        if stop.is_set():
            raise InterruptedError('下载已停止。')
        partial.replace(target)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return target
