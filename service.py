import mimetypes
import os

from werkzeug.utils import secure_filename

import database as db
from config import DOWNLOADS_DIR, THUMB_DIR
from downloader import DownloadManager
from models import Status, NotFoundError, ConflictError, ValidationError
from players import open_in_default_player
from utils import get_or_create_thumbnail, get_video_duration_seconds


class VideoService:
    def __init__(self):
        self._manager = DownloadManager()

    def init(self):
        db.init_db()
        db.mark_interrupted_as_paused()
        self._manager.load_queue_from_db()

    # ── Video / Playlist Info ─────────────────────────────────────────────

    def get_video_info(self, url: str) -> dict:
        if not url:
            raise ValidationError("URL is required")
        return self._manager.get_video_info(url)

    # ── Local Upload ──────────────────────────────────────────────────────

    def add_local_download(self, file_path: str, title: str | None = None) -> dict:
        path = os.path.abspath(file_path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        duration = get_video_duration_seconds(path)

        display_title = title or os.path.basename(path)

        download_id = db.create_download(
            video_id=None, url=None, title=display_title,
            thumbnail=get_or_create_thumbnail(video_path=path),
            duration=duration, format_id=None, quality_label=None,
            filesize=size, file_path=path, status=Status.COMPLETED,
        )
        dl = db.get_download(download_id)
        self._manager.broadcast({"type": "new", "download": dl})
        return {"id": download_id, "status": Status.COMPLETED}

    def save_upload(self, original_filename: str, mimetype: str | None,
                    save_cb) -> str:
        """Save an uploaded file to the downloads dir and return the path."""
        basename = self._safe_filename(original_filename, mimetype)
        dest = self._unique_path(basename)
        save_cb(dest)
        return dest

    # ── Single Download ───────────────────────────────────────────────────

    def start_download(self, url: str, video_info: dict, format_id: str,
                       quality_label: str, concurrent_fragments: int,
                       queued: bool) -> dict:
        if not url or not format_id or not video_info:
            raise ValidationError("url, format_id, and video_info are required")

        video_id = video_info.get("video_id", "")
        if video_id and db.has_active_download(video_id):
            raise ConflictError("This video is already in the download list.")

        if queued:
            download_id = self._manager.enqueue_download(
                url, video_info, format_id, quality_label, concurrent_fragments)
            return {"id": download_id, "status": Status.QUEUED}

        download_id = self._manager.start_download(
            url, video_info, format_id, quality_label, concurrent_fragments)
        return {"id": download_id, "status": Status.DOWNLOADING}

    # ── Custom Playlist Management ───────────────────────────────────────

    def create_custom_playlist(self, title: str) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValidationError("Playlist title is required")
        row_id = db.create_playlist(
            playlist_id=None, title=title, thumbnail=None, total_videos=0,
        )
        pl = db.get_playlist(row_id)
        self._manager.broadcast({"type": "playlist_created", "playlist": pl})
        return pl

    def rename_playlist(self, playlist_id: int, title: str) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValidationError("Playlist title is required")
        pl = db.get_playlist(playlist_id)
        if not pl:
            raise NotFoundError("Playlist not found")
        db.update_playlist_title(playlist_id, title)
        updated_pl = db.get_playlist(playlist_id)
        self._manager.broadcast({
            "type": "playlist_updated", "playlist": updated_pl,
        })
        return updated_pl

    def add_to_playlist(self, playlist_id: int, download_id: int) -> None:
        pl = db.get_playlist(playlist_id)
        if not pl:
            raise NotFoundError("Playlist not found")
        dl = db.get_download(download_id)
        if not dl:
            raise NotFoundError("Download not found")
        if dl["playlist_id"]:
            raise ConflictError("Download already belongs to a playlist")
        db.update_download_playlist(download_id, playlist_id)
        db.update_playlist_total(playlist_id)
        if not pl["thumbnail"] and dl.get("thumbnail"):
            db.update_playlist_thumbnail(playlist_id, dl["thumbnail"])
            self._manager.broadcast({
                "type": "playlist_updated",
                "playlist": db.get_playlist(playlist_id),
            })
        updated_dl = db.get_download(download_id)
        self._manager.broadcast({
            "type": "download_moved",
            "download": updated_dl,
            "playlist_id": playlist_id,
        })

    def update_playlist_thumbnail_upload(self, playlist_id: int,
                                           filename: str, mimetype: str | None,
                                           save_cb) -> dict:
        pl = db.get_playlist(playlist_id)
        if not pl:
            raise NotFoundError("Playlist not found")
        ext = self._guess_extension(mimetype)
        safe_name = secure_filename(filename) or "thumb"
        stem = os.path.splitext(safe_name)[0] or "thumb"
        dest_name = f"pl_{playlist_id}_{stem}{ext}"
        dest_path = os.path.join(THUMB_DIR, dest_name)
        save_cb(dest_path)
        db.update_playlist_thumbnail(playlist_id, dest_name)
        updated_pl = db.get_playlist(playlist_id)
        self._manager.broadcast({
            "type": "playlist_updated", "playlist": updated_pl,
        })
        return updated_pl

    def set_playlist_thumbnail_from_video(self, playlist_id: int,
                                           download_id: int) -> dict:
        pl = db.get_playlist(playlist_id)
        if not pl:
            raise NotFoundError("Playlist not found")
        dl = db.get_download(download_id)
        if not dl:
            raise NotFoundError("Download not found")
        thumb = dl.get("thumbnail")
        if not thumb:
            raise ValidationError("Selected video has no thumbnail")
        db.update_playlist_thumbnail(playlist_id, thumb)
        updated_pl = db.get_playlist(playlist_id)
        self._manager.broadcast({
            "type": "playlist_updated", "playlist": updated_pl,
        })
        return updated_pl

    def remove_from_playlist(self, download_id: int) -> None:
        dl = db.get_download(download_id)
        if not dl:
            raise NotFoundError("Download not found")
        old_playlist_id = dl["playlist_id"]
        if not old_playlist_id:
            raise ConflictError("Download is not in any playlist")
        db.update_download_playlist(download_id, None)
        db.update_playlist_total(old_playlist_id)
        updated_dl = db.get_download(download_id)
        self._manager.broadcast({
            "type": "download_moved",
            "download": updated_dl,
            "old_playlist_id": old_playlist_id,
        })

    # ── Playlist Download ─────────────────────────────────────────────────

    def start_playlist_download(self, playlist_info: dict, items: list,
                                concurrent_fragments: int) -> dict:
        if not playlist_info or not items:
            raise ValidationError("playlist_info and downloads are required")

        yt_playlist_id = playlist_info.get("playlist_id", "")
        pl_row_id = db.has_playlist(yt_playlist_id) if yt_playlist_id else None

        if not pl_row_id:
            pl_thumbnail = get_or_create_thumbnail(
                thumbnail_url=playlist_info.get("playlist_thumbnail", ""))
            pl_row_id = db.create_playlist(
                playlist_id=yt_playlist_id,
                title=playlist_info.get("playlist_title", ""),
                thumbnail=pl_thumbnail,
                total_videos=len(items),
            )

        results = []
        for item in items:
            url = item.get("url", "").strip()
            video_info = item.get("video_info", {})
            format_id = item.get("format_id", "")
            quality_label = item.get("quality_label", "")

            if not url or not video_info or not format_id:
                continue

            video_id = video_info.get("video_id", "")
            if video_id and db.has_active_download(video_id):
                continue

            download_id = self._manager.enqueue_download(
                url, video_info, format_id, quality_label,
                concurrent_fragments, playlist_id=pl_row_id,
            )
            results.append({"id": download_id, "status": Status.QUEUED})

        return {"playlist_id": pl_row_id, "results": results}

    # ── Pause / Resume ────────────────────────────────────────────────────

    def pause_download(self, download_id: int) -> None:
        ok = self._manager.pause_download(download_id)
        if not ok:
            raise NotFoundError("Download not active")

    def resume_download(self, download_id: int, queued: bool = False) -> str:
        ok = self._manager.resume_download(download_id, queued=queued)
        if not ok:
            raise NotFoundError("Cannot resume this download")
        return Status.QUEUED if queued else Status.DOWNLOADING

    # ── Delete ────────────────────────────────────────────────────────────

    def delete_download(self, download_id: int) -> None:
        dl = db.get_download(download_id)
        if not dl:
            raise NotFoundError("Download not found")
        self._manager.delete_download(download_id)

    def delete_playlist(self, playlist_id: int) -> None:
        pl = db.get_playlist(playlist_id)
        if not pl:
            raise NotFoundError("Playlist not found")

        for dl in db.get_downloads_by_playlist(playlist_id):
            self._manager.delete_download(dl["id"])

        db.delete_playlist(playlist_id)
        self._manager.broadcast({"type": "playlist_deleted", "playlist_id": playlist_id})

    # ── Playlist Pause / Resume ───────────────────────────────────────────

    def pause_playlist(self, playlist_id: int) -> int:
        pl = db.get_playlist(playlist_id)
        if not pl:
            raise NotFoundError("Playlist not found")
        count = 0
        for dl in db.get_downloads_by_playlist(playlist_id):
            if dl["status"] in (Status.DOWNLOADING, Status.QUEUED):
                if self._manager.pause_download(dl["id"]):
                    count += 1
        return count

    def resume_playlist(self, playlist_id: int) -> int:
        pl = db.get_playlist(playlist_id)
        if not pl:
            raise NotFoundError("Playlist not found")
        count = 0
        for dl in db.get_downloads_by_playlist(playlist_id):
            if dl["status"] in (Status.PAUSED, Status.ERROR):
                if self._manager.resume_download(dl["id"], queued=True):
                    count += 1
        return count

    # ── Listing ───────────────────────────────────────────────────────────

    def list_downloads(self) -> dict:
        return {
            "downloads": self._manager.get_downloads_with_runtime(),
            "playlists": db.get_all_playlists(),
        }

    # ── File Access ───────────────────────────────────────────────────────

    def get_download_file(self, download_id: int) -> tuple[str, str]:
        """Return (resolved_path, mimetype) or raise NotFoundError."""
        dl = db.get_download(download_id)
        if not dl or dl["status"] != Status.COMPLETED:
            raise NotFoundError("File not available")

        resolved = self._resolve_file_path(dl)
        if not resolved:
            raise NotFoundError("File not found on disk")

        mime = mimetypes.guess_type(resolved)[0] or "video/mp4"
        return resolved, mime

    def open_in_player(self, download_id: int) -> None:
        dl = db.get_download(download_id)
        if not dl or dl["status"] != Status.COMPLETED:
            raise NotFoundError("File not available")

        resolved = self._resolve_file_path(dl)
        if not resolved:
            raise NotFoundError("File not found on disk")

        open_in_default_player(resolved)

    # ── SSE Delegation ────────────────────────────────────────────────────

    def subscribe(self):
        return self._manager.subscribe()

    def unsubscribe(self, q):
        self._manager.unsubscribe(q)

    # ── Private Helpers ───────────────────────────────────────────────────

    @staticmethod
    def _resolve_file_path(dl) -> str | None:
        file_path = dl["file_path"]
        if "%(ext)s" not in file_path and os.path.exists(file_path):
            return file_path
        base_path = file_path.replace(".%(ext)s", "")
        for ext in [".mp4", ".webm", ".mkv"]:
            candidate = base_path + ext
            if os.path.exists(candidate):
                return candidate
        return None

    @staticmethod
    def _guess_extension(mimetype: str | None) -> str:
        mime = (mimetype or "").split(";")[0].strip()
        ext = mimetypes.guess_extension(mime or "") or ".mp4"
        return ".jpeg" if ext == ".jpe" else ext

    @staticmethod
    def _safe_filename(original: str, mimetype: str | None = None) -> str:
        base = secure_filename(original)
        if base and not base.startswith(".") and "." in base:
            return base
        stem = base if base and not base.startswith(".") else "video"
        return f"{stem}{VideoService._guess_extension(mimetype)}"

    @staticmethod
    def _unique_path(basename: str) -> str:
        dest = os.path.join(DOWNLOADS_DIR, basename)
        stem, ext = os.path.splitext(basename)
        n = 0
        while os.path.exists(dest):
            n += 1
            dest = os.path.join(DOWNLOADS_DIR, f"{stem}_{n}{ext}")
        return dest
