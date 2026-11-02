from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
from types import SimpleNamespace
import pytest


def helper(tmp_path,monkeypatch):
    path=Path(__file__).resolve().parents[1]/'packaging/runtime_tls_assets.py'
    spec=spec_from_file_location('runtime_tls_assets_test',path)
    module=module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setattr(module,'ssl',SimpleNamespace(__file__=str(tmp_path/'ssl.py')))
    return module


def test_standard_runtime_needs_no_extra_tls_asset(tmp_path,monkeypatch):
    assert helper(tmp_path,monkeypatch).assets()==[]


def test_custom_runtime_public_tls_resource_is_included(tmp_path,monkeypatch):
    module=helper(tmp_path,monkeypatch)
    resource=tmp_path/'codex-rejected-cas.pem';resource.write_bytes(b'Synthetic public certificate fixture')
    assert module.assets()==[str(resource)]


def test_refuse_private_key_as_bundled_tls_resource(tmp_path,monkeypatch):
    module=helper(tmp_path,monkeypatch)
    (tmp_path/'codex-rejected-cas.pem').write_bytes(b'-----BEGIN PRIVATE KEY-----\nsynthetic-only')
    with pytest.raises(ValueError,match='private key'):module.assets()
