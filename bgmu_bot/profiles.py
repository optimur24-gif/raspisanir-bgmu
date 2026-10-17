"""Private user preferences. Timetable payloads are never written here."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3


@dataclass(frozen=True)
class SavedGroup:
    path: tuple[str, ...]
    labels: tuple[str, ...]


class ProfileStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existed = self.path.exists()
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS profiles (
                    user_id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL,
                    labels TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
        if not existed:
            self.path.chmod(0o600)

    @classmethod
    def from_environment(cls):
        return cls(Path(os.environ.get("BGMU_DATA_DIR", ".data")) / "preferences.sqlite3")

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, user_id):
        with self.connection() as db:
            row = db.execute("SELECT path, labels FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
        if row is None:
            return None
        return SavedGroup(tuple(json.loads(row[0])), tuple(json.loads(row[1])))

    def save(self, user_id, path, labels):
        if len(path) not in (6, 7) or path[0] != "group" or len(labels) != len(path):
            raise ValueError("Only a complete personal group selection can be saved")
        with self.connection() as db:
            db.execute("""
                INSERT INTO profiles(user_id, path, labels, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET path=excluded.path, labels=excluded.labels, updated_at=excluded.updated_at
            """, (user_id, json.dumps(path, ensure_ascii=False), json.dumps(labels, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def delete(self, user_id):
        with self.connection() as db:
            db.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))

    def paths(self):
        with self.connection() as db:
            rows = db.execute("SELECT path FROM profiles ORDER BY updated_at DESC").fetchall()
        return tuple(tuple(json.loads(row[0])) for row in rows)

    def set_metadata(self, key, value):
        with self.connection() as db:
            db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def metadata(self, key):
        with self.connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
