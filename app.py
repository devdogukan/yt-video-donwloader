import json
import os

from flask import Flask, render_template, request, jsonify, Response, send_file

import database as db
from downloader import DownloadManager

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


@app.post("/api/download/start")
def download_start():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    format_id = data.get("format_id", "")
    quality_label = data.get("quality_label", "")
    video_info = data.get("video_info", {})

    if not url or not format_id or not video_info:
        return jsonify({"error": "url, format_id, and video_info are required"}), 400

    video_id = video_info.get("video_id", "")
    if video_id and db.has_active_download(video_id):
        return jsonify({"error": "This video is already in the download list."}), 409

    download_id = manager.start_download(url, video_info, format_id, quality_label)
    return jsonify({"id": download_id, "status": "downloading"})


@app.post("/api/download/<int:download_id>/pause")
def download_pause(download_id):
    ok = manager.pause_download(download_id)
    if ok:
        return jsonify({"status": "paused"})
    return jsonify({"error": "Download not active"}), 404


@app.post("/api/download/<int:download_id>/resume")
def download_resume(download_id):
    ok = manager.resume_download(download_id)
    if ok:
        return jsonify({"status": "downloading"})
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
    if not dl or dl["status"] != "completed":
        return jsonify({"error": "File not available"}), 404

    file_path = dl["file_path"]
    base_path = file_path.replace(".%(ext)s", "")
    for ext in [".mp4", ".webm", ".mkv"]:
        candidate = base_path + ext
        if os.path.exists(candidate):
            return send_file(candidate, as_attachment=False)

    return jsonify({"error": "File not found on disk"}), 404


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
    app.run(debug=False, threaded=True, port=5000)
