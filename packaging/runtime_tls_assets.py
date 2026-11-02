"""Preserve certificate resources required by the build interpreter's SSL module."""
from pathlib import Path
import json
import ssl


def assets():
    # Some bundled Python runtimes load this adjacent public-certificate resource
    # when constructing every SSLContext. Official CPython has no such resource.
    resource=Path(ssl.__file__).with_name('codex-rejected-cas.pem')
    if not resource.is_file():return []
    if b'PRIVATE KEY' in resource.read_bytes():
        raise ValueError('Refusing to package a private key as TLS data')
    return [str(resource)]


if __name__=='__main__':print(json.dumps(assets()))
