"""Frozen schema-5 export, produced from synthetic v4 data before the v6 implementation."""
from pathlib import Path
import sqlite3
from contextlib import closing


def create_v5(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.executescript((Path(__file__).parent/'fixtures/frozen_v5.sql').read_text(encoding='utf-8'))
    return path
