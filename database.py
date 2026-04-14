import sqlite3
import os
import threading
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
        CREATE TABLE IF NOT EXISTS playlists (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            playlist_id     TEXT,
            title           TEXT,
            thumbnail       TEXT,
            total_videos    INTEGER,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
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
            status          TEXT DEFAULT 'pending',
            file_path       TEXT,
            error_message   TEXT,
            concurrent_fragments INTEGER DEFAULT 1,
            is_queued       INTEGER DEFAULT 0,
            playlist_id     INTEGER REFERENCES playlists(id),
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    _migrate_add_column(conn, "downloads", "concurrent_fragments", "INTEGER DEFAULT 1")
    _migrate_add_column(conn, "downloads", "is_queued", "INTEGER DEFAULT 0")
    _migrate_add_column(conn, "downloads", "playlist_id", "INTEGER")
    conn.commit()


def _migrate_add_column(conn, table, column_name, column_def):
    cursor = conn.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]
    if column_name not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_name} {column_def}")


def create_download(video_id, url, title, thumbnail, duration, format_id,
                    quality_label, filesize, file_path,
                    status=Status.PENDING, concurrent_fragments=1,
                    is_queued=False, playlist_id=None):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO downloads
           (video_id, url, title, thumbnail, duration, format_id,
            quality_label, filesize, file_path, status, concurrent_fragments,
            is_queued, playlist_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (video_id, url, title, thumbnail, duration, format_id,
         quality_label, filesize, file_path, status, concurrent_fragments,
         int(is_queued), playlist_id),
    )
    conn.commit()
    return cursor.lastrowid


def update_status(download_id, status, error_message=None):
    conn = get_connection()
    conn.execute(
        "UPDATE downloads SET status = ?, error_message = ? WHERE id = ?",
        (status, error_message, download_id),
    )
    conn.commit()


def update_file_path(download_id, file_path):
    conn = get_connection()
    conn.execute(
        "UPDATE downloads SET file_path = ? WHERE id = ?",
        (file_path, download_id),
    )
    conn.commit()


def update_file_path_and_thumbnail(download_id, file_path, thumbnail):
    conn = get_connection()
    conn.execute(
        "UPDATE downloads SET file_path = ?, thumbnail = ? WHERE id = ?",
        (file_path, thumbnail, download_id),
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
    """On startup, mark any 'downloading' or 'merging' entries as 'paused'."""
    conn = get_connection()
    conn.execute(
        "UPDATE downloads SET status = ? WHERE status IN (?, ?)",
        (Status.PAUSED, Status.DOWNLOADING, Status.MERGING),
    )
    conn.commit()


def get_queued_ids():
    """Return IDs of downloads with QUEUED status, ordered by creation time."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT id FROM downloads WHERE status = ? ORDER BY created_at ASC",
        (Status.QUEUED,),
    ).fetchall()
    return [row["id"] for row in rows]


# ── Playlist helpers ─────────────────────────────────────────────────────────

def create_playlist(playlist_id, title, thumbnail, total_videos):
    conn = get_connection()
    cursor = conn.execute(
        """INSERT INTO playlists (playlist_id, title, thumbnail, total_videos)
           VALUES (?, ?, ?, ?)""",
        (playlist_id, title, thumbnail, total_videos),
    )
    conn.commit()
    return cursor.lastrowid


def get_playlist(row_id):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM playlists WHERE id = ?", (row_id,)
    ).fetchone()
    return dict[Any, Any](row) if row else None


def get_all_playlists():
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM playlists ORDER BY created_at DESC"
    ).fetchall()
    return [dict[Any, Any](row) for row in rows]


def has_playlist(playlist_id):
    """Check if a playlist with this YouTube ID already exists."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM playlists WHERE playlist_id = ? LIMIT 1",
        (playlist_id,),
    ).fetchone()
    return row["id"] if row else None


def delete_playlist(row_id):
    conn = get_connection()
    conn.execute("DELETE FROM playlists WHERE id = ?", (row_id,))
    conn.commit()


def get_downloads_by_playlist(playlist_id):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM downloads WHERE playlist_id = ? ORDER BY created_at ASC",
        (playlist_id,),
    ).fetchall()
    return [dict[Any, Any](row) for row in rows]
