from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
from types import SimpleNamespace
import pytest
import ssl
import re


def helper(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'packaging/runtime_tls_assets.py'
    spec=spec_from_file_location('runtime_tls_assets_test',path)
    module=module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ssl',SimpleNamespace(__file__=str(tmp_path/'ssl.py'),SSLContext=ssl.SSLContext,PROTOCOL_TLS_CLIENT=ssl.PROTOCOL_TLS_CLIENT))
    return module


def test_standard_runtime_needs_no_extra_tls_asset(tmp_path,monkeypatch):
    assert helper(tmp_path,monkeypatch).assets()==[]


@pytest.mark.parametrize('trusted',[False,True])
def test_custom_runtime_public_tls_resource_is_included(tmp_path,monkeypatch,trusted):
    module=helper(tmp_path,monkeypatch)
    if hasattr(ssl,'enum_certificates'):
        der=next(cert for cert,encoding,trust in ssl.enum_certificates('ROOT') if encoding=='x509_asn')
        certificate=ssl.DER_cert_to_PEM_cert(der)
    else:
        content=Path(ssl.get_default_verify_paths().cafile).read_text()
        certificate=re.search(r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----',content,re.S).group(0)
    if trusted:certificate=certificate.replace('BEGIN CERTIFICATE','BEGIN TRUSTED CERTIFICATE').replace('END CERTIFICATE','END TRUSTED CERTIFICATE')
    resource=tmp_path/'codex-rejected-cas.pem';resource.write_text(certificate,encoding='ascii')
    original=resource.read_bytes()
    assert module.assets()==[str(resource)]
    assert resource.read_bytes()==original


def test_refuse_private_key_as_bundled_tls_resource(tmp_path,monkeypatch):
    module=helper(tmp_path,monkeypatch)
    (tmp_path/'codex-rejected-cas.pem').write_bytes(b'-----BEGIN PRIVATE KEY-----\nsynthetic-only')
    with pytest.raises(ValueError,match='private key'):module.assets()


@pytest.mark.parametrize('content',[b'',b'not a certificate',b'-----BEGIN CERTIFICATE-----\nbroken\n-----END CERTIFICATE-----'])
def test_refuse_empty_or_malformed_tls_resource(tmp_path,monkeypatch,content):
    module=helper(tmp_path,monkeypatch)
    (tmp_path/'codex-rejected-cas.pem').write_bytes(content)
    with pytest.raises((ValueError,ssl.SSLError)):module.assets()
