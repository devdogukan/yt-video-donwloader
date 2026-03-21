import os
import re
import threading
import time
import queue
import json
import yt_dlp

import database as db
from database import Status

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

DOWNLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
os.makedirs(DOWNLOADS_DIR, exist_ok=True)


class DownloadTask:
    def __init__(self, download_id, url, format_id, output_path, manager,
                 concurrent_fragments=1):
        self.download_id = download_id
        self.url = url
        self.format_id = format_id
        self.output_path = output_path
        self.manager = manager
        self.concurrent_fragments = concurrent_fragments
        self._stop_event = threading.Event()
        self._thread = None
        self._last_db_write = 0.0

    def progress_hook(self, d):
        if self._stop_event.is_set():
            raise yt_dlp.utils.DownloadCancelled("Paused by user")

        if d["status"] == "downloading":
            downloaded = d.get("downloaded_bytes") or 0
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            progress = (downloaded / total * 100) if total else 0
            speed = _ANSI_RE.sub("", d.get("_speed_str", "")).strip()
            eta = _ANSI_RE.sub("", d.get("_eta_str", "")).strip()

            now = time.monotonic()
            if now - self._last_db_write >= 2.0:
                db.update_progress(self.download_id, downloaded, progress, speed, eta)
                self._last_db_write = now

            self.manager.broadcast({
                "type": "progress",
                "id": self.download_id,
                "downloaded_bytes": downloaded,
                "progress": round(progress, 1),
                "speed": speed,
                "eta": eta,
                "status": "downloading",
            })

        elif d["status"] == "finished":
            file_path = d.get("filename", self.output_path)
            db.update_progress(self.download_id, d.get("downloaded_bytes", 0), 100, "", "")
            db.update_file_path(self.download_id, file_path)

    def postprocessor_hook(self, d):
        if d["status"] == "started" and d.get("postprocessor") == "Merger":
            db.update_status(self.download_id, Status.MERGING)
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
            db.update_progress(self.download_id, 0, 100, "", "")
            self.manager.broadcast({
                "type": "status",
                "id": self.download_id,
                "status": Status.COMPLETED,
                "progress": 100,
            })
        except yt_dlp.utils.DownloadCancelled:
            db.update_status(self.download_id, Status.PAUSED)
            self.manager.broadcast({
                "type": "status",
                "id": self.download_id,
                "status": Status.PAUSED,
            })
        except Exception as e:
            db.update_status(self.download_id, Status.ERROR, str(e))
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
        self._subscribers: list[queue.Queue] = []
        self._sub_lock = threading.Lock()
        self._queue_event = threading.Event()
        self._queue_worker = threading.Thread(target=self._run_queue_worker, daemon=True)
        self._queue_worker.start()

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

    def _run_queue_worker(self):
        while True:
            self._queue_event.wait(timeout=3)
            self._queue_event.clear()
            if db.count_active_queued() > 0:
                continue
            next_dl = db.get_oldest_queued()
            if not next_dl:
                continue
            self._start_task(
                next_dl["id"], next_dl["url"], next_dl["format_id"],
                next_dl["file_path"],
                next_dl["concurrent_fragments"] or 1,
            )

    def _notify_queue(self):
        self._queue_event.set()

    def _create_download_record(self, url, video_info, format_id, quality_label,
                                concurrent_fragments=1, status=Status.PENDING,
                                is_queued=False):
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
            thumbnail=video_info["thumbnail"],
            duration=video_info["duration"],
            format_id=format_id,
            quality_label=quality_label,
            filesize=filesize,
            file_path=output_path,
            status=status,
            concurrent_fragments=concurrent_fragments,
            is_queued=is_queued,
        )
        return download_id

    def _start_task(self, download_id, url, format_id, output_path,
                    concurrent_fragments=1):
        task = DownloadTask(download_id, url, format_id, output_path, self,
                            concurrent_fragments)
        with self._lock:
            self._tasks[download_id] = task
        task.start()

    def get_video_info(self, url):
        ydl_opts = {"quiet": True, "no_warnings": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        formats = []
        seen = set()
        for f in info.get("formats", []):
            height = f.get("height")
            vcodec = f.get("vcodec", "none")
            acodec = f.get("acodec", "none")

            if vcodec == "none" and acodec != "none":
                label = f"Audio ({f.get('abr', '?')}kbps)"
                key = f"audio-{f.get('format_id')}"
            elif height:
                label = f"{height}p"
                if f.get("fps"):
                    label += f" {f['fps']}fps"
                key = f"{height}p"
            else:
                continue

            if key in seen:
                continue
            seen.add(key)

            formats.append({
                "format_id": f["format_id"],
                "label": label,
                "height": height or 0,
                "filesize": f.get("filesize") or f.get("filesize_approx") or 0,
                "ext": f.get("ext", "mp4"),
                "vcodec": vcodec,
                "acodec": acodec,
            })

        best_formats = self._build_quality_options(info)

        return {
            "video_id": info.get("id", ""),
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration", 0),
            "formats": best_formats,
        }

    def _build_quality_options(self, info):
        """Build user-friendly quality options using yt-dlp format selection."""
        options = []
        qualities = [
            ("2160", "4K (2160p)"),
            ("1440", "1440p"),
            ("1080", "1080p"),
            ("720", "720p"),
            ("480", "480p"),
            ("360", "360p"),
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

    def start_download(self, url, video_info, format_id, quality_label,
                       concurrent_fragments=1):
        download_id = self._create_download_record(
            url, video_info, format_id, quality_label, concurrent_fragments,
        )
        dl = db.get_download(download_id)
        self._start_task(download_id, url, format_id, dl["file_path"],
                         concurrent_fragments)
        self.broadcast({"type": "new", "download": dl})
        return download_id

    def enqueue_download(self, url, video_info, format_id, quality_label,
                         concurrent_fragments=1):
        download_id = self._create_download_record(
            url, video_info, format_id, quality_label, concurrent_fragments,
            status=Status.QUEUED, is_queued=True,
        )
        dl = db.get_download(download_id)
        self.broadcast({"type": "new", "download": dl})
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
            self.broadcast({"type": "status", "id": download_id, "status": Status.PAUSED})
            return True
        return False

    def resume_download(self, download_id, queued=False):
        dl = db.get_download(download_id)
        if not dl or dl["status"] not in (Status.PAUSED, Status.ERROR):
            return False

        db.update_is_queued(download_id, queued)

        if queued:
            db.update_status(download_id, Status.QUEUED)
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
            )
        return True

    def delete_download(self, download_id):
        with self._lock:
            task = self._tasks.get(download_id)
            if task:
                task.pause()
                time.sleep(0.5)
                self._tasks.pop(download_id, None)

        dl = db.get_download(download_id)
        if dl and dl["file_path"]:
            file_path = dl["file_path"]

            if "%(ext)s" in file_path:
                base_path = file_path.replace(".%(ext)s", "")
                import glob
                for f in glob.glob(base_path + ".*"):
                    try:
                        os.remove(f)
                    except OSError:
                        pass
            else:
                if os.path.exists(file_path):
                    os.remove(file_path)
                part = file_path + ".part"
                if os.path.exists(part):
                    os.remove(part)

        db.delete_download(download_id)
        self.broadcast({"type": "deleted", "id": download_id})
        return True

    def remove_task(self, download_id):
        with self._lock:
            self._tasks.pop(download_id, None)
        self._notify_queue()
