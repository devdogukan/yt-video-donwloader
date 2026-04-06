import sqlite3
import os
import threading
from datetime import datetime
from enum import StrEnum
from typing import Any


class Status(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.db")

_local = threading.local()


def get_connection():
    if not hasattr(_local, "connection") or _local.connection is None:
        _local.connection = sqlite3.connect(DB_PATH)
        _local.connection.row_factory = sqlite3.Row
        _local.connection.execute("PRAGMA journal_mode=WAL")
        _local.connection.execute("PRAGMA busy_timeout=5000")
    return _local.connection


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS downloads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id        TEXT,
            url             TEXT,
            title           TEXT,
            thumbnail       TEXT,
            duration        INTEGER,
            format_id       TEXT,
            quality_label   TEXT,
            filesize        INTEGER,
            downloaded_bytes INTEGER DEFAULT 0,
            progress        REAL DEFAULT 0,
            speed           TEXT,
            eta             TEXT,
            status          TEXT DEFAULT 'pending',
            file_path       TEXT,
            error_message   TEXT,
            concurrent_fragments INTEGER DEFAULT 1,
            is_queued       INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _migrate_add_column(conn, "concurrent_fragments", "INTEGER DEFAULT 1")
    _migrate_add_column(conn, "is_queued", "INTEGER DEFAULT 0")
    conn.commit()


def _migrate_add_column(conn, column_name, column_def):
    cursor = conn.execute("PRAGMA table_info(downloads)")
    columns = [row[1] for row in cursor.fetchall()]
    if column_name not in columns:
        conn.execute(f"ALTER TABLE downloads ADD COLUMN {column_name} {column_def}")


def create_download(video_id, url, title, thumbnail, duration, format_id,
                    quality_label, filesize, file_path,
                    status=Status.PENDING, concurrent_fragments=1,
                    is_queued=False):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO downloads
           (video_id, url, title, thumbnail, duration, format_id,
            quality_label, filesize, file_path, status, concurrent_fragments,
            is_queued)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (video_id, url, title, thumbnail, duration, format_id,
         quality_label, filesize, file_path, status, concurrent_fragments,
         int(is_queued)),
    )
    conn.commit()
    return cursor.lastrowid


def update_progress(download_id, downloaded_bytes, progress, speed, eta):
    conn = get_connection()
    conn.execute(
        """UPDATE downloads
           SET downloaded_bytes = ?, progress = ?, speed = ?, eta = ?,
               updated_at = ?
           WHERE id = ?""",
        (downloaded_bytes, progress, speed, eta, datetime.now(), download_id),
    )
    conn.commit()


def update_status(download_id, status, error_message=None):
    conn = get_connection()
    conn.execute(
        """UPDATE downloads
           SET status = ?, error_message = ?, updated_at = ?
           WHERE id = ?""",
        (status, error_message, datetime.now(), download_id),
    )
    conn.commit()


def update_file_path(download_id, file_path):
    conn = get_connection()
    conn.execute(
        """UPDATE downloads SET file_path = ?, updated_at = ? WHERE id = ?""",
        (file_path, datetime.now(), download_id),
    )
    conn.commit()


def get_all_downloads():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM downloads ORDER BY created_at DESC"
    ).fetchall()
    return [dict[Any, Any](row) for row in rows]


def get_download(download_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM downloads WHERE id = ?", (download_id,)
    ).fetchone()
    return dict[Any, Any](row) if row else None


def has_active_download(video_id):
    """Check if there is a non-deleted download for this video."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM downloads WHERE video_id = ? LIMIT 1",
        (video_id,),
    ).fetchone()
    return row is not None


def delete_download(download_id):
    conn = get_connection()
    conn.execute("DELETE FROM downloads WHERE id = ?", (download_id,))
    conn.commit()


def mark_interrupted_as_paused():
    """On startup, mark any 'downloading' entries as 'paused'."""
    conn = get_connection()
    conn.execute(
        """UPDATE downloads SET status = ?, updated_at = ?
           WHERE status = ?""",
        (Status.PAUSED, datetime.now(), Status.DOWNLOADING),
    )
    conn.commit()


def get_oldest_queued():
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM downloads WHERE status = ? ORDER BY created_at ASC LIMIT 1",
        (Status.QUEUED,),
    ).fetchone()
    return dict[Any, Any](row) if row else None


def count_by_status(status):
    conn = get_connection()
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM downloads WHERE status = ?",
        (status,),
    ).fetchone()
    return row["cnt"] if row else 0


def update_is_queued(download_id, is_queued):
    conn = get_connection()
    conn.execute(
        """UPDATE downloads SET is_queued = ?, updated_at = ? WHERE id = ?""",
        (int(is_queued), datetime.now(), download_id),
    )
    conn.commit()


def count_active_queued():
    conn = get_connection()
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM downloads
           WHERE is_queued = 1 AND status IN (?, ?)""",
        (Status.DOWNLOADING, Status.MERGING),
    ).fetchone()
    return row["cnt"] if row else 0
