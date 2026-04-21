from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

MAX_TITLE_LENGTH = 40
UNTITLED = "Untitled conversation"


def normalize_user_id(value: str | None) -> str:
    raw = str(value or "guest").strip()
    normalized = re.sub(r"[^0-9a-zA-Z_\-@.]", "_", raw)
    return normalized[:60] or "guest"


def normalize_conversation_id(value: str | None) -> str:
    raw = str(value or "default").strip()
    normalized = re.sub(r"[^0-9a-zA-Z_\-]", "_", raw)
    return normalized[:80] or "default"


def split_memory_key(memory_key: str) -> tuple[str, str]:
    marker = "__conv__"
    if marker in memory_key:
        user, conversation = memory_key.split(marker, 1)
        return normalize_user_id(user), normalize_conversation_id(conversation)
    return normalize_user_id(memory_key), "legacy"


def make_title_from_messages(messages: list[dict[str, str]]) -> str:
    for item in messages:
        if item.get("role") == "user" and item.get("content"):
            content = str(item["content"]).strip()
            return content[:MAX_TITLE_LENGTH] or UNTITLED
    return UNTITLED


class DigitalHumanStorage:
    """SQLite data layer for users, conversations, messages and admin delete events."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _db(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._db() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY,
                    role TEXT NOT NULL DEFAULT 'user',
                    password_hash TEXT,
                    created_at INTEGER NOT NULL,
                    last_login_at INTEGER NOT NULL,
                    deleted_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS conversations (
                    username TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT 'Untitled conversation',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    deleted_at INTEGER,
                    PRIMARY KEY (username, conversation_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS delete_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    username TEXT,
                    conversation_id TEXT,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(username, conversation_id, id);
                CREATE INDEX IF NOT EXISTS idx_delete_events_user
                    ON delete_events(username, event_type, created_at);
                """
            )

    def _set_setting(self, conn: sqlite3.Connection, key: str, value: str) -> None:
        conn.execute(
            """
            INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (key, value),
        )

    def _get_setting(self, conn: sqlite3.Connection, key: str) -> str | None:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def migrate_legacy_json(self, registry_path: Path, memory_path: Path) -> None:
        registry = self._read_json(registry_path, {})
        memory = self._read_json(memory_path, {})
        now = int(time.time())

        with self._db() as conn:
            if self._get_setting(conn, "legacy_json_migrated_at"):
                return

            if isinstance(registry, dict):
                for raw_username, raw_info in registry.items():
                    username = normalize_user_id(str(raw_username))
                    info = raw_info if isinstance(raw_info, dict) else {}
                    role = str(info.get("role", "user"))
                    created_at = self._int_or_now(info.get("created_at"), now)
                    last_login_at = self._int_or_now(info.get("last_login_at"), created_at)
                    self._sync_user_conn(conn, username, role, created_at, last_login_at)

                    conversations = info.get("conversations", {})
                    if isinstance(conversations, dict):
                        for raw_conv_id, raw_conv_info in conversations.items():
                            conv_id = normalize_conversation_id(str(raw_conv_id))
                            conv_info = raw_conv_info if isinstance(raw_conv_info, dict) else {}
                            title = str(conv_info.get("title", UNTITLED))
                            updated_at = self._int_or_now(conv_info.get("updated_at"), now)
                            self._upsert_conversation_conn(conn, username, conv_id, title, created_at, updated_at)

            if isinstance(memory, dict):
                for raw_key, raw_history in memory.items():
                    if not isinstance(raw_history, list):
                        continue
                    username, conv_id = split_memory_key(str(raw_key))
                    safe_history = self._clean_history(raw_history)
                    if not safe_history:
                        continue
                    self._sync_user_conn(conn, username, "user", now, now, preserve_role=True)
                    title = self._find_conversation_title(conn, username, conv_id) or make_title_from_messages(safe_history)
                    self._upsert_conversation_conn(conn, username, conv_id, title, now, now)
                    count = conn.execute(
                        "SELECT COUNT(*) AS count FROM messages WHERE username=? AND conversation_id=?",
                        (username, conv_id),
                    ).fetchone()["count"]
                    if count:
                        continue
                    for offset, item in enumerate(safe_history):
                        conn.execute(
                            """
                            INSERT INTO messages(username, conversation_id, role, content, created_at)
                            VALUES(?, ?, ?, ?, ?)
                            """,
                            (username, conv_id, item["role"], item["content"], now + offset),
                        )
            self._set_setting(conn, "legacy_json_migrated_at", str(now))

    def sync_user(self, username: str, role: str = "user", password_hash: str | None = None) -> str:
        user_id = normalize_user_id(username)
        now = int(time.time())
        with self._db() as conn:
            self._sync_user_conn(conn, user_id, role, now, now, password_hash=password_hash)
        return user_id

    def get_user_role(self, username: str) -> str:
        user_id = normalize_user_id(username)
        with self._db() as conn:
            row = conn.execute(
                "SELECT role FROM users WHERE username=? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()
        return str(row["role"]) if row else "user"

    def register_user(self, username: str, password_hash: str, role: str = "user") -> tuple[bool, str]:
        user_id = normalize_user_id(username)
        safe_hash = str(password_hash or "").strip()
        if not safe_hash:
            return False, "password_hash_empty"
        now = int(time.time())
        with self._db() as conn:
            row = conn.execute(
                "SELECT username, password_hash FROM users WHERE username=? AND deleted_at IS NULL",
                (user_id,),
            ).fetchone()
            if row and row["password_hash"]:
                return False, "user_exists"
            self._sync_user_conn(conn, user_id, role, now, now, password_hash=safe_hash)
        return True, "ok"

    def verify_user(self, username: str, password_hash: str) -> tuple[bool, str]:
        user_id = normalize_user_id(username)
        safe_hash = str(password_hash or "").strip()
        with self._db() as conn:
            row = conn.execute(
                """
                SELECT password_hash FROM users
                WHERE username=? AND deleted_at IS NULL
                """,
                (user_id,),
            ).fetchone()
            if not row or not row["password_hash"]:
                return False, "user_not_found"
            if str(row["password_hash"]) != safe_hash:
                return False, "bad_password"
            self._sync_user_conn(conn, user_id, "user", int(time.time()), int(time.time()), preserve_role=True)
        return True, "ok"

    def upsert_conversation(self, username: str, conversation_id: str, title: str = UNTITLED) -> None:
        user_id = normalize_user_id(username)
        conv_id = normalize_conversation_id(conversation_id)
        now = int(time.time())
        with self._db() as conn:
            self._sync_user_conn(conn, user_id, "user", now, now, preserve_role=True)
            self._upsert_conversation_conn(conn, user_id, conv_id, title, now, now)

    def get_llm_history(self, username: str, conversation_id: str, limit: int) -> list[dict[str, str]]:
        user_id = normalize_user_id(username)
        conv_id = normalize_conversation_id(conversation_id)
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM (
                    SELECT id, role, content FROM messages
                    WHERE username=? AND conversation_id=?
                    ORDER BY id DESC LIMIT ?
                ) ORDER BY id ASC
                """,
                (user_id, conv_id, limit),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def append_turn(self, username: str, conversation_id: str, user_text: str, assistant_text: str) -> None:
        user_id = normalize_user_id(username)
        conv_id = normalize_conversation_id(conversation_id)
        now = int(time.time())
        with self._db() as conn:
            self._sync_user_conn(conn, user_id, "user", now, now, preserve_role=True)
            title = self._find_conversation_title(conn, user_id, conv_id) or user_text[:MAX_TITLE_LENGTH] or UNTITLED
            self._upsert_conversation_conn(conn, user_id, conv_id, title, now, now)
            conn.executemany(
                """
                INSERT INTO messages(username, conversation_id, role, content, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                [
                    (user_id, conv_id, "user", user_text, now),
                    (user_id, conv_id, "assistant", assistant_text, now),
                ],
            )

    def import_client_conversations(self, username: str, conversations: list[dict[str, Any]]) -> int:
        user_id = normalize_user_id(username)
        now = int(time.time())
        imported = 0
        with self._db() as conn:
            self._sync_user_conn(conn, user_id, "user", now, now, preserve_role=True)
            for item in conversations:
                if not isinstance(item, dict):
                    continue
                conv_id = normalize_conversation_id(str(item.get("id") or "default"))
                was_deleted = conn.execute(
                    """
                    SELECT 1 FROM delete_events
                    WHERE event_type='conversation' AND username=? AND conversation_id=?
                    LIMIT 1
                    """,
                    (user_id, conv_id),
                ).fetchone()
                if was_deleted:
                    continue
                title = str(item.get("title") or UNTITLED)[:MAX_TITLE_LENGTH]
                created_at = self._int_or_now(item.get("createdAt") or item.get("created_at"), now)
                updated_at = self._int_or_now(item.get("updatedAt") or item.get("updated_at"), now)
                self._upsert_conversation_conn(conn, user_id, conv_id, title, created_at, updated_at)
                existing_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM messages WHERE username=? AND conversation_id=?",
                    (user_id, conv_id),
                ).fetchone()["count"]
                if existing_count:
                    continue
                messages = item.get("messages", [])
                if not isinstance(messages, list):
                    continue
                clean_messages = []
                for offset, raw_message in enumerate(messages):
                    if not isinstance(raw_message, dict):
                        continue
                    role = self._normalize_role(str(raw_message.get("role", "")))
                    content_text = str(raw_message.get("text") or raw_message.get("content") or "").strip()
                    if role and content_text:
                        clean_messages.append((user_id, conv_id, role, content_text, created_at + offset))
                if clean_messages:
                    conn.executemany(
                        """
                        INSERT INTO messages(username, conversation_id, role, content, created_at)
                        VALUES(?, ?, ?, ?, ?)
                        """,
                        clean_messages,
                    )
                    imported += 1
        return imported

    def list_conversations(self, username: str) -> list[dict[str, Any]]:
        user_id = normalize_user_id(username)
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT c.conversation_id, c.title, c.created_at, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m
                    ON m.username=c.username AND m.conversation_id=c.conversation_id
                WHERE c.username=? AND c.deleted_at IS NULL
                GROUP BY c.conversation_id, c.title, c.created_at, c.updated_at
                ORDER BY c.updated_at DESC, c.created_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [
            {
                "id": row["conversation_id"],
                "title": row["title"] or UNTITLED,
                "createdAt": int(row["created_at"] or 0) * 1000,
                "updatedAt": int(row["updated_at"] or 0) * 1000,
                "messageCount": int(row["message_count"] or 0),
            }
            for row in rows
        ]

    def list_existing_conversation_ids(self, username: str) -> list[str]:
        return [item["id"] for item in self.list_conversations(username)]

    def get_conversation_messages(self, username: str, conversation_id: str) -> list[dict[str, Any]]:
        user_id = normalize_user_id(username)
        conv_id = normalize_conversation_id(conversation_id)
        with self._db() as conn:
            rows = conn.execute(
                """
                SELECT role, content, created_at FROM messages
                WHERE username=? AND conversation_id=?
                ORDER BY id ASC
                """,
                (user_id, conv_id),
            ).fetchall()
        return [
            {
                "role": "bot" if row["role"] == "assistant" else row["role"],
                "text": row["content"],
                "time": int(row["created_at"] or 0) * 1000,
            }
            for row in rows
        ]

    def get_delete_state(self, username: str) -> dict[str, Any]:
        user_id = normalize_user_id(username)
        with self._db() as conn:
            conv_rows = conn.execute(
                """
                SELECT conversation_id FROM delete_events
                WHERE event_type='conversation' AND username=?
                """,
                (user_id,),
            ).fetchall()
            user_row = conn.execute(
                """
                SELECT MAX(created_at) AS ts FROM delete_events
                WHERE event_type='user' AND username=?
                """,
                (user_id,),
            ).fetchone()
            all_row = conn.execute(
                "SELECT MAX(created_at) AS ts FROM delete_events WHERE event_type='all'"
            ).fetchone()
        return {
            "deleted_conversation_ids": [row["conversation_id"] for row in conv_rows],
            "existing_conversation_ids": self.list_existing_conversation_ids(user_id),
            "user_deleted": bool(user_row and user_row["ts"]),
            "user_deleted_at": user_row["ts"] if user_row else None,
            "delete_all_at": all_row["ts"] if all_row else None,
        }

    def collect_admin_data(self) -> dict[str, Any]:
        with self._db() as conn:
            user_rows = conn.execute(
                """
                SELECT username, role, last_login_at FROM users
                WHERE deleted_at IS NULL
                ORDER BY username ASC
                """
            ).fetchall()
            conv_rows = conn.execute(
                """
                SELECT c.username, c.conversation_id, c.title, c.updated_at,
                       COUNT(m.id) AS message_count
                FROM conversations c
                LEFT JOIN messages m
                    ON m.username=c.username AND m.conversation_id=c.conversation_id
                JOIN users u ON u.username=c.username
                WHERE c.deleted_at IS NULL AND u.deleted_at IS NULL
                GROUP BY c.username, c.conversation_id, c.title, c.updated_at
                ORDER BY c.username ASC, c.updated_at DESC
                """
            ).fetchall()
        users: dict[str, dict[str, Any]] = {}
        for row in user_rows:
            users[row["username"]] = {
                "role": row["role"],
                "last_login": row["last_login_at"],
                "conversations": {},
            }
        for row in conv_rows:
            users.setdefault(row["username"], {"role": "unknown", "last_login": "", "conversations": {}})
            users[row["username"]]["conversations"][row["conversation_id"]] = {
                "title": row["title"] or UNTITLED,
                "message_count": int(row["message_count"] or 0),
            }
        return {"users": users}

    def delete_conversation(self, username: str, conversation_id: str) -> None:
        user_id = normalize_user_id(username)
        conv_id = normalize_conversation_id(conversation_id)
        now = int(time.time())
        with self._db() as conn:
            conn.execute("DELETE FROM messages WHERE username=? AND conversation_id=?", (user_id, conv_id))
            conn.execute(
                "UPDATE conversations SET deleted_at=?, updated_at=? WHERE username=? AND conversation_id=?",
                (now, now, user_id, conv_id),
            )
            conn.execute(
                """
                INSERT INTO delete_events(event_type, username, conversation_id, created_at)
                VALUES('conversation', ?, ?, ?)
                """,
                (user_id, conv_id, now),
            )

    def delete_user(self, username: str) -> None:
        user_id = normalize_user_id(username)
        now = int(time.time())
        with self._db() as conn:
            conn.execute("DELETE FROM messages WHERE username=?", (user_id,))
            conn.execute("UPDATE conversations SET deleted_at=?, updated_at=? WHERE username=?", (now, now, user_id))
            conn.execute("UPDATE users SET deleted_at=? WHERE username=?", (now, user_id))
            conn.execute(
                """
                INSERT INTO delete_events(event_type, username, conversation_id, created_at)
                VALUES('user', ?, NULL, ?)
                """,
                (user_id, now),
            )

    def delete_all(self) -> None:
        now = int(time.time())
        with self._db() as conn:
            conn.execute("DELETE FROM messages")
            conn.execute("UPDATE conversations SET deleted_at=?, updated_at=?", (now, now))
            conn.execute("UPDATE users SET deleted_at=?", (now,))
            conn.execute(
                """
                INSERT INTO delete_events(event_type, username, conversation_id, created_at)
                VALUES('all', NULL, NULL, ?)
                """,
                (now,),
            )

    def stats(self) -> dict[str, int]:
        with self._db() as conn:
            users = conn.execute("SELECT COUNT(*) AS count FROM users WHERE deleted_at IS NULL").fetchone()["count"]
            conversations = conn.execute("SELECT COUNT(*) AS count FROM conversations WHERE deleted_at IS NULL").fetchone()["count"]
            messages = conn.execute("SELECT COUNT(*) AS count FROM messages").fetchone()["count"]
        return {"users": int(users), "conversations": int(conversations), "messages": int(messages)}

    def _sync_user_conn(
        self,
        conn: sqlite3.Connection,
        username: str,
        role: str,
        created_at: int,
        last_login_at: int,
        password_hash: str | None = None,
        preserve_role: bool = False,
    ) -> None:
        username = normalize_user_id(username)
        role = role or "user"
        if preserve_role:
            row = conn.execute("SELECT role FROM users WHERE username=?", (username,)).fetchone()
            if row:
                role = row["role"] or role
        conn.execute(
            """
            INSERT INTO users(username, role, password_hash, created_at, last_login_at, deleted_at)
            VALUES(?, ?, ?, ?, ?, NULL)
            ON CONFLICT(username) DO UPDATE SET
                role=excluded.role,
                password_hash=COALESCE(excluded.password_hash, users.password_hash),
                last_login_at=excluded.last_login_at,
                deleted_at=NULL
            """,
            (username, role, password_hash, created_at, last_login_at),
        )

    def _upsert_conversation_conn(
        self,
        conn: sqlite3.Connection,
        username: str,
        conversation_id: str,
        title: str,
        created_at: int,
        updated_at: int,
    ) -> None:
        username = normalize_user_id(username)
        conversation_id = normalize_conversation_id(conversation_id)
        safe_title = (title or UNTITLED).strip()[:MAX_TITLE_LENGTH] or UNTITLED
        conn.execute(
            """
            INSERT INTO conversations(username, conversation_id, title, created_at, updated_at, deleted_at)
            VALUES(?, ?, ?, ?, ?, NULL)
            ON CONFLICT(username, conversation_id) DO UPDATE SET
                title=excluded.title,
                updated_at=excluded.updated_at,
                deleted_at=NULL
            """,
            (username, conversation_id, safe_title, created_at, updated_at),
        )

    def _find_conversation_title(self, conn: sqlite3.Connection, username: str, conversation_id: str) -> str:
        row = conn.execute(
            """
            SELECT title FROM conversations
            WHERE username=? AND conversation_id=? AND deleted_at IS NULL
            """,
            (normalize_user_id(username), normalize_conversation_id(conversation_id)),
        ).fetchone()
        return str(row["title"]) if row and row["title"] else ""

    def _clean_history(self, raw_history: list[Any]) -> list[dict[str, str]]:
        cleaned = []
        for item in raw_history:
            if not isinstance(item, dict):
                continue
            role = self._normalize_role(str(item.get("role", "")))
            content_text = str(item.get("content") or item.get("text") or "").strip()
            if role and content_text:
                cleaned.append({"role": role, "content": content_text})
        return cleaned

    def _normalize_role(self, role: str) -> str:
        if role == "bot":
            return "assistant"
        if role in {"user", "assistant"}:
            return role
        return ""

    def _read_json(self, path: Path, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _int_or_now(self, value: Any, now: int) -> int:
        try:
            parsed = int(float(value))
            if parsed > 10_000_000_000:
                parsed = parsed // 1000
            return parsed
        except Exception:
            return now
