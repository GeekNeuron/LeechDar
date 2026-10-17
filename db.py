"""
لایه‌ی پایداری با SQLite — جایگزین دیکشنری در حافظه.

با این کار اگه ربات ری‌استارت بشه (کرش، آپدیت سرور، ری‌استارت
داکر)، صف کارها و تنظیمات از دست نمی‌رن.

جدول‌ها:
  tasks     — کارهای در حال پردازش (دانلود/صف/آماده‌ی آپلود)
  settings  — تنظیمات زمان‌اجرا (destination, upload_mode, ...)
  completed — شناسه‌ی موارد قبلاً تکمیل‌شده (برای جلوگیری از تکراری)
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path("work/leechdar.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    hash TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    torrent_name TEXT,
    status TEXT NOT NULL DEFAULT 'downloading',
    files_json TEXT NOT NULL DEFAULT '[]',
    magnet_hash TEXT,
    source TEXT NOT NULL DEFAULT 'torrent',
    source_url TEXT,
    aria2_gid TEXT,
    created_at REAL
);

CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER NOT NULL DEFAULT 0,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS completed (
    identifier TEXT PRIMARY KEY,
    torrent_name TEXT,
    finished_at REAL
);

CREATE TABLE IF NOT EXISTS allowed_users (
    user_id INTEGER PRIMARY KEY,
    is_owner INTEGER NOT NULL DEFAULT 0,
    added_at REAL
);
"""


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------
# tasks
# ------------------------------------------------------------------

def upsert_task(
    hash_: str,
    chat_id: int,
    torrent_name: str,
    status: str,
    files: list[str] | None = None,
    magnet_hash: str | None = None,
    source: str = "torrent",
    source_url: str | None = None,
    aria2_gid: str | None = None,
) -> None:
    files_json = json.dumps(files or [])
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks (hash, chat_id, torrent_name, status, files_json, magnet_hash, source, source_url, aria2_gid, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(hash) DO UPDATE SET
                chat_id=excluded.chat_id,
                torrent_name=excluded.torrent_name,
                status=excluded.status,
                files_json=excluded.files_json,
                magnet_hash=COALESCE(excluded.magnet_hash, tasks.magnet_hash),
                source=excluded.source,
                source_url=COALESCE(excluded.source_url, tasks.source_url),
                aria2_gid=COALESCE(excluded.aria2_gid, tasks.aria2_gid)
            """,
            (hash_, chat_id, torrent_name, status, files_json, magnet_hash, source, source_url, aria2_gid, time.time()),
        )


def get_task(hash_: str) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE hash = ?", (hash_,)).fetchone()
    return _row_to_task(row) if row else None


def all_tasks() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM tasks").fetchall()
    return [_row_to_task(r) for r in rows]


def delete_task(hash_: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM tasks WHERE hash = ?", (hash_,))


def _row_to_task(row: sqlite3.Row) -> dict:
    return {
        "hash": row["hash"],
        "chat_id": row["chat_id"],
        "torrent_name": row["torrent_name"],
        "status": row["status"],
        "files": json.loads(row["files_json"]),
        "magnet_hash": row["magnet_hash"],
        "source": row["source"],
        "source_url": row["source_url"],
        "aria2_gid": row["aria2_gid"],
    }


# ------------------------------------------------------------------
# settings (runtime config که با دستورات تلگرام تغییر می‌کنه)
# ------------------------------------------------------------------

def get_setting(key: str, default: str | None = None, user_id: int = 0) -> str | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ? AND user_id = ?", (key, user_id)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str, user_id: int = 0) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
            (user_id, key, value),
        )


# ------------------------------------------------------------------
# completed (برای جلوگیری از دانلود تکراری)
# ------------------------------------------------------------------

def is_completed(identifier: str) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM completed WHERE identifier = ?", (identifier,)).fetchone()
    return row is not None


def mark_completed(identifier: str, torrent_name: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO completed (identifier, torrent_name, finished_at) VALUES (?, ?, ?)",
            (identifier, torrent_name, time.time()),
        )


# ------------------------------------------------------------------
# allowed_users (برای claim خودکار اولین کاربر + مدیریت دستی بقیه)
# ------------------------------------------------------------------

def add_allowed_user(user_id: int, is_owner: bool = False) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO allowed_users (user_id, is_owner, added_at) VALUES (?, ?, ?)",
            (user_id, int(is_owner), time.time()),
        )


def remove_allowed_user(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM allowed_users WHERE user_id = ?", (user_id,))


def is_user_allowed(user_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM allowed_users WHERE user_id = ?", (user_id,)).fetchone()
    return row is not None


def count_allowed_users() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM allowed_users").fetchone()
    return row["c"]


def list_allowed_users() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute("SELECT user_id, is_owner FROM allowed_users ORDER BY added_at").fetchall()
    return [{"user_id": r["user_id"], "is_owner": bool(r["is_owner"])} for r in rows]


def is_user_owner(user_id: int) -> bool:
    with _connect() as conn:
        row = conn.execute("SELECT is_owner FROM allowed_users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row and row["is_owner"])
