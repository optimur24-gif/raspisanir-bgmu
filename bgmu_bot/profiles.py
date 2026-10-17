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
                CREATE TABLE IF NOT EXISTS chats (
                    chat_id INTEGER PRIMARY KEY, path TEXT, labels TEXT,
                    active INTEGER NOT NULL DEFAULT 1, notifications INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS chat_deliveries (
                    chat_id INTEGER NOT NULL, kind TEXT NOT NULL, target TEXT NOT NULL,
                    parts INTEGER NOT NULL DEFAULT 0, completed INTEGER NOT NULL DEFAULT 0, fingerprint TEXT,
                    PRIMARY KEY(chat_id, kind, target)
                );
            """)
            if 'fingerprint' not in {row[1] for row in db.execute('PRAGMA table_info(chat_deliveries)')}:
                db.execute('ALTER TABLE chat_deliveries ADD COLUMN fingerprint TEXT')
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
            rows += db.execute("SELECT path FROM chats WHERE active=1 AND path IS NOT NULL").fetchall()
        return tuple(tuple(json.loads(row[0])) for row in rows)

    def register_chat(self, chat_id):
        with self.connection() as db:
            db.execute("INSERT INTO chats(chat_id) VALUES (?) ON CONFLICT(chat_id) DO UPDATE SET active=1", (chat_id,))

    def chat_group(self, chat_id):
        with self.connection() as db:
            row = db.execute("SELECT path, labels FROM chats WHERE chat_id=? AND path IS NOT NULL", (chat_id,)).fetchone()
        return SavedGroup(tuple(json.loads(row[0])), tuple(json.loads(row[1]))) if row else None

    def save_chat(self, chat_id, path, labels):
        if len(path) not in (6, 7) or path[0] != "group" or len(path) != len(labels):
            raise ValueError("Only a complete group can be assigned to a chat")
        with self.connection() as db:
            db.execute("""INSERT INTO chats(chat_id,path,labels) VALUES (?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET path=excluded.path,labels=excluded.labels,active=1""",
                (chat_id, json.dumps(path, ensure_ascii=False), json.dumps(labels, ensure_ascii=False)))

    def chat_notifications(self, chat_id, enabled):
        with self.connection() as db:
            db.execute("UPDATE chats SET notifications=? WHERE chat_id=?", (int(enabled), chat_id))

    def deactivate_chat(self, chat_id):
        with self.connection() as db:
            db.execute("UPDATE chats SET active=0 WHERE chat_id=?", (chat_id,))

    def subscribed_chats(self):
        with self.connection() as db:
            return tuple(row[0] for row in db.execute("SELECT chat_id FROM chats WHERE active=1 AND notifications=1 AND path IS NOT NULL"))

    def delivery(self, chat_id, kind, target):
        with self.connection() as db:
            row = db.execute("SELECT parts, completed FROM chat_deliveries WHERE chat_id=? AND kind=? AND target=?", (chat_id, kind, target)).fetchone()
        return row or (0, 0)

    def delivery_fingerprint(self, chat_id, kind, target):
        with self.connection() as db:
            row = db.execute('SELECT fingerprint FROM chat_deliveries WHERE chat_id=? AND kind=? AND target=?', (chat_id, kind, target)).fetchone()
        return row[0] if row else None

    def record_delivery(self, chat_id, kind, target, parts, completed=False, fingerprint=None):
        with self.connection() as db:
            db.execute("""INSERT INTO chat_deliveries VALUES (?,?,?,?,?,?)
                ON CONFLICT(chat_id,kind,target) DO UPDATE SET parts=excluded.parts,completed=excluded.completed,fingerprint=excluded.fingerprint""",
                (chat_id, kind, target, parts, int(completed), fingerprint))
            # Notification metadata only; no timetable bodies are stored.
            db.execute("DELETE FROM chat_deliveries WHERE target < date(?, '-60 days')", (target,))

    def migrate_chat(self, old_id, new_id):
        with self.connection() as db:
            db.execute("""INSERT INTO chats(chat_id,path,labels,active,notifications)
                SELECT ?,path,labels,active,notifications FROM chats WHERE chat_id=?
                ON CONFLICT(chat_id) DO NOTHING""", (new_id, old_id))
            db.execute("UPDATE chats SET active=0 WHERE chat_id=?", (old_id,))
            db.execute("""INSERT OR IGNORE INTO chat_deliveries SELECT ?,kind,target,parts,completed,fingerprint
                FROM chat_deliveries WHERE chat_id=?""", (new_id, old_id))

    def set_metadata(self, key, value):
        with self.connection() as db:
            db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value, ensure_ascii=False)))

    def metadata(self, key):
        with self.connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None
