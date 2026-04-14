import json
import os

import mimetypes

from flask import Flask, render_template, request, jsonify, Response, send_file, send_from_directory
from werkzeug.utils import secure_filename

import database as db
from database import Status
from downloader import DownloadManager, DOWNLOADS_DIR
from players import open_in_default_player
from utils import get_or_create_thumbnail, THUMB_DIR

app = Flask(__name__)
manager = DownloadManager()

db.init_db()
db.mark_interrupted_as_paused()
manager.load_queue_from_db()


# ── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── REST API ─────────────────────────────────────────────────────────────────

@app.post("/api/video/info")
def video_info():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    try:
        info = manager.get_video_info(url)
        return jsonify(info)
    except Exception as e:
        return jsonify({"error": str(e)}), 400


def _guess_extension(mimetype: str | None) -> str:
    mime = (mimetype or "").split(";")[0].strip()
    ext = mimetypes.guess_extension(mime or "") or ".mp4"
    return ".jpeg" if ext == ".jpe" else ext


def _safe_filename(original: str, mimetype: str | None = None) -> str:
    """Sanitize an upload filename, ensuring it has a valid extension."""
    base = secure_filename(original)
    if base and not base.startswith(".") and "." in base:
        return base
    stem = base if base and not base.startswith(".") else "video"
    return f"{stem}{_guess_extension(mimetype)}"


def _unique_path_in_downloads_dir(basename: str) -> str:
    dest = os.path.join(DOWNLOADS_DIR, basename)
    stem, ext = os.path.splitext(basename)
    n = 0
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(DOWNLOADS_DIR, f"{stem}_{n}{ext}")
    return dest


@app.post("/api/downloads/local")
def add_local_download():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "file is required"}), 400

    dest = _unique_path_in_downloads_dir(_safe_filename(f.filename, f.mimetype))
    try:
        f.save(dest)
    except OSError as e:
        return jsonify({"error": str(e)}), 500

    path = os.path.abspath(dest)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = None

    download_id = db.create_download(
        video_id=None, url=None, title=os.path.basename(path),
        thumbnail=get_or_create_thumbnail(video_path=path),
        duration=None, format_id=None, quality_label=None,
        filesize=size, file_path=path, status=Status.COMPLETED,
    )
    dl = db.get_download(download_id)
    manager.broadcast({"type": "new", "download": dl})
    return jsonify({"id": download_id, "status": Status.COMPLETED})


@app.post("/api/download/start")
def download_start():
    data = request.get_json(silent=True) or {}
    url: str = data.get("url", "").strip()
    format_id: str = data.get("format_id", "")
    quality_label: str = data.get("quality_label", "")
    video_info: dict = data.get("video_info", {})
    concurrent_fragments: int = max(1, min(16, int(data.get("concurrent_fragments", 1))))
    queued: bool = bool(data.get("queued", False))

    if not url or not format_id or not video_info:
        return jsonify({"error": "url, format_id, and video_info are required"}), 400

    video_id = video_info.get("video_id", "")
    if video_id and db.has_active_download(video_id):
        return jsonify({"error": "This video is already in the download list."}), 409

    if queued:
        download_id = manager.enqueue_download(
            url, video_info, format_id, quality_label, concurrent_fragments)
        return jsonify({"id": download_id, "status": Status.QUEUED})

    download_id = manager.start_download(
        url, video_info, format_id, quality_label, concurrent_fragments)
    return jsonify({"id": download_id, "status": Status.DOWNLOADING})


@app.post("/api/playlist/download")
def playlist_download():
    data = request.get_json(silent=True) or {}
    playlist_info = data.get("playlist_info", {})
    items = data.get("downloads", [])
    concurrent_fragments = max(1, min(16, int(data.get("concurrent_fragments", 1))))

    if not playlist_info or not items:
        return jsonify({"error": "playlist_info and downloads are required"}), 400

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

        download_id = manager.enqueue_download(
            url, video_info, format_id, quality_label,
            concurrent_fragments, playlist_id=pl_row_id,
        )
        results.append({"id": download_id, "status": Status.QUEUED})

    return jsonify({"playlist_id": pl_row_id, "results": results})


@app.delete("/api/playlist/<int:playlist_id>")
def playlist_delete(playlist_id):
    pl = db.get_playlist(playlist_id)
    if not pl:
        return jsonify({"error": "Playlist not found"}), 404

    for dl in db.get_downloads_by_playlist(playlist_id):
        manager.delete_download(dl["id"])

    db.delete_playlist(playlist_id)
    manager.broadcast({"type": "playlist_deleted", "playlist_id": playlist_id})
    return jsonify({"status": "deleted"})


@app.post("/api/download/<int:download_id>/pause")
def download_pause(download_id):
    ok = manager.pause_download(download_id)
    if ok:
        return jsonify({"status": "paused"})
    return jsonify({"error": "Download not active"}), 404


@app.post("/api/download/<int:download_id>/resume")
def download_resume(download_id):
    data = request.get_json(silent=True) or {}
    queued = bool(data.get("queued", False))
    ok = manager.resume_download(download_id, queued=queued)
    if ok:
        status = Status.QUEUED if queued else Status.DOWNLOADING
        return jsonify({"status": status})
    return jsonify({"error": "Cannot resume this download"}), 400


@app.delete("/api/download/<int:download_id>")
def download_delete(download_id):
    dl = db.get_download(download_id)
    if not dl:
        return jsonify({"error": "Download not found"}), 404
    manager.delete_download(download_id)
    return jsonify({"status": "deleted"})


@app.get("/api/downloads")
def downloads_list():
    return jsonify({
        "downloads": manager.get_downloads_with_runtime(),
        "playlists": db.get_all_playlists(),
    })


@app.get("/api/download/<int:download_id>/file")
def download_file(download_id):
    dl = db.get_download(download_id)
    if not dl or dl["status"] != Status.COMPLETED:
        return jsonify({"error": "File not available"}), 404

    resolved = _resolve_file_path(dl)
    if not resolved:
        return jsonify({"error": "File not found on disk"}), 404

    mimetype = mimetypes.guess_type(resolved)[0] or "video/mp4"
    return send_file(resolved, as_attachment=False, mimetype=mimetype)


def _resolve_file_path(dl):
    file_path = dl["file_path"]
    if "%(ext)s" not in file_path and os.path.exists(file_path):
        return file_path
    base_path = file_path.replace(".%(ext)s", "")
    for ext in [".mp4", ".webm", ".mkv"]:
        candidate = base_path + ext
        if os.path.exists(candidate):
            return candidate
    return None


@app.post("/api/download/<int:download_id>/open")
def open_download(download_id):
    dl = db.get_download(download_id)
    if not dl or dl["status"] != Status.COMPLETED:
        return jsonify({"error": "File not available"}), 404

    resolved = _resolve_file_path(dl)
    if not resolved:
        return jsonify({"error": "File not found on disk"}), 404

    try:
        open_in_default_player(resolved)
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    
@app.get("/api/thumbnail/<filename>")
def get_thumbnail(filename: str):
    return send_from_directory(THUMB_DIR, filename)


# ── SSE Stream ───────────────────────────────────────────────────────────────

@app.get("/api/downloads/stream")
def downloads_stream():
    def generate():
        q = manager.subscribe()
        try:
            init_data = {
                "downloads": manager.get_downloads_with_runtime(),
                "playlists": db.get_all_playlists(),
            }
            initial = json.dumps(init_data, default=str)
            yield f"event: init\ndata: {initial}\n\n"

            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except Exception:
                    yield ": heartbeat\n\n"
        finally:
            manager.unsubscribe(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, threaded=True, host="0.0.0.0", port=5000)
