let currentVideoInfo = null;
let downloads = {};
let playlists = {};
let evtSource = null;
let deleteModalTargetId = null;
let deletePlaylistTargetId = null;
let currentPlaylistData = null;
let activePlaylistPageId = null;
let addToPlaylistTargetId = null;

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
    const files = localVideoInput.files;
    if (files && files.length > 0) {
        uploadLocalVideos([...files]);
    }
    localVideoInput.value = "";
});

async function uploadLocalVideos(files) {
    const card = localUploadCard;
    const loader = card.querySelector(".upload-card-loader");
    const titleEl = card.querySelector(".upload-card-title");
    const hintEl = card.querySelector(".upload-card-hint");
    const errorEl = $("#localUploadError");
    const prevTitle = titleEl.textContent;
    const prevHint = hintEl.textContent;

    card.disabled = true;
    loader.classList.remove("hidden");
    errorEl.classList.add("hidden");

    const total = files.length;
    const errors = [];

    for (let i = 0; i < total; i++) {
        const file = files[i];
        titleEl.textContent = total > 1 ? `Uploading ${i + 1}/${total}…` : "Uploading…";
        hintEl.textContent = file.name || "";

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
            errors.push(`${file.name}: ${err.message}`);
        }
    }

    card.disabled = false;
    loader.classList.add("hidden");
    titleEl.textContent = prevTitle;
    hintEl.textContent = prevHint;

    if (errors.length > 0) {
        errorEl.textContent = errors.join(" | ");
        errorEl.classList.remove("hidden");
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
        const wasOnPlaylistPage = activePlaylistPageId === String(id);
        closeDeleteModal();
        if (wasOnPlaylistPage) closePlaylistPage();
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
    const url = `/api/download/${id}/file`;
    if (isMobile) {
        window.location.href = url;
    } else {
        window.open(url, "_blank");
    }
}

// ── Playlist Pause / Resume ──────────────────────────────────────────────────

async function pausePlaylist(plId) {
    await fetch(`/api/playlist/${plId}/pause`, { method: "POST" });
}

async function resumePlaylist(plId) {
    await fetch(`/api/playlist/${plId}/resume`, { method: "POST" });
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

// ── Create Playlist Modal ────────────────────────────────────────────────────

function openCreatePlaylistModal() {
    const modal = $("#createPlaylistModal");
    const input = $("#newPlaylistNameInput");
    input.value = "";
    modal.classList.remove("hidden");
    setTimeout(() => input.focus(), 50);
    document.addEventListener("keydown", handleCreatePlaylistEscape);
}

function closeCreatePlaylistModal() {
    $("#createPlaylistModal").classList.add("hidden");
    document.removeEventListener("keydown", handleCreatePlaylistEscape);
}

function handleCreatePlaylistEscape(e) {
    if (e.key === "Escape") closeCreatePlaylistModal();
}

async function submitCreatePlaylist() {
    const input = $("#newPlaylistNameInput");
    const title = input.value.trim();
    if (!title) return;

    const btn = $("#createPlaylistSubmit");
    btn.disabled = true;

    try {
        const res = await fetch("/api/playlist/create", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to create playlist");
        closeCreatePlaylistModal();
    } catch (err) {
        alert("Could not create playlist: " + err.message);
    } finally {
        btn.disabled = false;
    }
}

$("#createPlaylistCancel").addEventListener("click", closeCreatePlaylistModal);
$("#createPlaylistSubmit").addEventListener("click", submitCreatePlaylist);
$("#newPlaylistNameInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") submitCreatePlaylist();
});
$("#createPlaylistModal").addEventListener("click", (e) => {
    if (e.target === $("#createPlaylistModal")) closeCreatePlaylistModal();
});

// ── Add to Playlist Modal ───────────────────────────────────────────────────

function openAddToPlaylistModal(downloadId) {
    addToPlaylistTargetId = downloadId;
    const list = $("#addToPlaylistList");
    const plIds = Object.keys(playlists);

    if (plIds.length === 0) {
        list.innerHTML = '<p class="empty-state" style="padding:24px 16px">No playlists yet. Create one first.</p>';
    } else {
        list.innerHTML = plIds.map((id) => {
            const pl = playlists[id];
            return `
                <button class="add-to-playlist-item" onclick="confirmAddToPlaylist(${id})">
                    <span class="material-symbols-rounded">playlist_play</span>
                    <span class="add-to-playlist-item-title">${escapeHtml(pl.title || "Playlist")}</span>
                    <span class="add-to-playlist-item-count">${pl.total_videos || 0} videos</span>
                </button>
            `;
        }).join("");
    }

    $("#addToPlaylistModal").classList.remove("hidden");
    document.addEventListener("keydown", handleAddToPlaylistEscape);
}

function closeAddToPlaylistModal() {
    addToPlaylistTargetId = null;
    $("#addToPlaylistModal").classList.add("hidden");
    document.removeEventListener("keydown", handleAddToPlaylistEscape);
}

function handleAddToPlaylistEscape(e) {
    if (e.key === "Escape") closeAddToPlaylistModal();
}

async function confirmAddToPlaylist(playlistId) {
    const downloadId = addToPlaylistTargetId;
    if (!downloadId) return;
    closeAddToPlaylistModal();

    try {
        const res = await fetch(`/api/playlist/${playlistId}/add`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ download_id: downloadId }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to add to playlist");
    } catch (err) {
        alert("Could not add to playlist: " + err.message);
    }
}

async function removeFromPlaylist(downloadId) {
    const dl = downloads[downloadId];
    if (!dl || !dl.playlist_id) return;

    try {
        const res = await fetch(`/api/playlist/${dl.playlist_id}/remove`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ download_id: downloadId }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to remove from playlist");
    } catch (err) {
        alert("Could not remove from playlist: " + err.message);
    }
}

$("#addToPlaylistCancel").addEventListener("click", closeAddToPlaylistModal);
$("#addToPlaylistModal").addEventListener("click", (e) => {
    if (e.target === $("#addToPlaylistModal")) closeAddToPlaylistModal();
});

// ── Thumbnail Picker Modal ──────────────────────────────────────────────────

function openThumbPickerModal() {
    const plId = activePlaylistPageId;
    if (!plId) return;

    const group = Object.values(downloads).filter(
        (d) => String(d.playlist_id) === String(plId) && d.thumbnail
    );

    const list = $("#thumbPickerVideoList");
    if (group.length === 0) {
        list.innerHTML = '<p class="empty-state" style="padding:12px 0">No videos with thumbnails.</p>';
    } else {
        list.innerHTML = group.map((dl) => `
            <button class="thumb-picker-item" onclick="pickVideoThumbnail(${dl.id})">
                <img src="/api/thumbnail/${dl.thumbnail}" alt="" onerror="this.style.display='none'">
                <span class="thumb-picker-item-title">${escapeHtml(dl.title || "Untitled")}</span>
            </button>
        `).join("");
    }

    $("#thumbPickerModal").classList.remove("hidden");
    document.addEventListener("keydown", handleThumbPickerEscape);
}

function closeThumbPickerModal() {
    $("#thumbPickerModal").classList.add("hidden");
    document.removeEventListener("keydown", handleThumbPickerEscape);
}

function handleThumbPickerEscape(e) {
    if (e.key === "Escape") closeThumbPickerModal();
}

async function pickVideoThumbnail(downloadId) {
    const plId = activePlaylistPageId;
    if (!plId) return;
    closeThumbPickerModal();

    try {
        const res = await fetch(`/api/playlist/${plId}/thumbnail/pick`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ download_id: downloadId }),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to update thumbnail");
    } catch (err) {
        alert("Could not update thumbnail: " + err.message);
    }
}

async function uploadPlaylistThumbnail(file) {
    const plId = activePlaylistPageId;
    if (!plId) return;
    closeThumbPickerModal();

    const form = new FormData();
    form.append("file", file, file.name);

    try {
        const res = await fetch(`/api/playlist/${plId}/thumbnail/upload`, {
            method: "POST",
            body: form,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Failed to upload thumbnail");
    } catch (err) {
        alert("Could not upload thumbnail: " + err.message);
    }
}

$("#thumbPickerCancel").addEventListener("click", closeThumbPickerModal);
$("#thumbPickerModal").addEventListener("click", (e) => {
    if (e.target === $("#thumbPickerModal")) closeThumbPickerModal();
});
$("#thumbPickerUploadBtn").addEventListener("click", () => {
    $("#playlistThumbInput").click();
});
$("#playlistThumbInput").addEventListener("change", () => {
    const file = $("#playlistThumbInput").files && $("#playlistThumbInput").files[0];
    if (file) uploadPlaylistThumbnail(file);
    $("#playlistThumbInput").value = "";
});

// ── Render Downloads List ───────────────────────────────────────────────────

function buildPlaylistPauseResumeBtn(plId, group) {
    const hasActive = group.some((d) =>
        ["downloading", "queued", "merging"].includes(d.status)
    );
    const hasResumable = group.some((d) =>
        ["paused", "error"].includes(d.status)
    );
    if (hasActive) {
        return `<button onclick="event.stopPropagation(); pausePlaylist(${plId})" title="Pause all">
            <span class="material-symbols-rounded">pause</span>
        </button>`;
    }
    if (hasResumable) {
        return `<button onclick="event.stopPropagation(); resumePlaylist(${plId})" title="Resume all">
            <span class="material-symbols-rounded">play_arrow</span>
        </button>`;
    }
    return "";
}

function buildPlaylistCard(plId, group) {
    const pl = playlists[plId];
    const title = pl ? pl.title : "Playlist";
    const thumb = pl ? pl.thumbnail : null;

    const completed = group.filter((d) => d.status === "completed").length;
    const total = group.length;
    const aggregateProgress = total > 0
        ? group.reduce((sum, d) => sum + (d.progress || 0), 0) / total
        : 0;
    const allDone = completed === total;
    const progressClass = allDone ? "completed" : "";

    let actionButtons = buildPlaylistPauseResumeBtn(plId, group);
    actionButtons += `<button onclick="event.stopPropagation(); openPlaylistPage('${plId}')" title="Open playlist">
        <span class="material-symbols-rounded">open_in_new</span> Open
    </button>`;
    actionButtons += `<button class="danger" onclick="event.stopPropagation(); deletePlaylist(${plId})" title="Delete playlist">
        <span class="material-symbols-rounded">delete</span> Delete
    </button>`;

    return `
        <div class="download-item playlist-card" data-playlist-id="${plId}" onclick="openPlaylistPage('${plId}')">
            <div class="dl-thumb-wrap">
                ${thumb ? `<img src="/api/thumbnail/${thumb}" alt="" onerror="this.style.display='none'">` : ""}
                <div class="playlist-card-badge">
                    <span class="material-symbols-rounded">playlist_play</span>
                    <span>${total} videos</span>
                </div>
            </div>
            <div class="dl-body">
                <div class="dl-header">
                    <span class="dl-title">${escapeHtml(title)}</span>
                    <span class="status-badge ${allDone ? "status-completed" : "status-downloading"}">${allDone ? "Completed" : `${completed}/${total}`}</span>
                </div>
                <div class="dl-meta">
                    <span>Playlist</span>
                    <span>${completed} of ${total} completed</span>
                </div>
                ${!allDone ? `
                <div class="progress-wrapper">
                    <div class="progress-bar">
                        <div class="progress-fill ${progressClass}" style="width: ${aggregateProgress}%"></div>
                    </div>
                    <div class="progress-stats">
                        <span>${aggregateProgress.toFixed(1)}%</span>
                        <span></span>
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

function renderDownloads() {
    const list = $("#downloadsList");
    const keys = Object.keys(downloads);

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

    Object.keys(playlists).forEach((plId) => {
        if (!playlistGroups[plId]) playlistGroups[plId] = [];
    });

    const hasContent = Object.keys(playlistGroups).length > 0 || standalone.length > 0;
    if (!hasContent) {
        list.innerHTML = '<p class="empty-state">No downloads yet.</p>';
        return;
    }

    const unified = [];

    Object.keys(playlistGroups).forEach((plId) => {
        const pl = playlists[plId];
        unified.push({
            kind: "playlist",
            plId,
            group: playlistGroups[plId],
            created_at: pl ? pl.created_at : null,
        });
    });

    standalone.forEach((dl) => {
        unified.push({
            kind: "download",
            dl,
            created_at: dl.created_at,
        });
    });

    unified.sort((a, b) => new Date(b.created_at) - new Date(a.created_at));

    let html = "";
    unified.forEach((item) => {
        if (item.kind === "playlist") {
            html += buildPlaylistCard(item.plId, item.group);
        } else {
            html += buildDownloadItem(item.dl);
        }
    });

    list.innerHTML = html;
}

function openPlaylistPage(plId) {
    activePlaylistPageId = String(plId);
    $(".container").classList.add("hidden");
    $("#playlistPage").classList.remove("hidden");
    renderPlaylistPage();
    history.pushState({ playlistPage: plId }, "");
    window.scrollTo(0, 0);
}

function closePlaylistPage() {
    activePlaylistPageId = null;
    $("#playlistPage").classList.add("hidden");
    $(".container").classList.remove("hidden");
}

function renderPlaylistPage() {
    const plId = activePlaylistPageId;
    if (!plId) return;

    const pl = playlists[plId];
    const group = Object.values(downloads).filter(
        (d) => String(d.playlist_id) === String(plId)
    );
    const sorted = [...group].sort(
        (a, b) => new Date(a.created_at) - new Date(b.created_at)
    );

    const title = pl ? pl.title : "Playlist";
    const thumb = pl ? pl.thumbnail : "";
    const completed = group.filter((d) => d.status === "completed").length;
    const total = group.length;
    const aggregateProgress = total > 0
        ? group.reduce((sum, d) => sum + (d.progress || 0), 0) / total
        : 0;
    const allDone = completed === total;

    const thumbImg = $("#playlistPageThumb");
    if (thumb) {
        thumbImg.src = `/api/thumbnail/${thumb}`;
        thumbImg.style.display = "";
    } else {
        thumbImg.src = "";
        thumbImg.style.display = "none";
    }

    $("#playlistPageTitle").textContent = title;
    $("#playlistPageMeta").textContent = `${completed}/${total} completed`;

    const progressEl = $("#playlistPageProgress");
    progressEl.style.width = `${aggregateProgress}%`;
    progressEl.className = `progress-fill ${allDone ? "completed" : ""}`;

    const actionsEl = $("#playlistPageActions");
    let actionsHtml = buildPlaylistPauseResumeBtn(plId, group);
    actionsHtml += `<button class="danger" onclick="deletePlaylist(${plId})" title="Delete playlist">
        <span class="material-symbols-rounded">delete</span> Delete
    </button>`;
    actionsEl.innerHTML = actionsHtml;

    const listEl = $("#playlistPageList");
    if (sorted.length === 0) {
        listEl.innerHTML = '<p class="empty-state">No videos in this playlist.</p>';
    } else {
        listEl.innerHTML = sorted.map((dl) => buildDownloadItem(dl, { inPlaylistPage: true })).join("");
    }
}

window.addEventListener("popstate", (e) => {
    if (activePlaylistPageId) {
        closePlaylistPage();
    }
});

function buildDownloadItem(dl, opts = {}) {
    const progress = dl.progress || 0;
    const progressClass = ["completed", "paused", "merging", "error", "queued"].includes(dl.status) ? dl.status : "";
    const inPlaylistPage = opts.inPlaylistPage || false;

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
    if (inPlaylistPage) {
        actionButtons += `<button onclick="removeFromPlaylist(${dl.id})" title="Remove from playlist"><span class="material-symbols-rounded">playlist_remove</span> Remove</button>`;
    } else if (!dl.playlist_id && Object.keys(playlists).length > 0) {
        actionButtons += `<button onclick="openAddToPlaylistModal(${dl.id})" title="Add to playlist"><span class="material-symbols-rounded">playlist_add</span></button>`;
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

function recalcPlaylistTotals() {
    const counts = {};
    Object.values(downloads).forEach((dl) => {
        if (dl.playlist_id) {
            counts[dl.playlist_id] = (counts[dl.playlist_id] || 0) + 1;
        }
    });
    Object.keys(playlists).forEach((plId) => {
        playlists[plId].total_videos = counts[plId] || 0;
    });
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
                if (activePlaylistPageId && String(downloads[event.id].playlist_id) === activePlaylistPageId) {
                    updatePlaylistPageInPlace(event.id);
                }
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
                if (activePlaylistPageId) renderPlaylistPage();
            }
            break;

        case "new":
            if (event.download) {
                downloads[event.download.id] = event.download;
                renderDownloads();
                if (activePlaylistPageId && String(event.download.playlist_id) === activePlaylistPageId) {
                    renderPlaylistPage();
                }
            }
            break;

        case "deleted":
            delete downloads[event.id];
            renderDownloads();
            if (activePlaylistPageId) renderPlaylistPage();
            break;

        case "playlist_deleted":
            if (activePlaylistPageId === String(event.playlist_id)) {
                closePlaylistPage();
            }
            delete playlists[event.playlist_id];
            Object.keys(downloads).forEach((k) => {
                if (downloads[k].playlist_id === event.playlist_id) {
                    delete downloads[k];
                }
            });
            renderDownloads();
            break;

        case "playlist_created":
            if (event.playlist) {
                playlists[event.playlist.id] = event.playlist;
                renderDownloads();
            }
            break;

        case "playlist_updated":
            if (event.playlist) {
                playlists[event.playlist.id] = event.playlist;
                renderDownloads();
                if (activePlaylistPageId === String(event.playlist.id)) {
                    renderPlaylistPage();
                }
            }
            break;

        case "download_moved":
            if (event.download) {
                downloads[event.download.id] = event.download;
                recalcPlaylistTotals();
                renderDownloads();
                if (activePlaylistPageId) renderPlaylistPage();
            }
            break;
    }
}

function updatePlaylistPageInPlace(id) {
    const dl = downloads[id];
    if (!dl || !activePlaylistPageId) return;

    const pageList = $("#playlistPageList");
    if (!pageList) return;
    const el = pageList.querySelector(`.download-item[data-id="${id}"]`);
    if (!el) return;

    const fill = el.querySelector(".progress-fill");
    if (fill) fill.style.width = `${dl.progress || 0}%`;

    const stats = el.querySelector(".progress-stats");
    if (stats) {
        const statusLine = `${dl.speed || ""} ${dl.eta ? "• " + dl.eta : ""}`;
        stats.innerHTML = `<span>${(dl.progress || 0).toFixed(1)}%</span><span>${statusLine}</span>`;
    }

    const group = Object.values(downloads).filter(
        (d) => String(d.playlist_id) === activePlaylistPageId
    );
    const completed = group.filter((d) => d.status === "completed").length;
    const total = group.length;
    const aggregateProgress = total > 0
        ? group.reduce((sum, d) => sum + (d.progress || 0), 0) / total
        : 0;
    $("#playlistPageMeta").textContent = `${completed}/${total} completed`;
    const progressEl = $("#playlistPageProgress");
    progressEl.style.width = `${aggregateProgress}%`;
    progressEl.className = `progress-fill ${completed === total ? "completed" : ""}`;
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
$("#playlistPageBack").addEventListener("click", () => {
    history.back();
});
$("#playlistPageThumbWrap").addEventListener("click", () => {
    if (activePlaylistPageId) openThumbPickerModal();
});
connectSSE();
