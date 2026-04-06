import json
import os

import mimetypes

from flask import Flask, render_template, request, jsonify, Response, send_file
from werkzeug.utils import secure_filename

import database as db
from database import Status
from downloader import DownloadManager, DOWNLOADS_DIR
from players import open_in_default_player

app = Flask(__name__)
manager = DownloadManager()

db.init_db()
db.mark_interrupted_as_paused()


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


def _unique_path_in_downloads_dir(basename):
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

    mime = (f.mimetype or "").split(";")[0].strip()
    base = secure_filename(f.filename)
    if not base or base.startswith("."):
        guess = mimetypes.guess_extension(mime or "") or ".mp4"
        if guess == ".jpe":
            guess = ".jpeg"
        base = f"video{guess}"
    elif "." not in base:
        guess = mimetypes.guess_extension(mime or "") or ".mp4"
        if guess == ".jpe":
            guess = ".jpeg"
        base = f"{base}{guess}"

    dest = _unique_path_in_downloads_dir(base)
    try:
        f.save(dest)
    except OSError as e:
        return jsonify({"error": str(e)}), 500

    resolved = os.path.abspath(dest)
    title = os.path.basename(resolved)
    try:
        size = os.path.getsize(resolved)
    except OSError:
        size = None

    download_id = db.create_download(
        video_id=None,
        url=None,
        title=title,
        thumbnail=None,
        duration=None,
        format_id=None,
        quality_label=None,
        filesize=size,
        file_path=resolved,
        status=Status.COMPLETED,
        concurrent_fragments=1,
        is_queued=False,
    )
    db.update_progress(download_id, size or 0, 100.0, "", "")
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
    return jsonify(db.get_all_downloads())


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


# ── SSE Stream ───────────────────────────────────────────────────────────────

@app.get("/api/downloads/stream")
def downloads_stream():
    def generate():
        q = manager.subscribe()
        try:
            initial = json.dumps(db.get_all_downloads(), default=str)
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
