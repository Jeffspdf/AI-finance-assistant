"""Opt-in storage for chat text. Calculation snapshots remain in memory."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from PySide6.QtCore import QLockFile


MAX_SESSIONS = 15
MAX_MESSAGES = 40
MAX_TEXT = 2000
MAX_FILE = 8 * 1024 * 1024


class SessionHistory:
    def __init__(self, directory, mode):
        self.path = Path(directory) / f'chat-history-{mode}.json'
        self.previous = None

    def read_bytes(self):
        try:
            with self.path.open('rb') as stream:
                return stream.read(MAX_FILE + 1)
        except FileNotFoundError:
            return None

    def load(self):
        raw = self.previous = self.read_bytes()
        if raw is None:
            return [[]], 0
        try:
            if len(raw) > MAX_FILE:
                raise ValueError
            data = json.loads(raw)
            sessions, active = data['sessions'], data['active']
            if (data['version'] != 1 or not isinstance(sessions, list) or not 1 <= len(sessions) <= MAX_SESSIONS
                    or type(active) is not int or not 0 <= active < len(sessions)):
                raise ValueError
            result = []
            for rows in sessions:
                if not isinstance(rows, list) or len(rows) > MAX_MESSAGES:
                    raise ValueError
                messages = []
                for role, text in rows:
                    if role not in ('assistant', 'user') or not isinstance(text, str) or len(text) > MAX_TEXT:
                        raise ValueError
                    messages.append((role, text if role == 'user' else {'answer': text, 'archived_text': True}))
                result.append(messages)
            return result, active
        except (KeyError, ValueError, TypeError, UnicodeError, RecursionError):
            raise ValueError('本机对话历史无法读取，原文件已保留。可关闭本机保存并确认删除。') from None

    def save(self, sessions, active):
        rows = [[(role, (payload if role == 'user' else payload.get('display_answer', payload['answer']))[:MAX_TEXT])
                 for role, payload in messages] for messages in sessions]
        data = json.dumps(dict(version=1, active=active, sessions=rows), ensure_ascii=False).encode('utf-8')
        self.replace(data)

    def erase(self):
        self.replace(None)

    def replace(self, data):
        lock = QLockFile(str(self.path) + '.lock')
        if not lock.tryLock(0):
            raise ValueError('另一个 Qt 窗口正在更新对话历史。')
        temporary = None
        try:
            if self.read_bytes() != self.previous:
                raise ValueError('对话历史已被另一个窗口修改。请重启后再操作本机历史。')
            if data is None:
                self.path.unlink(missing_ok=True)
            else:
                with NamedTemporaryFile(dir=self.path.parent, prefix='.chat-history-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
            self.previous = data
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            lock.unlock()
