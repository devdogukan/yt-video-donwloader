# YT Video Downloader

A web-based application for downloading YouTube videos. Features pause/resume support, quality selection, and real-time progress tracking.

## Features

- **Video info** — Paste a URL to see title, duration, and available formats
- **Quality selection** — Choose from 360p, 720p, 1080p, and other formats
- **Pause / Resume** — Pause and resume downloads at any time
- **Real-time progress** — Speed, ETA, and percentage display
- **Playback** — Open in default player or play in browser
- **Delete confirmation** — Modal for safe deletion

## Requirements

- Python 3.11+
- [ffmpeg](https://ffmpeg.org/) — Required for merging video and audio (720p, 1080p, etc.)

## Installation

[uv](https://docs.astral.sh/uv/) ile kurulum:

```bash
# install uv if not installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# clone the repository
git clone https://github.com/devdogukan/yt-video-donwloader
cd yt-video-donwloader

# create virtual environment and install dependencies
uv sync
```

## Running

```bash
uv run python app.py
```

The app runs at `http://localhost:5000` by default.

## Usage

1. Open `http://localhost:5000` in your browser
2. Paste a YouTube video URL (or it will be detected on paste)
3. Click "Fetch Info" to load video details
4. Select quality and click "Download" to start
5. Play downloaded videos with Play or Browser buttons

## Project Structure

```
yt-video-donwloader/
├── app.py           # Flask app and API
├── downloader.py    # Download management with yt-dlp
├── database.py      # SQLite database
├── players.py       # Default player (WSL support)
├── static/
│   ├── css/style.css
│   └── js/app.js
├── templates/
│   └── index.html
└── downloads/       # Downloaded files
```

## Tech Stack

- **Backend:** Flask, yt-dlp
- **Frontend:** Vanilla JS, CSS
- **Database:** SQLite
- **Real-time:** Server-Sent Events (SSE)

## License

MIT
