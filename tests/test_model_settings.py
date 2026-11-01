import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest
from finance.model_settings import CredentialError, ModelSettings, WindowsCipher

URL='https://example.com/v1'
SECRET='synthetic-key-for-tests-only'


class TestCipher:
    """Deterministic fake for persistence paths, not a security assertion."""
    __test__=False
    def protect(self,value):return bytes(b ^ 173 for b in value)
    def unprotect(self,value):return self.protect(value)


def repository(tmp_path):return ModelSettings(tmp_path,cipher=TestCipher())


def test_old_configuration_and_corrupt_file_do_not_crash(tmp_path):
    store=repository(tmp_path)
    assert not store.load().enabled
    store.path.write_text(json.dumps({'base_url':URL,'model':'test'}))
    loaded=store.load()
    assert loaded.base_url==URL and not loaded.enabled and loaded.remember_key
    assert '重新填写' in loaded.notice
    store.path.write_text('broken configuration')
    assert store.load().notice and store.path.read_text()=='broken configuration'


def test_save_restart_and_upgrade_without_network_or_plaintext(tmp_path):
    store=repository(tmp_path)
    with patch('finance.llm.urlopen',side_effect=AssertionError('Unexpected request')):
        store.save(URL,'test',SECRET,True,True)
        # Fresh repository instance is independent of app version/install directory.
        loaded=repository(tmp_path).load()
    assert loaded.enabled and loaded.api_key==SECRET and loaded.model=='test'
    assert SECRET not in repr(loaded)
    assert SECRET not in store.path.read_text()
    assert 'api_key' not in json.loads(store.path.read_text())
    assert not list(tmp_path.glob('.model-settings-*.tmp'))


def test_session_only_does_not_restore_or_leave_old_saved_secret(tmp_path):
    store=repository(tmp_path);store.save(URL,'test',SECRET)
    active=store.save(URL,'test',SECRET,True,False)
    assert active.enabled and active.api_key==SECRET
    loaded=repository(tmp_path).load()
    assert not loaded.enabled and not loaded.api_key and not loaded.remember_key
    assert 'encrypted_api_key' not in json.loads(store.path.read_text())


def test_disable_persists_but_keeps_remembered_key(tmp_path):
    store=repository(tmp_path);store.save(URL,'test',SECRET,False,True)
    loaded=repository(tmp_path).load()
    assert not loaded.enabled and loaded.api_key==SECRET


def test_keyless_local_endpoint_restores(tmp_path):
    store=repository(tmp_path);store.save('http://localhost:11434/v1','local','')
    loaded=repository(tmp_path).load()
    assert loaded.enabled and not loaded.api_key


def test_session_only_keyless_endpoint_does_not_auto_enable(tmp_path):
    store=repository(tmp_path)
    active=store.save('http://localhost:11434/v1','local','',True,False)
    assert active.enabled and not store.load().enabled


def test_clear_removes_key_and_disables_without_losing_endpoint(tmp_path):
    store=repository(tmp_path);store.save(URL,'test',SECRET)
    store.clear_key();loaded=repository(tmp_path).load()
    assert loaded.base_url==URL and loaded.model=='test'
    assert not loaded.enabled and not loaded.api_key
    assert 'encrypted_api_key' not in json.loads(store.path.read_text())


def test_changed_endpoint_in_file_never_restores_secret_to_other_host(tmp_path):
    store=repository(tmp_path);store.save(URL,'test',SECRET)
    data=json.loads(store.path.read_text());data['base_url']='https://other.example/v1'
    store.path.write_text(json.dumps(data))
    loaded=store.load()
    assert not loaded.enabled and not loaded.api_key and loaded.notice
    assert 'encrypted_api_key' in json.loads(store.path.read_text())


def test_encryption_failure_and_atomic_write_failure_preserve_previous_config(tmp_path):
    store=repository(tmp_path);store.save(URL,'test',SECRET)
    previous=store.path.read_bytes()
    with patch.object(store.cipher,'protect',side_effect=CredentialError('加密失败')):
        with pytest.raises(CredentialError):store.save(URL,'new-model','new-test-key')
    assert store.path.read_bytes()==previous
    with patch('finance.model_settings.os.replace',side_effect=OSError('Write failed')):
        with pytest.raises(OSError):store.save(URL,'new-model','new-test-key')
    assert store.path.read_bytes()==previous and not list(tmp_path.glob('.model-settings-*.tmp'))


def test_corrupt_cipher_disables_without_deleting_file(tmp_path):
    store=repository(tmp_path);store.save(URL,'test',SECRET)
    data=json.loads(store.path.read_text());data['encrypted_api_key']='invalid-base64!'
    store.path.write_text(json.dumps(data));previous=store.path.read_bytes()
    loaded=store.load()
    assert not loaded.enabled and not loaded.api_key and loaded.notice
    assert previous==store.path.read_bytes()


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI integration')
def test_real_windows_dpapi_reopens_from_fresh_process(tmp_path):
    store=ModelSettings(tmp_path);store.save(URL,'synthetic-model',SECRET)
    raw=store.path.read_bytes()
    assert SECRET.encode() not in raw
    data=json.loads(raw)
    encrypted=base64.b64decode(data['encrypted_api_key'])
    assert SECRET.encode() not in encrypted
    script="from finance.model_settings import ModelSettings; import sys; c=ModelSettings(sys.argv[1]).load(); assert c.enabled and c.api_key=='synthetic-key-for-tests-only'; print('DPAPI_CROSS_PROCESS_RESTORE_OK')"
    result=subprocess.run([sys.executable,'-c',script,str(tmp_path)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert 'DPAPI_CROSS_PROCESS_RESTORE_OK' in result.stdout


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI integrity')
def test_real_windows_dpapi_rejects_damaged_blob():
    cipher=WindowsCipher();encrypted=cipher.protect(b'synthetic-only')
    damaged=bytes([encrypted[0]^1])+encrypted[1:]
    with pytest.raises(CredentialError):cipher.unprotect(damaged)
