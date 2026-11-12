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

from finance.updates import REPO, _fresh_request, _open_download


RELEASES = f'https://github.com/{REPO}/releases'
FEED = f'https://api.github.com/repos/{REPO}/releases?per_page=30'
VERSION = '0.10.0-preview.5'
VERSION_PATTERN = r'(\d+)\.(\d+)\.(\d+)(?:-preview\.(\d+))?'
MAX_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple
    name: str
    url: str
    size: int
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


def check(*, installed=False):
    limit = 4 * 1024 * 1024
    with _open_download(_fresh_request(FEED, {'Accept': 'application/vnd.github+json'}), 8, stage='qt-release') as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('GitHub 更新列表超过预期大小。')
    return select_release(json.loads(payload), installed=installed)


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
        digest = hashlib.file_digest(stream, 'sha256').hexdigest() if size == release.size else ''
    if digest != release.digest:
        raise ValueError('安装包已变化，请重新下载。')
    command = [str(target), f'/DIR={directory}']
    if mode == 'standalone':
        command.append('/NOREGISTRATION=1')
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
                    if received > release.size:
                        raise ValueError('更新文件超过预期大小。')
                    digest.update(block)
                    output.write(block)
            if stop.is_set():
                raise InterruptedError('下载已停止。')
            if received != release.size or digest.hexdigest() != release.digest:
                raise ValueError('Qt 更新文件未通过大小或 SHA256 校验。')
        if stop.is_set():
            raise InterruptedError('下载已停止。')
        partial.replace(target)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return target
