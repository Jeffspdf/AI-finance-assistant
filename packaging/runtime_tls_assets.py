"""Preserve certificate resources required by the build interpreter's SSL module."""
from pathlib import Path
import json
import ssl
import re


def assets():
    # Some bundled Python runtimes load this adjacent public-certificate resource
    # when constructing every SSLContext. Official CPython has no such resource.
    resource=Path(ssl.__file__).with_name('codex-rejected-cas.pem')
    if not resource.is_file():return []
    content=resource.read_bytes()
    if b'PRIVATE KEY' in content:
        raise ValueError('Refusing to package a private key as TLS data')
    if not content.strip() or not re.search(rb'-----BEGIN (?:TRUSTED )?CERTIFICATE-----',content):
        raise ValueError('TLS resource must contain at least one PEM certificate')
    # OpenSSL's TRUSTED CERTIFICATE form can include explicit rejection flags.
    # Validate the exact file with the same loader; never strip/convert that metadata.
    ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT).load_verify_locations(cafile=str(resource))
    return [str(resource)]


if __name__=='__main__':print(json.dumps(assets()))
