import json

from flask import Flask, render_template, request, jsonify, Response, send_file, send_from_directory

from config import THUMB_DIR
from models import NotFoundError, ConflictError, ValidationError
from service import VideoService
from utils import is_ffmpeg_installed

app = Flask(__name__)
svc = VideoService()
svc.init()


# ── Pages ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ── REST API ─────────────────────────────────────────────────────────────────

@app.get("/api/system/ffmpeg-status")
def ffmpeg_status():
    """Check if ffmpeg is installed on the system."""
    return jsonify({"installed": is_ffmpeg_installed()})


@app.post("/api/video/info")
def video_info():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    try:
        info = svc.get_video_info(url)
        return jsonify(info)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/downloads/local")
def add_local_download():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "file is required"}), 400

    try:
        dest = svc.save_upload(f.filename, f.mimetype, f.save)
    except OSError as e:
        return jsonify({"error": str(e)}), 500

    result = svc.add_local_download(dest, title=f.filename)
    return jsonify(result)


@app.post("/api/download/start")
def download_start():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    format_id = data.get("format_id", "")
    quality_label = data.get("quality_label", "")
    video_info_data = data.get("video_info", {})
    concurrent_fragments = max(1, min(16, int(data.get("concurrent_fragments", 1))))
    queued = bool(data.get("queued", False))

    try:
        result = svc.start_download(
            url, video_info_data, format_id, quality_label,
            concurrent_fragments, queued)
        return jsonify(result)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except ConflictError as e:
        return jsonify({"error": str(e)}), 409


@app.post("/api/playlist/create")
def playlist_create():
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    try:
        pl = svc.create_custom_playlist(title)
        return jsonify(pl)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/playlist/<int:playlist_id>/rename")
def playlist_rename(playlist_id):
    data = request.get_json(silent=True) or {}
    title = data.get("title", "").strip()
    try:
        pl = svc.rename_playlist(playlist_id, title)
        return jsonify(pl)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/playlist/<int:playlist_id>/add")
def playlist_add_download(playlist_id):
    data = request.get_json(silent=True) or {}
    download_id = data.get("download_id")
    if not download_id:
        return jsonify({"error": "download_id is required"}), 400
    try:
        svc.add_to_playlist(playlist_id, int(download_id))
        return jsonify({"status": "added"})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ConflictError as e:
        return jsonify({"error": str(e)}), 409


@app.post("/api/playlist/<int:playlist_id>/thumbnail/upload")
def playlist_thumbnail_upload(playlist_id):
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "file is required"}), 400
    try:
        pl = svc.update_playlist_thumbnail_upload(
            playlist_id, f.filename, f.mimetype, f.save)
        return jsonify(pl)
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except OSError as e:
        return jsonify({"error": str(e)}), 500


@app.post("/api/playlist/<int:playlist_id>/thumbnail/pick")
def playlist_thumbnail_pick(playlist_id):
    data = request.get_json(silent=True) or {}
    download_id = data.get("download_id")
    if not download_id:
        return jsonify({"error": "download_id is required"}), 400
    try:
        pl = svc.set_playlist_thumbnail_from_video(playlist_id, int(download_id))
        return jsonify(pl)
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/playlist/<int:playlist_id>/remove")
def playlist_remove_download(playlist_id):
    data = request.get_json(silent=True) or {}
    download_id = data.get("download_id")
    if not download_id:
        return jsonify({"error": "download_id is required"}), 400
    try:
        svc.remove_from_playlist(int(download_id))
        return jsonify({"status": "removed"})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except ConflictError as e:
        return jsonify({"error": str(e)}), 409


@app.post("/api/playlist/download")
def playlist_download():
    data = request.get_json(silent=True) or {}
    playlist_info = data.get("playlist_info", {})
    items = data.get("downloads", [])
    concurrent_fragments = max(1, min(16, int(data.get("concurrent_fragments", 1))))

    try:
        result = svc.start_playlist_download(playlist_info, items, concurrent_fragments)
        return jsonify(result)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/playlist/<int:playlist_id>")
def playlist_delete(playlist_id):
    try:
        svc.delete_playlist(playlist_id)
        return jsonify({"status": "deleted"})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/playlist/<int:playlist_id>/pause")
def playlist_pause(playlist_id):
    try:
        count = svc.pause_playlist(playlist_id)
        return jsonify({"status": "paused", "count": count})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/playlist/<int:playlist_id>/resume")
def playlist_resume(playlist_id):
    try:
        count = svc.resume_playlist(playlist_id)
        return jsonify({"status": "resumed", "count": count})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/download/<int:download_id>/pause")
def download_pause(download_id):
    try:
        svc.pause_download(download_id)
        return jsonify({"status": "paused"})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/download/<int:download_id>/resume")
def download_resume(download_id):
    data = request.get_json(silent=True) or {}
    queued = bool(data.get("queued", False))
    try:
        status = svc.resume_download(download_id, queued=queued)
        return jsonify({"status": status})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/download/<int:download_id>")
def download_delete(download_id):
    try:
        svc.delete_download(download_id)
        return jsonify({"status": "deleted"})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.get("/api/downloads")
def downloads_list():
    return jsonify(svc.list_downloads())


@app.get("/api/download/<int:download_id>/file")
def download_file(download_id):
    try:
        resolved, mimetype = svc.get_download_file(download_id)
        return send_file(resolved, as_attachment=False, mimetype=mimetype)
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404


@app.post("/api/download/<int:download_id>/open")
def open_download(download_id):
    try:
        svc.open_in_player(download_id)
        return jsonify({"status": "ok"})
    except NotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/thumbnail/<filename>")
def get_thumbnail(filename: str):
    return send_from_directory(THUMB_DIR, filename)


# ── SSE Stream ───────────────────────────────────────────────────────────────

@app.get("/api/downloads/stream")
def downloads_stream():
    def generate():
        q = svc.subscribe()
        try:
            init_data = svc.list_downloads()
            initial = json.dumps(init_data, default=str)
            yield f"event: init\ndata: {initial}\n\n"

            while True:
                try:
                    data = q.get(timeout=15)
                    yield f"data: {data}\n\n"
                except Exception:
                    yield ": heartbeat\n\n"
        finally:
            svc.unsubscribe(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, threaded=True, host="0.0.0.0", port=5000)
