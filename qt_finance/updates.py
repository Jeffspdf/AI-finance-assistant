"""An independent release channel for Qt's portable Windows builds."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from finance.updates import REPO, _fresh_request, _open_download


RELEASES = f'https://github.com/{REPO}/releases'
FEED = f'https://api.github.com/repos/{REPO}/releases?per_page=30'
CURRENT = (0, 10, 0)
MAX_BYTES = 512 * 1024 * 1024


@dataclass(frozen=True)
class Release:
    tag: str
    version: tuple
    name: str
    url: str
    size: int
    digest: str


def select_release(records, current=CURRENT):
    if not isinstance(records, list):
        raise ValueError('GitHub 更新列表格式不兼容。')
    candidates = []
    for record in records:
        tag = record.get('tag_name', '')
        match = re.fullmatch(r'qt-v(\d+\.\d+\.\d+)', tag)
        if not match or record.get('draft') or record.get('prerelease'):
            continue
        version = tuple(map(int, match[1].split('.')))
        if version <= current:
            continue
        name = f'AIFinanceHelperQt-{match[1]}-Windows-x64.zip'
        url = f'{RELEASES}/download/{tag}/{name}'
        for asset in record.get('assets', ()):
            digest, size = asset.get('digest') or '', asset.get('size')
            if (asset.get('name') == name and asset.get('state') == 'uploaded'
                    and asset.get('browser_download_url') == url and re.fullmatch(r'sha256:[0-9a-f]{64}', digest)
                    and type(size) is int and 0 < size <= MAX_BYTES):
                candidates.append(Release(tag, version, name, url, size, digest[7:]))
                break
    return max(candidates, default=None, key=lambda release: release.version)


def check():
    limit = 4 * 1024 * 1024
    with _open_download(_fresh_request(FEED, {'Accept': 'application/vnd.github+json'}), 8, stage='qt-release') as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError('GitHub 更新列表超过预期大小。')
    return select_release(json.loads(payload))


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
                        raise ValueError('压缩包超过预期大小。')
                    digest.update(block)
                    output.write(block)
            if stop.is_set():
                raise InterruptedError('下载已停止。')
            if received != release.size or digest.hexdigest() != release.digest:
                raise ValueError('Qt 压缩包未通过大小或 SHA256 校验。')
        if stop.is_set():
            raise InterruptedError('下载已停止。')
        partial.replace(target)
    finally:
        if partial is not None:
            partial.unlink(missing_ok=True)
    return target
