import glob
import os
import re
import threading
import time
import queue
import json
from collections import deque

import yt_dlp

import database as db
from models import Status
from config import DOWNLOADS_DIR, THUMB_DIR
from utils import get_or_create_thumbnail

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _is_path_under_dir(path, parent_dir):
    try:
        abs_path = os.path.normcase(os.path.abspath(path))
        abs_parent = os.path.normcase(os.path.abspath(parent_dir))
        if abs_path == abs_parent:
            return False
        prefix = abs_parent + os.sep
        return abs_path.startswith(prefix)
    except OSError:
        return False


class DownloadTask:
    def __init__(self, download_id, url, format_id, output_path, manager,
                 concurrent_fragments=1, thumbnail_url=None):
        self.download_id = download_id
        self.url = url
        self.format_id = format_id
        self.output_path = output_path
        self.manager = manager
        self.concurrent_fragments = concurrent_fragments
        self.thumbnail_url = thumbnail_url
        self._stop_event = threading.Event()
        self._thread = None

    def progress_hook(self, d):
        if self._stop_event.is_set():
            raise yt_dlp.utils.DownloadCancelled("Paused by user")

        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            progress = (downloaded / total * 100) if total else 0
            speed = _ANSI_RE.sub("", d.get("_speed_str", "")).strip()
            eta = _ANSI_RE.sub("", d.get("_eta_str", "")).strip()

            state = {
                "downloaded_bytes": downloaded,
                "progress": round(progress, 1),
                "speed": speed,
                "eta": eta,
                "status": Status.DOWNLOADING,
            }
            self.manager.update_runtime_state(self.download_id, state)
            self.manager.broadcast({
                "type": "progress",
                "id": self.download_id,
                **state,
            })

        elif d["status"] == "finished":
            file_path = d.get("filename", self.output_path)
            db.update_file_path(self.download_id, file_path)

    def postprocessor_hook(self, d):
        if d["status"] == "started" and d.get("postprocessor") == "Merger":
            db.update_status(self.download_id, Status.MERGING)
            self.manager.update_runtime_state(self.download_id, {
                "status": Status.MERGING,
                "progress": 100,
                "speed": "",
                "eta": "",
            })
            self.manager.broadcast({
                "type": "status",
                "id": self.download_id,
                "status": Status.MERGING,
                "progress": 100,
            })

    def start(self):
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        db.update_status(self.download_id, Status.DOWNLOADING)
        self.manager.update_runtime_state(self.download_id, {
            "status": Status.DOWNLOADING,
            "progress": 0,
            "speed": "",
            "eta": "",
            "downloaded_bytes": 0,
        })
        self.manager.broadcast({
            "type": "status",
            "id": self.download_id,
            "status": Status.DOWNLOADING,
        })

        ydl_opts = {
            "format": self.format_id,
            "outtmpl": self.output_path,
            "continuedl": True,
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": "mp4",
            "concurrent_fragment_downloads": self.concurrent_fragments,
            "progress_hooks": [self.progress_hook],
            "postprocessor_hooks": [self.postprocessor_hook],
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([self.url])

            final_path = self.output_path
            base, _ = os.path.splitext(self.output_path)
            mp4_path = base + ".mp4"
            if os.path.exists(mp4_path):
                final_path = mp4_path

            db.update_status(self.download_id, Status.COMPLETED)
            db.update_file_path(self.download_id, final_path)
            self.manager.clear_runtime_state(self.download_id)
            self.manager.broadcast({
                "type": "status",
                "id": self.download_id,
                "status": Status.COMPLETED,
                "progress": 100,
                "file_path": final_path,
            })
        except yt_dlp.utils.DownloadCancelled:
            db.update_status(self.download_id, Status.PAUSED)
            self.manager.clear_runtime_state(self.download_id)
            self.manager.broadcast({
                "type": "status",
                "id": self.download_id,
                "status": Status.PAUSED,
            })
        except Exception as e:
            db.update_status(self.download_id, Status.ERROR, str(e))
            self.manager.clear_runtime_state(self.download_id)
            self.manager.broadcast({
                "type": "status",
                "id": self.download_id,
                "status": Status.ERROR,
                "error_message": str(e),
            })
        finally:
            self.manager.remove_task(self.download_id)

    def pause(self):
        self._stop_event.set()


class DownloadManager:
    def __init__(self):
        self._tasks: dict[int, DownloadTask] = {}
        self._lock = threading.Lock()
        self._runtime_state: dict[int, dict] = {}
        self._state_lock = threading.Lock()

        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()

        # Event-driven queue: in-memory tracking instead of DB polling
        self._queued_ids: deque[int] = deque()
        self._queued_active_ids: set[int] = set()
        self._queue_event = threading.Event()
        self._queue_worker = threading.Thread(target=self._run_queue_worker, daemon=True)
        self._queue_worker.start()

    # ── Runtime state (replaces DB progress writes) ───────────────────────

    def update_runtime_state(self, download_id, state: dict):
        with self._state_lock:
            self._runtime_state[download_id] = state

    def get_runtime_state(self, download_id):
        with self._state_lock:
            return self._runtime_state.get(download_id)

    def clear_runtime_state(self, download_id):
        with self._state_lock:
            self._runtime_state.pop(download_id, None)

    def get_downloads_with_runtime(self, title = None):
        """Return all DB records enriched with in-memory runtime state."""
        downloads = db.get_all_downloads(title)
        with self._state_lock:
            for dl in downloads:
                runtime = self._runtime_state.get(dl["id"])
                if runtime:
                    dl.update(runtime)
                else:
                    dl.setdefault("downloaded_bytes", 0)
                    dl.setdefault("progress",
                                  100.0 if dl["status"] == Status.COMPLETED else 0.0)
                    dl.setdefault("speed", "")
                    dl.setdefault("eta", "")
        return downloads

    # ── SSE pub/sub ───────────────────────────────────────────────────────

    def subscribe(self):
        q = queue.Queue()
        with self._sub_lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            self._subscribers.remove(q)

    def broadcast(self, event: dict):
        data = json.dumps(event, default=str)
        with self._sub_lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(data)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.remove(q)

    # ── Event-driven queue ────────────────────────────────────────────────

    def load_queue_from_db(self):
        """One-time recovery: load QUEUED items from DB into memory.
        Call after db.init_db() and db.mark_interrupted_as_paused()."""
        ids = db.get_queued_ids()
        with self._lock:
            self._queued_ids.extend(ids)
        if ids:
            self._notify_queue()

    def _run_queue_worker(self):
        while True:
            self._queue_event.wait()
            self._queue_event.clear()

            with self._lock:
                if self._queued_active_ids:
                    continue
                if not self._queued_ids:
                    continue
                download_id = self._queued_ids.popleft()

            dl = db.get_download(download_id)
            if not dl or dl["status"] != Status.QUEUED:
                self._notify_queue()
                continue

            with self._lock:
                self._queued_active_ids.add(download_id)

            self._start_task(
                download_id, dl["url"], dl["format_id"],
                dl["file_path"],
                dl["concurrent_fragments"] or 1,
                thumbnail_url=dl["thumbnail"],
            )

    def _notify_queue(self):
        self._queue_event.set()

    # ── Download record & task lifecycle ──────────────────────────────────

    def _create_download_record(self, url, video_info, format_id, quality_label,
                                concurrent_fragments=1, status=Status.PENDING,
                                is_queued=False, playlist_id=None):
        title = video_info["title"]
        safe_title = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)
        output_path = os.path.join(DOWNLOADS_DIR, f"{safe_title}.%(ext)s")

        filesize = 0
        for f in video_info.get("formats", []):
            if f["format_id"] == format_id:
                filesize = f.get("filesize", 0)
                break

        download_id = db.create_download(
            video_id=video_info["video_id"],
            url=url,
            title=title,
            thumbnail=get_or_create_thumbnail(thumbnail_url=video_info["thumbnail"]),
            duration=video_info["duration"],
            format_id=format_id,
            quality_label=quality_label,
            filesize=filesize,
            file_path=output_path,
            status=status,
            concurrent_fragments=concurrent_fragments,
            is_queued=is_queued,
            playlist_id=playlist_id,
        )
        return download_id

    def _start_task(self, download_id, url, format_id, output_path,
                    concurrent_fragments=1, thumbnail_url=None):
        task = DownloadTask(download_id, url, format_id, output_path, self,
                            concurrent_fragments, thumbnail_url)
        with self._lock:
            self._tasks[download_id] = task
        task.start()

    def get_video_info(self, url):
        ydl_opts = {"quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if info.get("_type") == "playlist" or "entries" in info:
            return self._build_playlist_info(info)

        best_formats = self._build_quality_options(info)

        return {
            "type": "video",
            "video_id": info.get("id", ""),
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration", 0),
            "formats": best_formats,
        }

    def _build_playlist_info(self, info):
        entries = []
        for entry in info.get("entries", []):
            if entry is None:
                continue
            formats = self._build_quality_options(entry)
            video_id = entry.get("id", "")
            video_url = entry.get("webpage_url") or entry.get("url") or ""
            entries.append({
                "video_id": video_id,
                "title": entry.get("title", ""),
                "thumbnail": entry.get("thumbnail") or entry.get("thumbnails", [{}])[-1].get("url", ""),
                "duration": entry.get("duration", 0),
                "url": video_url,
                "formats": formats,
            })

        return {
            "type": "playlist",
            "playlist_id": info.get("id", ""),
            "playlist_title": info.get("title", ""),
            "playlist_thumbnail": info.get("thumbnails", [{}])[-1].get("url", "") if info.get("thumbnails") else "",
            "entries": entries,
        }

    def _build_quality_options(self, info):
        """Build user-friendly quality options using yt-dlp format selection."""
        options = []
        qualities = [
            ("4320", "8K (4320p)"),
            ("2160", "4K (2160p)"),
            ("1440", "1440p"),
            ("1080", "1080p"),
            ("720", "720p"),
            ("480", "480p"),
            ("360", "360p"),
            ("240", "240p"),
            ("144", "144p"),
        ]


        available_heights = set()
        for f in info.get("formats", []):
            h = f.get("height")
            if h:
                available_heights.add(h)

        for height_str, label in qualities:
            height = int(height_str)
            if height in available_heights:
                filesize = 0
                for f in info.get("formats", []):
                    if f.get("height") == height:
                        filesize = max(filesize, f.get("filesize") or f.get("filesize_approx") or 0)

                options.append({
                    "format_id": f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[acodec^=mp4a]/bestvideo[height<={height}]+bestaudio/best[height<={height}]",
                    "label": label,
                    "height": height,
                    "filesize": filesize,
                })

        options.append({
            "format_id": "bestaudio/best",
            "label": "Audio Only (Best)",
            "height": 0,
            "filesize": 0,
        })

        return options

    # ── Public download operations ────────────────────────────────────────

    def start_download(self, url, video_info, format_id, quality_label,
                       concurrent_fragments=1, playlist_id=None):
        download_id = self._create_download_record(
            url, video_info, format_id, quality_label, concurrent_fragments,
            playlist_id=playlist_id,
        )
        dl = db.get_download(download_id)
        self._start_task(download_id, url, format_id, dl["file_path"],
                         concurrent_fragments, thumbnail_url=dl["thumbnail"])
        self.broadcast({"type": "new", "download": dl})
        return download_id

    def enqueue_download(self, url, video_info, format_id, quality_label,
                         concurrent_fragments=1, playlist_id=None):
        download_id = self._create_download_record(
            url, video_info, format_id, quality_label, concurrent_fragments,
            status=Status.QUEUED, is_queued=True, playlist_id=playlist_id,
        )
        dl = db.get_download(download_id)
        self.broadcast({"type": "new", "download": dl})

        with self._lock:
            self._queued_ids.append(download_id)
        self._notify_queue()
        return download_id

    def pause_download(self, download_id):
        with self._lock:
            task = self._tasks.get(download_id)
        if task:
            task.pause()
            return True

        dl = db.get_download(download_id)
        if dl and dl["status"] == Status.QUEUED:
            db.update_status(download_id, Status.PAUSED)
            with self._lock:
                try:
                    self._queued_ids.remove(download_id)
                except ValueError:
                    pass
            self.broadcast({"type": "status", "id": download_id, "status": Status.PAUSED})
            return True
        return False

    def resume_download(self, download_id, queued=False):
        dl = db.get_download(download_id)
        if not dl or dl["status"] not in (Status.PAUSED, Status.ERROR):
            return False

        if queued:
            db.update_status(download_id, Status.QUEUED)
            with self._lock:
                self._queued_ids.append(download_id)
            self.broadcast({
                "type": "status",
                "id": download_id,
                "status": Status.QUEUED,
            })
            self._notify_queue()
        else:
            self._start_task(
                download_id, dl["url"], dl["format_id"], dl["file_path"],
                dl["concurrent_fragments"] or 1,
                thumbnail_url=dl["thumbnail"],
            )
        return True

    def delete_download(self, download_id):
        with self._lock:
            task = self._tasks.get(download_id)
            if task:
                task.pause()
                time.sleep(0.5)
                self._tasks.pop(download_id, None)
            try:
                self._queued_ids.remove(download_id)
            except ValueError:
                pass
            self._queued_active_ids.discard(download_id)

        self.clear_runtime_state(download_id)

        dl = db.get_download(download_id)
        if dl:
            if dl["file_path"]:
                file_path = dl["file_path"]

                if "%(ext)s" in file_path:
                    base_path = file_path.replace(".%(ext)s", "")
                    for f in glob.glob(base_path + ".*"):
                        if not _is_path_under_dir(f, DOWNLOADS_DIR):
                            continue
                        try:
                            os.remove(f)
                        except OSError:
                            pass
                else:
                    if _is_path_under_dir(file_path, DOWNLOADS_DIR):
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        part = file_path + ".part"
                        if os.path.exists(part):
                            os.remove(part)

            if dl["thumbnail"]:
                thumbnail_path = os.path.join(THUMB_DIR, dl["thumbnail"])
                if os.path.exists(thumbnail_path):
                    os.remove(thumbnail_path)

        db.delete_download(download_id)
        self.broadcast({"type": "deleted", "id": download_id})
        return True

    def remove_task(self, download_id):
        with self._lock:
            self._tasks.pop(download_id, None)
            self._queued_active_ids.discard(download_id)
        self._notify_queue()
