import os
import subprocess
import requests
import uuid

from config import THUMB_DIR


def generate_thumbnail_with_ffmpeg(video_path: str, output_path: str) -> bool:
    cmd = [
        "ffmpeg",
        "-y",
        "-ss", "00:00:05",
        "-i", video_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return os.path.exists(output_path)
    except Exception as e:
        print(f"[ERROR]: {video_path} -> {e}")
        return False


def download_thumbnail(url: str, output_path: str) -> bool:
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(output_path, "wb") as f:
                f.write(r.content)
            return True
    except Exception as e:
        print(f"[ERROR]: {output_path} -> {e}")
    return False


def get_or_create_thumbnail(thumbnail_url: str = None, video_path: str = None,
                            filename: str = None) -> str | None:
    output_filename = f"{filename if filename else uuid.uuid4()}.jpg"
    output_path = os.path.join(THUMB_DIR, output_filename)
    if os.path.exists(output_path):
        return output_path

    if thumbnail_url:
        if download_thumbnail(thumbnail_url, output_path):
            return output_filename

    if video_path:
        if generate_thumbnail_with_ffmpeg(video_path, output_path):
            return output_filename

    return None


def is_ffmpeg_installed() -> bool:
    """Check if ffmpeg is installed and available in PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def get_video_duration_seconds(video_path: str) -> int | None:
    """Return media duration in whole seconds using ffprobe, if available."""
    cmd = [
        "ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )
        raw = (result.stdout or "").strip()
        if not raw:
            return None

        duration = float(raw)
        if duration < 0:
            return None
        return int(round(duration))
    except (ValueError, OSError, subprocess.SubprocessError):
        return None
