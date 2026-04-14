let currentVideoInfo = null;
let downloads = {};
let playlists = {};
let evtSource = null;
let deleteModalTargetId = null;
let deletePlaylistTargetId = null;
let currentPlaylistData = null;
let expandedPlaylists = new Set();

const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(
    navigator.userAgent
);

// ── Helpers ─────────────────────────────────────────────────────────────────

function formatBytes(bytes) {
    if (!bytes || bytes === 0) return "—";
    const units = ["B", "KB", "MB", "GB"];
    let i = 0;
    let val = bytes;
    while (val >= 1024 && i < units.length - 1) {
        val /= 1024;
        i++;
    }
    return `${val.toFixed(1)} ${units[i]}`;
}

function formatDuration(seconds) {
    if (!seconds) return "";
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;
    if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
    return `${m}:${String(s).padStart(2, "0")}`;
}

function statusLabel(status) {
    const labels = {
        pending: "Pending",
        queued: "Queued",
        downloading: "Downloading",
        merging: "Merging",
        paused: "Paused",
        completed: "Completed",
        error: '<span class="material-symbols-rounded" style="font-size:12px">error</span> Error',
    };
    return labels[status] || status;
}

function $(sel) {
    return document.querySelector(sel);
}

// ── Delete Modal (created in JS so it always exists) ──────────────────────────

function createDeleteModal() {
    const modal = document.createElement("div");
    modal.id = "deleteModal";
    modal.className = "modal-overlay hidden";
    modal.innerHTML = `
        <div class="modal">
            <div class="modal-header">
                <span class="material-symbols-rounded modal-icon danger">delete</span>
                <h3 class="modal-title">Delete Download</h3>
            </div>
            <p class="modal-body">Are you sure you want to delete this download? This action cannot be undone.</p>
            <div class="modal-actions">
                <button type="button" class="modal-btn modal-btn-cancel">Cancel</button>
                <button type="button" class="modal-btn modal-btn-danger">
                    <span class="material-symbols-rounded">delete</span>
                    Delete
                </button>
            </div>
        </div>
    `;
    modal.querySelector(".modal-btn-cancel").addEventListener("click", closeDeleteModal);
    modal.querySelector(".modal-btn-danger").addEventListener("click", confirmDelete);
    modal.addEventListener("click", (e) => {
        if (e.target === modal) closeDeleteModal();
    });
    document.body.appendChild(modal);
}

// ── URL Input ───────────────────────────────────────────────────────────────

const urlInput = $("#urlInput");
urlInput.addEventListener("paste", () => {
    setTimeout(() => fetchVideoInfo(), 100);
});
urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") fetchVideoInfo();
});

const localVideoInput = $("#localVideoInput");
const localUploadCard = $("#localUploadCard");

localUploadCard.addEventListener("click", () => {
    if (localUploadCard.disabled) return;
    localVideoInput.click();
});

localVideoInput.addEventListener("change", () => {
    const file = localVideoInput.files && localVideoInput.files[0];
    if (file) {
        uploadLocalVideo(file);
    }
    localVideoInput.value = "";
});

async function uploadLocalVideo(file) {
    const card = localUploadCard;
    const loader = card.querySelector(".upload-card-loader");
    const titleEl = card.querySelector(".upload-card-title");
    const hintEl = card.querySelector(".upload-card-hint");
    const errorEl = $("#localUploadError");
    const prevTitle = titleEl.textContent;
    const prevHint = hintEl.textContent;

    card.disabled = true;
    loader.classList.remove("hidden");
    titleEl.textContent = "Uploading…";
    hintEl.textContent = file.name || "";
    errorEl.classList.add("hidden");

    const form = new FormData();
    form.append("file", file, file.name);

    try {
        const res = await fetch("/api/downloads/local", {
            method: "POST",
            body: form,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Upload failed");
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        card.disabled = false;
        loader.classList.add("hidden");
        titleEl.textContent = prevTitle;
        hintEl.textContent = prevHint;
    }
}

async function fetchVideoInfo() {
    const url = urlInput.value.trim();
    if (!url) return;

    const btn = $("#fetchBtn");
    const btnText = btn.querySelector(".btn-text");
    const btnLoader = btn.querySelector(".btn-loader");
    const errorEl = $("#urlError");

    btn.disabled = true;
    btnText.textContent = "Loading...";
    btnLoader.classList.remove("hidden");
    errorEl.classList.add("hidden");
    $("#videoInfo").classList.add("hidden");

    try {
        const res = await fetch("/api/video/info", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url }),
        });
        const data = await res.json();

        if (!res.ok) throw new Error(data.error || "Could not fetch video info");

        if (data.type === "playlist") {
            openPlaylistModal(data, url);
        } else {
            currentVideoInfo = data;
            currentVideoInfo._url = url;
            renderVideoInfo(data);
        }
    } catch (err) {
        errorEl.textContent = err.message;
        errorEl.classList.remove("hidden");
    } finally {
        btn.disabled = false;
        btnText.textContent = "Fetch Info";
        btnLoader.classList.add("hidden");
    }
}

function renderVideoInfo(info) {
    $("#videoThumb").src = info.thumbnail;
    $("#videoTitle").textContent = info.title;
    $("#videoDurationText").textContent = formatDuration(info.duration);

    const select = $("#qualitySelect");
    select.innerHTML = "";
    info.formats.forEach((f) => {
        const opt = document.createElement("option");
        opt.value = f.format_id;
        opt.textContent = f.label;
        opt.dataset.filesize = f.filesize || 0;
        select.appendChild(opt);
    });
    select.addEventListener("change", updateEstimatedSize);
    updateEstimatedSize();

    $("#videoInfo").classList.remove("hidden");
}

function updateEstimatedSize() {
    const select = $("#qualitySelect");
    const opt = select.options[select.selectedIndex];
    const size = parseInt(opt?.dataset.filesize || 0);
    $("#estimatedSize").textContent = size > 0 ? `~${formatBytes(size)}` : "";
}

// ── Start Download ──────────────────────────────────────────────────────────

async function startDownload() {
    if (!currentVideoInfo) return;

    const select = $("#qualitySelect");
    const formatId = select.value;
    const qualityLabel = select.options[select.selectedIndex].textContent;

    try {
        const res = await fetch("/api/download/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                url: currentVideoInfo._url,
                format_id: formatId,
                quality_label: qualityLabel,
                video_info: currentVideoInfo,
                concurrent_fragments: parseInt($("#concurrentSelect").value),
                queued: $("#queueCheckbox").checked,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);

        $("#videoInfo").classList.add("hidden");
        urlInput.value = "";
        currentVideoInfo = null;
    } catch (err) {
        alert("Download failed: " + err.message);
    }
}

// ── Pause / Resume / Delete / Play ──────────────────────────────────────────

async function pauseDownload(id) {
    await fetch(`/api/download/${id}/pause`, { method: "POST" });
}

async function resumeDownload(id) {
    const cb = document.querySelector(`.download-item[data-id="${id}"] .dl-queue-toggle`);
    const queued = cb ? cb.checked : false;
    await fetch(`/api/download/${id}/resume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ queued }),
    });
}

function deleteDownload(id) {
    deleteModalTargetId = id;
    const modal = $("#deleteModal");
    modal.classList.remove("hidden");
    document.addEventListener("keydown", handleModalEscape);
}

function closeDeleteModal() {
    deleteModalTargetId = null;
    deletePlaylistTargetId = null;
    const modal = $("#deleteModal");
    modal.classList.add("hidden");
    modal.querySelector(".modal-title").textContent = "Delete Download";
    modal.querySelector(".modal-body").textContent =
        "Are you sure you want to delete this download? This action cannot be undone.";
    document.removeEventListener("keydown", handleModalEscape);
}

function handleModalEscape(e) {
    if (e.key === "Escape") closeDeleteModal();
}

async function confirmDelete() {
    if (deletePlaylistTargetId) {
        const id = deletePlaylistTargetId;
        closeDeleteModal();
        await fetch(`/api/playlist/${id}`, { method: "DELETE" });
        return;
    }
    if (!deleteModalTargetId) return;
    const id = deleteModalTargetId;
    closeDeleteModal();
    await fetch(`/api/download/${id}`, { method: "DELETE" });
}

async function openInPlayer(id) {
    try {
        const res = await fetch(`/api/download/${id}/open`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error);
    } catch (err) {
        alert("Could not open player: " + err.message);
    }
}

function openInBrowser(id) {
    window.open(`/api/download/${id}/file`, "_blank");
}

// ── Playlist Modal ──────────────────────────────────────────────────────────

function openPlaylistModal(data, sourceUrl) {
    currentPlaylistData = data;
    currentPlaylistData._sourceUrl = sourceUrl;

    $("#playlistModalTitle").textContent = data.playlist_title || "Playlist";

    const qualitySelect = $("#plQualitySelect");
    qualitySelect.innerHTML = "";

    const allHeights = new Set();
    data.entries.forEach((e) => {
        e.formats.forEach((f) => { if (f.height > 0) allHeights.add(f.height); });
    });
    const sortedHeights = [...allHeights].sort((a, b) => b - a);
    sortedHeights.forEach((h) => {
        const opt = document.createElement("option");
        opt.value = h;
        opt.textContent = h >= 2160 ? `${h}p (4K+)` : `${h}p`;
        qualitySelect.appendChild(opt);
    });
    const audioOpt = document.createElement("option");
    audioOpt.value = "audio";
    audioOpt.textContent = "Audio Only";
    qualitySelect.appendChild(audioOpt);

    if (qualitySelect.querySelector('option[value="1080"]')) {
        qualitySelect.value = "1080";
    }

    renderPlaylistEntries();
    qualitySelect.onchange = renderPlaylistEntries;

    $("#plSelectAll").checked = true;
    updatePlaylistSelectionCount();

    const modal = $("#playlistModal");
    modal.classList.remove("hidden");
    document.addEventListener("keydown", handlePlaylistModalEscape);
}

function closePlaylistModal() {
    currentPlaylistData = null;
    $("#playlistModal").classList.add("hidden");
    document.removeEventListener("keydown", handlePlaylistModalEscape);
}

function handlePlaylistModalEscape(e) {
    if (e.key === "Escape") closePlaylistModal();
}

function renderPlaylistEntries() {
    if (!currentPlaylistData) return;
    const list = $("#playlistVideoList");
    const selectedQuality = $("#plQualitySelect").value;

    list.innerHTML = currentPlaylistData.entries.map((entry, idx) => {
        const matchedFormat = getFormatForQuality(entry.formats, selectedQuality);
        const sizeText = matchedFormat && matchedFormat.filesize > 0
            ? formatBytes(matchedFormat.filesize) : "";

        return `
            <label class="playlist-video-item" data-idx="${idx}">
                <input type="checkbox" class="pl-video-cb" data-idx="${idx}" checked>
                <div class="pl-video-thumb">
                    <img src="${escapeHtml(entry.thumbnail)}" alt="" onerror="this.style.display='none'">
                    ${entry.duration ? `<span class="pl-video-duration">${formatDuration(entry.duration)}</span>` : ""}
                </div>
                <div class="pl-video-info">
                    <span class="pl-video-title">${escapeHtml(entry.title || "Untitled")}</span>
                    <span class="pl-video-meta">${sizeText}</span>
                </div>
            </label>
        `;
    }).join("");

    list.querySelectorAll(".pl-video-cb").forEach((cb) => {
        cb.addEventListener("change", updatePlaylistSelectionCount);
    });
    updatePlaylistSelectionCount();
}

function getFormatForQuality(formats, quality) {
    if (quality === "audio") {
        return formats.find((f) => f.height === 0) || null;
    }
    const h = parseInt(quality);
    let best = null;
    for (const f of formats) {
        if (f.height === h) return f;
        if (f.height > 0 && f.height <= h) {
            if (!best || f.height > best.height) best = f;
        }
    }
    return best || formats.find((f) => f.height > 0) || formats[0];
}

function updatePlaylistSelectionCount() {
    const checkboxes = document.querySelectorAll(".pl-video-cb");
    const checked = document.querySelectorAll(".pl-video-cb:checked");
    const total = checkboxes.length;
    const selected = checked.length;

    $("#plSelectedCount").textContent = `${selected}/${total}`;
    $("#playlistDownloadBtnText").textContent = `Download Selected (${selected})`;
    $("#playlistDownloadBtn").disabled = selected === 0;

    const selectAll = $("#plSelectAll");
    selectAll.checked = selected === total;
    selectAll.indeterminate = selected > 0 && selected < total;

    const selectedQuality = $("#plQualitySelect").value;
    let totalBytes = 0;
    checked.forEach((cb) => {
        const idx = parseInt(cb.dataset.idx);
        const entry = currentPlaylistData?.entries[idx];
        if (!entry) return;
        const fmt = getFormatForQuality(entry.formats, selectedQuality);
        if (fmt && fmt.filesize > 0) totalBytes += fmt.filesize;
    });
    const sizeEl = $("#plTotalSize");
    if (sizeEl) {
        sizeEl.textContent = totalBytes > 0 ? `~${formatBytes(totalBytes)}` : "";
    }
}

function handleSelectAll() {
    const checked = $("#plSelectAll").checked;
    document.querySelectorAll(".pl-video-cb").forEach((cb) => { cb.checked = checked; });
    updatePlaylistSelectionCount();
}

async function downloadSelectedPlaylistVideos() {
    if (!currentPlaylistData) return;

    const selectedQuality = $("#plQualitySelect").value;
    const concurrent = parseInt($("#plConcurrentSelect").value);
    const checkedBoxes = document.querySelectorAll(".pl-video-cb:checked");

    if (checkedBoxes.length === 0) return;

    const items = [];
    checkedBoxes.forEach((cb) => {
        const idx = parseInt(cb.dataset.idx);
        const entry = currentPlaylistData.entries[idx];
        if (!entry) return;
        const fmt = getFormatForQuality(entry.formats, selectedQuality);
        if (!fmt) return;
        items.push({
            url: entry.url,
            video_info: {
                video_id: entry.video_id,
                title: entry.title,
                thumbnail: entry.thumbnail,
                duration: entry.duration,
                formats: entry.formats,
            },
            format_id: fmt.format_id,
            quality_label: fmt.label,
        });
    });

    const btn = $("#playlistDownloadBtn");
    const loader = $("#playlistDownloadLoader");
    btn.disabled = true;
    loader.classList.remove("hidden");

    try {
        const res = await fetch("/api/playlist/download", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                playlist_info: {
                    playlist_id: currentPlaylistData.playlist_id,
                    playlist_title: currentPlaylistData.playlist_title,
                    playlist_thumbnail: currentPlaylistData.playlist_thumbnail,
                },
                downloads: items,
                concurrent_fragments: concurrent,
            }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to start downloads");

        closePlaylistModal();
        urlInput.value = "";
    } catch (err) {
        alert("Playlist download failed: " + err.message);
    } finally {
        btn.disabled = false;
        loader.classList.add("hidden");
    }
}

$("#playlistModalClose").addEventListener("click", closePlaylistModal);
$("#playlistModalCancel").addEventListener("click", closePlaylistModal);
$("#playlistDownloadBtn").addEventListener("click", downloadSelectedPlaylistVideos);
$("#plSelectAll").addEventListener("change", handleSelectAll);
$("#playlistModal").addEventListener("click", (e) => {
    if (e.target === $("#playlistModal")) closePlaylistModal();
});

// ── Delete Playlist Modal ───────────────────────────────────────────────────

function deletePlaylist(playlistId) {
    deletePlaylistTargetId = playlistId;
    deleteModalTargetId = null;
    const modal = $("#deleteModal");
    modal.querySelector(".modal-title").textContent = "Delete Playlist";
    modal.querySelector(".modal-body").textContent =
        "Are you sure you want to delete this playlist and all its downloads? This action cannot be undone.";
    modal.classList.remove("hidden");
    document.addEventListener("keydown", handleModalEscape);
}

// ── Render Downloads List ───────────────────────────────────────────────────

function renderDownloads() {
    const list = $("#downloadsList");
    const keys = Object.keys(downloads);

    if (keys.length === 0) {
        list.innerHTML = '<p class="empty-state">No downloads yet.</p>';
        return;
    }

    const playlistGroups = {};
    const standalone = [];

    keys.forEach((k) => {
        const dl = downloads[k];
        if (dl.playlist_id) {
            if (!playlistGroups[dl.playlist_id]) playlistGroups[dl.playlist_id] = [];
            playlistGroups[dl.playlist_id].push(dl);
        } else {
            standalone.push(dl);
        }
    });

    let html = "";

    const playlistIds = Object.keys(playlistGroups).sort((a, b) => {
        const plA = playlists[a];
        const plB = playlists[b];
        if (plA && plB) return new Date(plB.created_at) - new Date(plA.created_at);
        return b - a;
    });

    playlistIds.forEach((plId) => {
        const group = playlistGroups[plId];
        const pl = playlists[plId];
        const title = pl ? pl.title : "Playlist";
        const thumb = pl ? pl.thumbnail : null;
        const isExpanded = expandedPlaylists.has(plId);

        const completed = group.filter((d) => d.status === "completed").length;
        const total = group.length;
        const aggregateProgress = total > 0
            ? group.reduce((sum, d) => sum + (d.progress || 0), 0) / total
            : 0;
        const allDone = completed === total;
        const progressClass = allDone ? "completed" : "";

        const sortedGroup = [...group].sort(
            (a, b) => new Date(a.created_at) - new Date(b.created_at)
        );

        html += `
            <div class="playlist-folder ${isExpanded ? "expanded" : "collapsed"}" data-playlist-id="${plId}">
                <div class="playlist-folder-header" onclick="togglePlaylistFolder('${plId}')">
                    <div class="playlist-folder-thumb">
                        ${thumb ? `<img src="/api/thumbnail/${thumb}" alt="" onerror="this.style.display='none'">` : ""}
                        <span class="material-symbols-rounded playlist-folder-icon">folder</span>
                    </div>
                    <div class="playlist-folder-info">
                        <span class="playlist-folder-title">${escapeHtml(title)}</span>
                        <span class="playlist-folder-meta">${completed}/${total} completed</span>
                        <div class="progress-bar" style="margin-top:4px">
                            <div class="progress-fill ${progressClass}" style="width:${aggregateProgress}%"></div>
                        </div>
                    </div>
                    <div class="playlist-folder-actions">
                        <button class="danger" onclick="event.stopPropagation(); deletePlaylist(${plId})" title="Delete playlist">
                            <span class="material-symbols-rounded">delete</span>
                        </button>
                        <span class="material-symbols-rounded playlist-folder-chevron">
                            ${isExpanded ? "expand_less" : "expand_more"}
                        </span>
                    </div>
                </div>
                ${isExpanded ? `
                    <div class="playlist-folder-body">
                        ${sortedGroup.map((dl) => buildDownloadItem(dl)).join("")}
                    </div>
                ` : ""}
            </div>
        `;
    });

    const sortedStandalone = standalone.sort(
        (a, b) => new Date(b.created_at) - new Date(a.created_at)
    );
    html += sortedStandalone.map((dl) => buildDownloadItem(dl)).join("");

    if (!html) {
        html = '<p class="empty-state">No downloads yet.</p>';
    }

    list.innerHTML = html;
}

function togglePlaylistFolder(plId) {
    if (expandedPlaylists.has(plId)) {
        expandedPlaylists.delete(plId);
    } else {
        expandedPlaylists.add(plId);
    }
    renderDownloads();
}

function buildDownloadItem(dl) {
    const progress = dl.progress || 0;
    const progressClass = ["completed", "paused", "merging", "error", "queued"].includes(dl.status) ? dl.status : "";

    let actionButtons = "";
    if (dl.status === "queued") {
        actionButtons = `<button onclick="pauseDownload(${dl.id})" title="Pause"><span class="material-symbols-rounded">pause</span> Pause</button>`;
    } else if (dl.status === "downloading") {
        actionButtons = `<button onclick="pauseDownload(${dl.id})" title="Pause"><span class="material-symbols-rounded">pause</span> Pause</button>`;
    } else if (dl.status === "paused") {
        const checked = dl.is_queued ? "checked" : "";
        actionButtons = `<label class="dl-queue-label"><input type="checkbox" class="dl-queue-toggle" ${checked}><span class="material-symbols-rounded" style="font-size:16px">queue</span> Queue</label>`;
        actionButtons += `<button onclick="resumeDownload(${dl.id})" title="Resume"><span class="material-symbols-rounded">play_arrow</span> Resume</button>`;
    } else if (dl.status === "error") {
        const checked = dl.is_queued ? "checked" : "";
        actionButtons = `<label class="dl-queue-label"><input type="checkbox" class="dl-queue-toggle" ${checked}><span class="material-symbols-rounded" style="font-size:16px">queue</span> Queue</label>`;
        actionButtons += `<button onclick="resumeDownload(${dl.id})" title="Retry"><span class="material-symbols-rounded">refresh</span> Retry</button>`;
    } else if (dl.status === "completed") {
        if (isMobile) {
            actionButtons = `<button onclick="openInBrowser(${dl.id})" title="Play in browser"><span class="material-symbols-rounded">play_arrow</span> Play</button>`;
        } else {
            actionButtons = `<button onclick="openInPlayer(${dl.id})" title="Open in default player"><span class="material-symbols-rounded">play_arrow</span> Play</button>`;
            actionButtons += `<button onclick="openInBrowser(${dl.id})" title="Play in browser"><span class="material-symbols-rounded">open_in_new</span> Browser</button>`;
        }
    }
    actionButtons += `<button class="danger" onclick="deleteDownload(${dl.id})" title="Delete"><span class="material-symbols-rounded">delete</span> Delete</button>`;

    let statusLine = "";
    if (dl.status === "queued") {
        statusLine = "Waiting in queue...";
    } else if (dl.status === "downloading") {
        statusLine = `${dl.speed || ""} ${dl.eta ? "• " + dl.eta : ""}`;
    } else if (dl.status === "merging") {
        statusLine = "Merging video and audio...";
    } else if (dl.status === "error") {
        statusLine = `<span class="error-line"><span class="material-symbols-rounded">warning</span> ${escapeHtml(dl.error_message || "Unknown error")}</span>`;
    }

    return `
        <div class="download-item" data-id="${dl.id}">
            <div class="dl-thumb-wrap">
                <img src="/api/thumbnail/${dl.thumbnail}" alt="" onerror="this.style.display='none'">
            </div>
            <div class="dl-body">
                <div class="dl-header">
                    <span class="dl-title">${escapeHtml(dl.title || "Unknown Video")}</span>
                    <span class="status-badge status-${dl.status}">${statusLabel(dl.status)}</span>
                </div>
                <div class="dl-meta">
                    <span>${dl.quality_label || ""}</span>
                    <span>${dl.filesize ? formatBytes(dl.filesize) : ""}</span>
                </div>
                ${dl.status !== "completed" ? `
                <div class="progress-wrapper">
                    <div class="progress-bar">
                        <div class="progress-fill ${progressClass}" style="width: ${progress}%"></div>
                    </div>
                    <div class="progress-stats">
                        <span>${progress.toFixed(1)}%</span>
                        <span>${statusLine}</span>
                    </div>
                </div>
                ` : ""}
            </div>
            <hr class="dl-actions-divider">
            <div class="dl-actions">
                ${actionButtons}
            </div>
        </div>
    `;
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// ── SSE Connection ──────────────────────────────────────────────────────────

function connectSSE() {
    if (evtSource) {
        evtSource.close();
    }

    evtSource = new EventSource("/api/downloads/stream");

    evtSource.addEventListener("init", (e) => {
        const data = JSON.parse(e.data);
        downloads = {};
        playlists = {};
        (data.downloads || []).forEach((dl) => { downloads[dl.id] = dl; });
        (data.playlists || []).forEach((pl) => { playlists[pl.id] = pl; });
        renderDownloads();
    });

    evtSource.onmessage = (e) => {
        const event = JSON.parse(e.data);
        handleSSEEvent(event);
    };

    evtSource.onerror = () => {
        refreshDownloads();
    };
}

function handleSSEEvent(event) {
    switch (event.type) {
        case "progress":
            if (downloads[event.id]) {
                downloads[event.id].downloaded_bytes = event.downloaded_bytes;
                downloads[event.id].progress = event.progress;
                downloads[event.id].speed = event.speed;
                downloads[event.id].eta = event.eta;
                downloads[event.id].status = event.status;
                updateDownloadItemInPlace(event.id);
            }
            break;

        case "status":
            if (downloads[event.id]) {
                downloads[event.id].status = event.status;
                if (event.progress !== undefined) {
                    downloads[event.id].progress = event.progress;
                }
                if (event.error_message) {
                    downloads[event.id].error_message = event.error_message;
                }
                if (event.is_queued !== undefined) {
                    downloads[event.id].is_queued = event.is_queued;
                }
                renderDownloads();
            }
            break;

        case "new":
            if (event.download) {
                downloads[event.download.id] = event.download;
                renderDownloads();
            }
            break;

        case "deleted":
            delete downloads[event.id];
            renderDownloads();
            break;

        case "playlist_deleted":
            delete playlists[event.playlist_id];
            Object.keys(downloads).forEach((k) => {
                if (downloads[k].playlist_id === event.playlist_id) {
                    delete downloads[k];
                }
            });
            expandedPlaylists.delete(String(event.playlist_id));
            renderDownloads();
            break;
    }
}

function updateDownloadItemInPlace(id) {
    const dl = downloads[id];
    if (!dl) return;

    const el = document.querySelector(`.download-item[data-id="${id}"]`);
    if (!el) {
        renderDownloads();
        return;
    }

    const fill = el.querySelector(".progress-fill");
    if (fill) {
        fill.style.width = `${dl.progress || 0}%`;
    }

    const stats = el.querySelector(".progress-stats");
    if (stats) {
        const statusLine = `${dl.speed || ""} ${dl.eta ? "• " + dl.eta : ""}`;
        stats.innerHTML = `<span>${(dl.progress || 0).toFixed(1)}%</span><span>${statusLine}</span>`;
    }
}

// ── Refresh ─────────────────────────────────────────────────────────────────

async function refreshDownloads() {
    const btn = $(".refresh-btn");
    if (btn) btn.classList.add("spinning");
    try {
        const res = await fetch("/api/downloads");
        const data = await res.json();
        downloads = {};
        playlists = {};
        (data.downloads || []).forEach((dl) => { downloads[dl.id] = dl; });
        (data.playlists || []).forEach((pl) => { playlists[pl.id] = pl; });
        renderDownloads();
    } catch (_) {
        // ignore
    } finally {
        if (btn) setTimeout(() => btn.classList.remove("spinning"), 400);
    }
}

// ── Init ────────────────────────────────────────────────────────────────────

createDeleteModal();
connectSSE();
