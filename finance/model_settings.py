"""User-scoped encrypted model settings. Loading never makes a network request."""
import base64
import ctypes
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile

from .llm import ModelClient


class CredentialError(ValueError):
    pass


class WindowsCipher:
    """DPAPI current-user scope; stable entropy survives app upgrades."""
    ENTROPY = b'AIFinanceHelper.ModelCredentials.v1'

    def _crypt(self, content, decrypt=False):
        if os.name != 'nt':
            raise CredentialError('此系统暂不支持安全记住密钥，请取消“记住密钥”后仅本次使用。')
        from ctypes import wintypes

        class Blob(ctypes.Structure):
            _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]

        content_buffer = ctypes.create_string_buffer(content)
        entropy_buffer = ctypes.create_string_buffer(self.ENTROPY)
        source = Blob(len(content), ctypes.cast(content_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        entropy = Blob(len(self.ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_ubyte)))
        output = Blob()
        crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        protect = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        protect.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p if decrypt else wintypes.LPCWSTR,
                            ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(Blob)]
        protect.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        # UI_FORBIDDEN, and deliberately no LOCAL_MACHINE flag.
        if not protect(ctypes.byref(source), None if decrypt else 'AI理财小助手 API Key',
                       ctypes.byref(entropy), None, None, 1, ctypes.byref(output)):
            raise CredentialError('Windows 无法读取或保存密钥，请重新填写；可取消记住后仅本次使用。')
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)

    def protect(self, content):
        return self._crypt(content)

    def unprotect(self, content):
        return self._crypt(content, decrypt=True)


@dataclass
class Configuration:
    base_url: str = ''
    model: str = ''
    api_key: str = field(default='', repr=False)
    enabled: bool = False
    remember_key: bool = True
    notice: str = ''


class ModelSettings:
    def __init__(self, directory, cipher=None):
        self.path = Path(directory) / 'model-settings.json'
        self.cipher = cipher or WindowsCipher()

    def load(self):
        try:
            if self.path.stat().st_size > 131072:
                raise ValueError('Oversized settings')
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict):
                raise ValueError('Invalid settings')
        except FileNotFoundError:
            return Configuration()
        except (OSError, ValueError):
            return Configuration(notice='模型配置无法读取，请打开模型设置重新保存；原文件未改动。')
        config = Configuration(base_url=str(data.get('base_url') or ''), model=str(data.get('model') or ''),
                               remember_key=data.get('remember_key', True) is True)
        if 'version' not in data and config.base_url:
            config.notice = '旧版本未保存密钥，请重新填写并保存一次；以后可自动恢复。'
        if data.get('version', 1) not in (1, 2):
            config.notice = '模型配置来自较新版本，请升级应用；未自动启用。'
            return config
        cipher_text = data.get('encrypted_api_key')
        if cipher_text:
            try:
                decrypted = self.cipher.unprotect(base64.b64decode(cipher_text, validate=True))
                payload = json.loads(decrypted.decode('utf-8'))
                # Editing the endpoint in JSON must never forward the old secret elsewhere.
                if payload['base_url'] != config.base_url or not isinstance(payload['api_key'], str):
                    raise ValueError('Endpoint mismatch')
                config.api_key = payload['api_key']
            except (CredentialError, ValueError, TypeError, KeyError, UnicodeError):
                config.notice = '已保存的密钥无法解密或接口地址已变化，请重新填写。原配置未删除。'
                return config
        if data.get('requires_key') and not config.api_key:
            config.notice = '上次选择了仅本次使用，请重新填写密钥后启用。'
            return config
        if data.get('enabled') is True:
            try:
                self._validate(config.base_url, config.model, config.api_key)
                config.enabled = True
            except ValueError:
                config.notice = '已保存的模型配置不完整，请打开设置核对后启用。'
        return config

    @staticmethod
    def _validate(base_url, model, api_key):
        if base_url.endswith('/chat/completions'):
            raise ValueError('请填写 Base URL，不要包含 /chat/completions')
        return ModelClient(base_url, model, api_key, enabled=True)

    def _write(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One atomic file avoids mismatching a newly saved endpoint and an old key.
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.model-settings-', suffix='.tmp', delete=False) as stream:
                name = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if name is not None:
                name.unlink(missing_ok=True)

    def save(self, base_url, model, api_key, enabled=True, remember_key=True):
        base_url, model, api_key = base_url.strip().rstrip('/'), model.strip(), api_key.strip()
        if len(api_key) > 16384:
            raise ValueError('密钥过长，请核对输入。')
        if enabled:
            self._validate(base_url, model, api_key)
        elif base_url:
            self._validate(base_url, model or 'disabled', api_key)
        data = dict(version=2, base_url=base_url, model=model, enabled=bool(enabled and remember_key),
                    remember_key=bool(remember_key), requires_key=bool(api_key))
        if remember_key and api_key:
            payload = json.dumps(dict(base_url=base_url, api_key=api_key), ensure_ascii=False).encode('utf-8')
            data['encrypted_api_key'] = base64.b64encode(self.cipher.protect(payload)).decode('ascii')
        self._write(data)
        return Configuration(base_url, model, api_key, bool(enabled), bool(remember_key))

    def clear_key(self):
        config = self.load()
        self._write(dict(version=2, base_url=config.base_url, model=config.model, enabled=False,
                         remember_key=False, requires_key=False))
        return Configuration(config.base_url, config.model, remember_key=False,
                             notice='已清除本机保存的密钥并停用模型。')
