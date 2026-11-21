"use strict";

const $ = (s, el = document) => el.querySelector(s);

// Two ways this page runs:
//  * LOCAL  - served by the YTGuy helper itself (http://127.0.0.1:87xx), with a session token.
//  * REMOTE - hosted on GitHub Pages; it finds the helper on this PC and talks to it directly.
const META_TOKEN = document.querySelector('meta[name="ytguy-token"]').content;
const LOCAL = ["127.0.0.1", "localhost"].includes(location.hostname) && !META_TOKEN.startsWith("{{");
const TOKEN = LOCAL ? META_TOKEN : "";
const HELPER_PORTS = [8765, 8766, 8767, 8768, 8769];
const DOWNLOAD_URL = "https://github.com/froodyofficial-maker/ytguy/releases/latest/download/YTGuy.exe";
let API_BASE = LOCAL ? "" : null;

const QUALITIES = [
  { h: 2160, label: "4K", sub: "2160p · Ultra HD", tag: "UHD" },
  { h: 1440, label: "1440p", sub: "2K · Quad HD" },
  { h: 1080, label: "1080p", sub: "Full HD" },
  { h: 720, label: "720p", sub: "HD" },
  { h: 480, label: "480p", sub: "SD" },
  { h: 360, label: "360p", sub: "Low" },
  { h: 240, label: "240p", sub: "Very low" },
  { h: 144, label: "144p", sub: "Tiny" },
];
const FORMATS = {
  mp3: "MP3 plays on almost every device, car stereo and app.",
  m4a: "M4A (AAC) sounds better than MP3 at the same bitrate. Ideal for iPhone, iTunes and Apple Music.",
  opus: "Opus is YouTube's own audio format and gives the best quality for the file size.",
  flac: "FLAC is a lossless format. Files are larger, and the quality can't exceed what YouTube streams.",
  wav: "WAV is uncompressed audio, useful for video editors and DAWs. Files are very large.",
};
const LOSSLESS = new Set(["flac", "wav"]);
const BITRATES = [
  { k: "320", sub: "Best" }, { k: "256", sub: "Great" }, { k: "192", sub: "Good" },
  { k: "160", sub: "" }, { k: "128", sub: "Standard" }, { k: "96", sub: "Small" }, { k: "64", sub: "Voice" },
];

const state = {
  settings: null,
  info: null,          // last fetched info
  infoUrl: null,
  infoLoading: false,
  jobs: [],
  setup: null,
  pollTimer: null,
  finishedSeen: new Set(),
  settingsLoaded: false,
  firstLoad: true,
};

/* ---------------- api ---------------- */
async function api(path, body) {
  if (API_BASE === null) throw new Error("YTGuy isn't running on this computer yet.");
  const headers = {};
  if (TOKEN) headers["X-Token"] = TOKEN;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(API_BASE + "/api/" + path, {
    method: body === undefined ? "GET" : "POST",
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

/* ---------------- helpers ---------------- */
function el(tag, props = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (v !== undefined && v !== null && v !== false) n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) n.append(c);
  return n;
}
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const u = document.createElementNS("http://www.w3.org/2000/svg", "use");
  u.setAttribute("href", "#i-" + name);
  s.append(u);
  return s;
}
function fmtSize(n) {
  if (!n && n !== 0) return "";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (n >= 100 || i === 0 ? n.toFixed(0) : n.toFixed(1)) + " " + u[i];
}
function fmtTime(s) {
  if (s == null || isNaN(s)) return "";
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(sec).padStart(2, "0");
}
function shortPath(p = "") {
  if (p.length <= 52) return p;
  const parts = p.split(/[\\/]/), sep = p.includes("\\") ? "\\" : "/";
  let out = parts.pop();
  while (parts.length > 1 && (parts[parts.length - 1] + sep + out).length < 44) out = parts.pop() + sep + out;
  return parts[0] + sep + "…" + sep + out;
}
function fmtCount(n) {
  if (!n) return "";
  return new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 }).format(n) + " views";
}
function parseUrls(text) {
  return text.split(/[\s,]+/).map(s => s.trim()).filter(s => /^https?:\/\/\S+$/i.test(s) || /^(www\.)?(youtube\.com|youtu\.be|music\.youtube\.com)\//i.test(s))
    .map(s => /^https?:/i.test(s) ? s : "https://" + s);
}
function toast(msg, kind = "") {
  const t = el("div", { class: "toast " + kind }, kind === "ok" ? icon("check") : kind === "err" ? icon("alert") : null, el("span", { text: msg }));
  $("#toasts").append(t);
  setTimeout(() => { t.style.transition = "opacity .3s"; t.style.opacity = "0"; setTimeout(() => t.remove(), 300); }, kind === "err" ? 5000 : 3000);
}
let saveTimer;
function saveSettings(patch) {
  Object.assign(state.settings, patch);
  clearTimeout(saveTimer);
  if (API_BASE !== null) saveTimer = setTimeout(() => api("settings", patch).catch(e => toast(e.message, "err")), 150);
  renderOptions();
}

/* ---------------- options UI ---------------- */
function maxHeight() {
  return state.info && state.info.kind === "video" && state.info.heights?.length ? state.info.heights[0] : null;
}
function effectiveHeight() {
  const s = state.settings, max = maxHeight();
  if (!s.height) return max;
  return max ? Math.min(s.height, max) : s.height;
}

function renderOptions() {
  const s = state.settings;
  if (!s) return;
  const seg = $("#modeSeg");
  seg.dataset.mode = s.mode;
  for (const b of seg.querySelectorAll("button")) b.setAttribute("aria-selected", String(b.dataset.mode === s.mode));
  $("#videoOpts").hidden = s.mode !== "video";
  $("#audioOpts").hidden = s.mode !== "audio";

  // quality tiles
  const max = maxHeight();
  const sizes = state.info?.sizes || {};
  const qt = $("#qualityTiles");
  qt.replaceChildren(...QUALITIES.map(q => {
    const unavailable = !!(max && q.h > max);
    const size = sizes[q.h];
    const pressed = q.h === (s.height || 2160);
    return el("button", {
      class: "tile", type: "button", "aria-pressed": String(pressed), disabled: unavailable,
      title: unavailable ? `This video only goes up to ${max}p` : "",
      onclick: () => saveSettings({ height: q.h }),
    }, q.tag ? el("i", { class: "tag", text: q.tag }) : null,
      el("b", { text: q.label }), el("span", { text: unavailable ? "Not available" : (size ? "≈ " + fmtSize(size) : q.sub) }));
  }));
  let note = "";
  if (max && s.height && s.height > max) note = `This video tops out at ${max}p, so you'll get ${max}p.`;
  else if (max) note = `Available up to ${max >= 2160 ? "4K" : max + "p"}`;
  $("#qualityNote").textContent = note;
  $("#h264Toggle").checked = !!s.prefer_h264;

  // audio
  $("#formatChips").replaceChildren(...Object.keys(FORMATS).map(f => el("button", {
    type: "button", "aria-pressed": String(s.audio_format === f), text: f.toUpperCase(),
    onclick: () => saveSettings({ audio_format: f }),
  })));
  $("#formatDesc").textContent = FORMATS[s.audio_format] || "";
  const lossless = LOSSLESS.has(s.audio_format);
  const dur = state.info?.duration;
  $("#bitrateTiles").replaceChildren(...BITRATES.map(b => el("button", {
    class: "tile", type: "button", disabled: lossless, "aria-pressed": String(!lossless && s.bitrate === b.k),
    onclick: () => saveSettings({ bitrate: b.k }),
  }, el("b", { text: b.k }), el("span", { text: dur ? "≈ " + fmtSize(b.k * 1000 / 8 * dur) : (b.sub ? `kbps · ${b.sub}` : "kbps") }))));
  $("#bitrateNote").textContent = lossless ? "Lossless formats don't use a bitrate." : (dur ? "Estimated file size shown" : "");

  $("#thumbToggle").checked = !!s.embed_thumbnail;
  $("#playlistToggle").checked = !!s.playlist;
  $("#outDir").textContent = shortPath(s.out_dir);
  $("#outDir").title = s.out_dir;
  $("#outDirInput").value = s.out_dir;
  for (const b of document.querySelectorAll("#themeChips button")) b.setAttribute("aria-pressed", String(b.dataset.theme === currentTheme()));
  renderDownloadButton();
}

function renderDownloadButton() {
  const s = state.settings;
  const urls = parseUrls($("#url").value);
  let sub;
  if (s.mode === "video") {
    const h = effectiveHeight();
    sub = `MP4 · ${!h ? "Best quality" : h >= 2160 ? "4K" : h + "p"}`;
    const size = state.info?.sizes?.[s.height || 2160];
    if (size && urls.length <= 1 && !s.playlist) sub += ` · ≈ ${fmtSize(size)}`;
  } else {
    sub = LOSSLESS.has(s.audio_format) ? s.audio_format.toUpperCase() : `${s.audio_format.toUpperCase()} · ${s.bitrate} kbps`;
    if (state.info?.duration && !LOSSLESS.has(s.audio_format) && urls.length <= 1)
      sub += ` · ≈ ${fmtSize(s.bitrate * 1000 / 8 * state.info.duration)}`;
  }
  let main = "Download";
  if (urls.length > 1) main = `Download ${urls.length} links`;
  else if (s.playlist && state.info?.kind === "playlist") main = `Download playlist (${state.info.count})`;
  $("#dlMain").textContent = main;
  $("#dlSub").textContent = sub;
  $("#downloadBtn").disabled = urls.length === 0;
}

/* ---------------- preview ---------------- */
function renderPreview() {
  const p = $("#preview");
  p.className = "preview";
  if (state.infoLoading) {
    p.replaceChildren(el("div", { class: "card-preview skeleton" },
      el("div", { class: "thumb" }),
      el("div", { class: "meta" }, el("div", { class: "line", style: "width:90%" }), el("div", { class: "line", style: "width:60%" }), el("div", { class: "line", style: "width:35%" }))));
    return;
  }
  const info = state.info;
  if (info?.error) {
    p.replaceChildren(el("div", { class: "preview-error" }, icon("alert"),
      el("div", {}, el("b", { text: "Couldn't load that link" }), el("p", { text: info.error }))));
    return;
  }
  if (!info) {
    p.className = "preview empty";
    p.replaceChildren(el("div", { class: "empty-hint" }, el("div", { class: "steps" },
      el("div", {}, el("b", { text: "1" }), "Paste a link"),
      el("div", {}, el("b", { text: "2" }), "Choose a format and quality"),
      el("div", {}, el("b", { text: "3" }), "Download"))));
    return;
  }
  const thumb = el("div", { class: "thumb" });
  if (info.thumbnail) thumb.append(el("img", { src: info.thumbnail, alt: "", referrerpolicy: "no-referrer", loading: "lazy" }));
  if (info.kind === "playlist") thumb.append(el("span", { class: "badge", text: `${info.count} videos` }));
  else if (info.duration) thumb.append(el("span", { class: "badge", text: fmtTime(info.duration) }));

  const sub = el("div", { class: "sub" });
  if (info.uploader) sub.append(el("span", { text: info.uploader }));
  if (info.view_count) sub.append(el("span", { text: fmtCount(info.view_count) }));
  if (info.kind === "playlist" && info.duration) sub.append(el("span", { text: fmtTime(info.duration) + " total" }));

  const pills = el("div", { class: "pills" });
  if (info.kind === "video" && info.heights?.length) {
    const max = info.heights[0];
    pills.append(el("span", { class: "pill hot", text: max >= 2160 ? "4K available" : `Up to ${max}p` }));
    for (const h of info.heights.slice(1, 6)) pills.append(el("span", { class: "pill", text: h + "p" }));
  } else if (info.kind === "playlist") {
    pills.append(el("span", { class: "pill hot", text: "Playlist" }));
  }
  p.replaceChildren(el("div", { class: "card-preview" }, thumb,
    el("div", { class: "meta" }, el("h3", { text: info.title || "Untitled" }), sub, pills)));
}

function renderPlaylistBanner() {
  const info = state.info;
  const show = !!info && !info.error && info.has_playlist && parseUrls($("#url").value).length <= 1;
  $("#playlistBanner").hidden = !show;
  if (show) {
    $("#playlistNote").textContent = state.settings.playlist
      ? (info.kind === "playlist" ? `The whole playlist (${info.count} videos) will be downloaded into its own folder.` : "The whole playlist will be downloaded.")
      : "Only this video will be downloaded. Switch on to get the whole playlist.";
  }
}

let fetchSeq = 0;
async function loadInfo(force = false) {
  const urls = parseUrls($("#url").value);
  const meta = $("#urlMeta");
  meta.hidden = urls.length <= 1;
  if (urls.length > 1) meta.textContent = `${urls.length} links detected. They'll be queued with the settings below.`;
  renderDownloadButton();
  if (urls.length && API_BASE === null) { showConnect(); return; }
  if (urls.length === 0) {
    state.info = null; state.infoUrl = null; state.infoLoading = false;
    renderPreview(); renderPlaylistBanner(); renderOptions();
    return;
  }
  const url = urls[0];
  const key = url + "|" + (state.settings.playlist ? 1 : 0);
  if (!force && key === state.infoUrl) return;
  if (state.setup?.missing?.includes("yt-dlp")) { state.infoLoading = false; showSetup(); return; }
  state.infoUrl = key;
  state.infoLoading = true;
  renderPreview();
  const seq = ++fetchSeq;
  try {
    const info = await api("info", { url, playlist: !!state.settings.playlist });
    if (seq !== fetchSeq) return;
    state.info = info;
  } catch (e) {
    if (seq !== fetchSeq) return;
    state.info = { error: e.message };
    state.infoUrl = null;
  }
  state.infoLoading = false;
  renderPreview(); renderPlaylistBanner(); renderOptions();
}

/* ---------------- download ---------------- */
async function startDownload() {
  if (API_BASE === null) { showConnect(); return; }
  const urls = parseUrls($("#url").value);
  if (!urls.length) { toast("Paste a YouTube link first.", "err"); $("#url").focus(); return; }
  if (blockingMissing().length) { showSetup(); return; }
  const s = state.settings;
  const info = state.info && !state.info.error ? state.info : null;
  const meta = info && urls.length === 1 ? { title: info.title, thumbnail: info.thumbnail, duration: info.duration } : {};
  const btn = $("#downloadBtn");
  btn.disabled = true;
  try {
    await api("download", {
      urls, mode: s.mode, height: s.height || null, audio_format: s.audio_format, bitrate: s.bitrate,
      prefer_h264: s.prefer_h264, embed_thumbnail: s.embed_thumbnail,
      playlist: urls.length === 1 && info?.has_playlist ? s.playlist : false, meta,
    });
    toast(urls.length > 1 ? `${urls.length} downloads added` : "Download started", "ok");
    $("#url").value = "";
    autoGrow();
    state.info = null; state.infoUrl = null;
    renderPreview(); renderPlaylistBanner(); renderOptions();
    $("#urlMeta").hidden = true;
    poll(true);
  } catch (e) {
    toast(e.message, "err");
  } finally {
    renderDownloadButton();
  }
}

/* ---------------- jobs ---------------- */
const STATUS_TEXT = { queued: "Waiting in queue", downloading: "Downloading", processing: "Processing", done: "Done", error: "Failed", cancelled: "Cancelled" };

function jobCard(job) {
  const card = el("article", { class: "job", "data-id": job.id });
  card.innerHTML = `
    <div class="job-top">
      <div class="thumb"></div>
      <div class="job-info">
        <div class="job-title"></div>
        <div class="job-tags"><span class="pill job-label"></span><span class="job-status"></span></div>
      </div>
    </div>
    <div class="bar"><span></span></div>
    <div class="job-stats"><span class="st-left"></span><span class="st-right"></span></div>
    <div class="job-error" hidden></div>
    <div class="job-actions"></div>
    <details><summary>Details <svg><use href="#i-chevron"/></svg></summary><pre></pre></details>`;
  return card;
}

function updateJobCard(card, job) {
  card.dataset.status = job.status;
  const thumb = $(".thumb", card);
  const img = thumb.querySelector("img");
  if (job.thumbnail && (!img || img.getAttribute("src") !== job.thumbnail)) {
    thumb.replaceChildren(el("img", { src: job.thumbnail, alt: "", referrerpolicy: "no-referrer", loading: "lazy" }));
  } else if (!job.thumbnail && !thumb.firstChild) {
    thumb.append(el("div", { class: "ph" }, icon(job.mode === "audio" ? "music" : "video")));
  }
  $(".job-title", card).textContent = job.title;
  $(".job-title", card).title = job.title;
  $(".job-label", card).textContent = job.label;

  const statusEl = $(".job-status", card);
  let st = STATUS_TEXT[job.status] || job.status;
  if ((job.status === "downloading" || job.status === "processing") && job.stage) st = job.stage;
  if (job.items && job.items > 1 && job.status !== "done") st += ` · ${job.item}/${job.items}`;
  if (job.status === "done" && job.files.length > 1) st = `Done · ${job.files.length} files`;
  statusEl.replaceChildren(job.status === "done" ? icon("check") : job.status === "error" ? icon("alert") : "", el("span", { text: st }));

  const pct = job.status === "done" ? 100 : (job.progress || 0);
  const bar = $(".bar", card);
  bar.querySelector("span").style.width = pct + "%";
  bar.classList.toggle("indeterminate", job.status === "downloading" && !job.total);
  bar.hidden = job.status === "error" || job.status === "cancelled";

  const left = $(".st-left", card), right = $(".st-right", card);
  if (job.status === "downloading" && job.total) {
    left.textContent = `${Math.floor(pct)}% · ${fmtSize(job.downloaded)} of ${fmtSize(job.total)}`;
    right.textContent = [job.speed ? fmtSize(job.speed) + "/s" : "", job.eta != null ? fmtTime(job.eta) + " left" : ""].filter(Boolean).join(" · ");
  } else if (job.status === "done" && job.files[0]) {
    left.textContent = job.files.length === 1 ? job.files[0].split(/[\\/]/).pop() : "";
    left.title = job.files[0];
    right.textContent = "";
  } else {
    left.textContent = ""; right.textContent = "";
  }
  $(".job-stats", card).hidden = !left.textContent && !right.textContent;

  const err = $(".job-error", card);
  err.hidden = job.status !== "error";
  if (job.status === "error") err.textContent = humanError(job.error);

  // actions (only rebuild when the status changes)
  const actions = $(".job-actions", card);
  if (actions.dataset.status !== job.status) {
    actions.dataset.status = job.status;
    const btns = [];
    const B = (ic, label, fn, cls = "ghost") => el("button", { class: "btn sm " + cls, type: "button", onclick: fn }, icon(ic), label);
    if (["queued", "downloading", "processing"].includes(job.status))
      btns.push(B("x", "Cancel", () => api("cancel", { id: job.id }).then(() => poll(true))));
    if (job.status === "done") {
      btns.push(B("folder", job.files.length === 1 ? "Show in folder" : "Open folder", () => api("reveal", { path: job.files.length === 1 ? job.files[0] : "" }).catch(e => toast(e.message, "err"))));
    }
    if (job.status === "error" || job.status === "cancelled")
      btns.push(B("retry", "Retry", () => api("retry", { id: job.id }).then(() => poll(true))));
    if (["done", "error", "cancelled"].includes(job.status))
      btns.push(B("trash", "Remove", () => api("remove", { id: job.id }).then(() => poll(true))));
    actions.replaceChildren(...btns);
  }
  const pre = $("pre", card);
  const logText = job.log.join("\n");
  if (pre.textContent !== logText) {
    const atBottom = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 4;
    pre.textContent = logText || "No output yet.";
    if (atBottom) pre.scrollTop = pre.scrollHeight;
  }
}

function humanError(msg = "") {
  if (/Sign in to confirm|not a bot/i.test(msg)) return "YouTube asked to confirm you're not a bot. Wait a few minutes, update yt-dlp in Settings, then retry.";
  if (/Private video/i.test(msg)) return "This video is private.";
  if (/Video unavailable/i.test(msg)) return "This video is unavailable (deleted, private, or blocked in your region).";
  if (/age/i.test(msg) && /confirm|restricted/i.test(msg)) return "This video is age-restricted and can't be downloaded without signing in.";
  if (/Unsupported URL/i.test(msg)) return "That doesn't look like a supported link.";
  return msg || "Something went wrong.";
}

function renderJobs() {
  const list = $("#jobs");
  const existing = new Map([...list.children].map(c => [c.dataset.id, c]));
  const ids = new Set(state.jobs.map(j => j.id));
  for (const [id, c] of existing) if (!ids.has(id)) c.remove();
  let prev = null;
  for (const job of state.jobs) {
    let card = existing.get(job.id);
    if (!card) card = jobCard(job);
    updateJobCard(card, job);
    const want = prev ? prev.nextSibling : list.firstChild;
    if (want !== card) list.insertBefore(card, want);
    prev = card;

    if (!state.firstLoad && (job.status === "done" || job.status === "error") && !state.finishedSeen.has(job.id)) {
      if (job.status === "done") toast(`Finished: ${job.title}`, "ok");
      else toast(`Failed: ${job.title}`, "err");
    }
    if (job.status === "done" || job.status === "error") state.finishedSeen.add(job.id);
  }
  state.firstLoad = false;
  const active = state.jobs.filter(j => ["queued", "downloading", "processing"].includes(j.status)).length;
  const finished = state.jobs.length - active;
  $("#queueEmpty").hidden = state.jobs.length > 0;
  $("#queueCount").hidden = !active;
  $("#queueCount").textContent = active;
  $("#clearBtn").hidden = !finished;
  const pct = state.jobs.find(j => j.status === "downloading" && j.total);
  document.title = active ? `(${pct ? Math.floor(pct.progress) + "%" : active}) YTGuy` : "YTGuy";
}

/* ---------------- setup ---------------- */
const TOOL_INFO = { "yt-dlp": "yt-dlp: the downloader", ffmpeg: "FFmpeg: merging and conversion", deno: "Deno: needed by yt-dlp for YouTube" };
let setupWanted = false;
let platform = "windows";
// yt-dlp + FFmpeg are required for any download; Deno is needed for YouTube links.
function blockingMissing() { return (state.setup?.missing || []).filter(n => n !== "deno" || platform === "windows"); }
function showSetup() {
  if (platform !== "windows") {
    toast(`Missing: ${state.setup.missing.join(", ")}. Install them and add them to PATH.`, "err");
    return;
  }
  setupWanted = true; renderSetup();
}
function renderSetup() {
  const s = state.setup;
  if (!s) return;
  const needs = s.missing.length > 0 || s.running;
  const modal = $("#setupModal");
  if (!needs || (!setupWanted && !s.running)) {
    if (!modal.hidden && !needs) { modal.hidden = true; toast("Setup complete. You're ready to go!", "ok"); loadInfo(true); }
    return;
  }
  modal.hidden = false;
  const names = Object.keys(TOOL_INFO);
  $("#setupList").replaceChildren(...names.map(n => {
    const missing = s.missing.includes(n);
    const active = s.running && s.current === n;
    return el("li", { class: active ? "active" : missing ? "" : "done" },
      el("span", { class: "dot" }, !missing && !active ? icon("check") : null),
      el("span", { text: TOOL_INFO[n] }),
      el("span", { class: "muted", text: active ? (s.total ? Math.floor(s.done / s.total * 100) + "%" : fmtSize(s.done)) : missing ? "Needed" : "Ready" }));
  }));
  const bar = $("#setupBar");
  if (s.running) {
    const each = 1 / Math.max(1, s.count);
    const frac = (s.index - 1) * each + (s.total ? s.done / s.total : 0) * each;
    bar.style.width = (frac * 100).toFixed(1) + "%";
    $("#setupText").textContent = `Downloading ${s.current}… ${s.total ? fmtSize(s.done) + " of " + fmtSize(s.total) : fmtSize(s.done)}`;
  } else {
    bar.style.width = "0%";
    $("#setupText").innerHTML = "&nbsp;";
  }
  $("#setupError").hidden = !s.error;
  $("#setupError").textContent = s.error ? "Setup failed: " + s.error : "";
  const btn = $("#setupBtn");
  btn.disabled = s.running;
  btn.textContent = s.running ? "Downloading…" : s.error ? "Try again" : "Download components";
}

/* ---------------- helper connection (GitHub Pages mode) ---------------- */
let connectDismissed = false;
async function findHelper() {
  for (const port of HELPER_PORTS) {
    const base = `http://127.0.0.1:${port}`;
    try {
      const res = await fetch(base + "/api/ping", { signal: AbortSignal.timeout(1500) });
      const j = await res.json();
      if (j.app === "YTGuy") return base;
    } catch (e) { /* not on this port */ }
  }
  return null;
}
function setConnected(ok) {
  const pill = $("#helperPill");
  if (!LOCAL) {
    pill.hidden = false;
    pill.dataset.ok = String(ok);
    $("#helperPillText").textContent = ok ? "Connected" : "Not connected";
  }
  $("#connectModal").hidden = LOCAL || ok || connectDismissed;
  document.body.classList.toggle("disconnected", !ok);
}
function showConnect() { connectDismissed = false; setConnected(false); }

/* ---------------- polling ---------------- */
let failures = 0;
async function poll(immediate) {
  clearTimeout(state.pollTimer);
  if (!LOCAL && API_BASE === null) {
    API_BASE = await findHelper();
    if (API_BASE === null) {
      setConnected(false);
      state.pollTimer = setTimeout(poll, 2500);
      return;
    }
    state.settingsLoaded = false; // reload settings from the helper we just found
    toast("Connected to YTGuy", "ok");
  }
  try {
    const data = await api("state");
    failures = 0;
    $("#offline").hidden = true;
    setConnected(true);
    if (!state.settingsLoaded) {
      state.settingsLoaded = true;
      state.settings = data.settings;
      applyTheme(localTheme() || data.settings.theme || "system");
      $("#appVersion").textContent = `YTGuy v${data.version}`;
      platform = data.platform;
      renderOptions();
    }
    state.jobs = data.jobs;
    state.setup = data.setup;
    if (state.setup.missing.length && !state.setup.running && !setupWanted && data.platform === "windows") setupWanted = true;
    renderJobs();
    renderSetup();
  } catch (e) {
    if (++failures >= 2) {
      if (LOCAL) $("#offline").hidden = false;
      else { API_BASE = null; setConnected(false); state.pollTimer = setTimeout(poll, 2500); return; }
    }
  }
  const busy = state.jobs.some(j => ["queued", "downloading", "processing"].includes(j.status)) || state.setup?.running;
  state.pollTimer = setTimeout(poll, busy ? 600 : 3000);
}

/* ---------------- theme ---------------- */
function localTheme() { try { return localStorage.getItem("ytguy-theme"); } catch (e) { return null; } }
function currentTheme() { return document.documentElement.dataset.theme || "system"; }
function effectiveDark() {
  const t = currentTheme();
  return t === "dark" || (t === "system" && !matchMedia("(prefers-color-scheme: light)").matches);
}
function applyTheme(t) {
  if (t === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  try { localStorage.setItem("ytguy-theme", t); } catch (e) {}
  $("#themeBtn use").setAttribute("href", effectiveDark() ? "#i-sun" : "#i-moon");
  for (const b of document.querySelectorAll("#themeChips button")) b.setAttribute("aria-pressed", String(b.dataset.theme === t));
}

/* ---------------- drawer ---------------- */
function openDrawer() {
  $("#drawer").classList.add("open");
  $("#drawer").setAttribute("aria-hidden", "false");
  $("#scrim").hidden = false;
  api("versions").then(v => {
    $("#versions").replaceChildren(...Object.entries(v).map(([k, val]) =>
      el("div", {}, el("span", { text: k }), el("code", { text: val || "not installed" }))));
  }).catch(() => {});
}
function closeDrawer() {
  $("#drawer").classList.remove("open");
  $("#drawer").setAttribute("aria-hidden", "true");
  $("#scrim").hidden = true;
}

/* ---------------- events ---------------- */
const urlBox = $("#url");
function autoGrow() { urlBox.style.height = "auto"; urlBox.style.height = Math.min(urlBox.scrollHeight, 140) + "px"; }
let typeTimer;
urlBox.addEventListener("input", () => {
  autoGrow();
  renderDownloadButton();
  clearTimeout(typeTimer);
  typeTimer = setTimeout(() => loadInfo(), 450);
});
urlBox.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (state.info && !state.info.error && !state.infoLoading) startDownload(); else loadInfo(true);
  }
});
$("#urlForm").addEventListener("submit", e => { e.preventDefault(); loadInfo(true); });
$("#pasteBtn").addEventListener("click", async () => {
  try {
    const text = (await navigator.clipboard.readText()).trim();
    if (!text) return toast("Your clipboard is empty.");
    urlBox.value = urlBox.value.trim() ? urlBox.value.trim() + "\n" + text : text;
    autoGrow(); loadInfo(true);
  } catch (e) {
    urlBox.focus();
    toast("Press Ctrl+V to paste");
  }
});
$("#modeSeg").addEventListener("click", e => {
  const b = e.target.closest("button[data-mode]");
  if (b) saveSettings({ mode: b.dataset.mode });
});
$("#h264Toggle").addEventListener("change", e => saveSettings({ prefer_h264: e.target.checked }));
$("#thumbToggle").addEventListener("change", e => saveSettings({ embed_thumbnail: e.target.checked }));
$("#playlistToggle").addEventListener("change", e => { saveSettings({ playlist: e.target.checked }); renderPlaylistBanner(); loadInfo(); });
$("#downloadBtn").addEventListener("click", startDownload);
$("#clearBtn").addEventListener("click", () => api("clear", {}).then(() => poll(true)));

async function pickFolder() {
  if (API_BASE === null) { showConnect(); return; }
  try {
    const r = await api("pick-folder", {});
    if (r.changed) { state.settings.out_dir = r.out_dir; renderOptions(); toast("Download folder updated", "ok"); }
  } catch (e) { toast(e.message, "err"); }
}
$("#changeDirBtn").addEventListener("click", pickFolder);
$("#browseDirBtn").addEventListener("click", pickFolder);
$("#openDirBtn").addEventListener("click", () => API_BASE === null ? showConnect() : api("reveal", { path: "" }).catch(e => toast(e.message, "err")));
$("#saveDirBtn").addEventListener("click", () => {
  const v = $("#outDirInput").value.trim();
  if (!v) return;
  saveSettings({ out_dir: v });
  toast("Download folder updated", "ok");
});

$("#themeBtn").addEventListener("click", () => applyTheme(effectiveDark() ? "light" : "dark"));
$("#themeChips").addEventListener("click", e => {
  const b = e.target.closest("button[data-theme]");
  if (b) applyTheme(b.dataset.theme);
});
matchMedia("(prefers-color-scheme: light)").addEventListener("change", () => applyTheme(currentTheme()));
$("#settingsBtn").addEventListener("click", openDrawer);
$("#closeDrawer").addEventListener("click", closeDrawer);
$("#scrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeDrawer(); });

$("#updateBtn").addEventListener("click", async () => {
  if (API_BASE === null) { showConnect(); return; }
  try { await api("update", {}); setupWanted = false; toast("Updating yt-dlp…"); watchUpdate(); }
  catch (e) { toast(e.message, "err"); }
});
async function watchUpdate() {
  await new Promise(r => setTimeout(r, 800));
  const s = (await api("state")).setup;
  if (s.running) return watchUpdate();
  if (s.error) toast("Update failed: " + s.error, "err");
  else { toast("yt-dlp is up to date", "ok"); openDrawer(); }
}
$("#quitBtn").addEventListener("click", async () => {
  if (!confirm("Quit YTGuy? Any running downloads will be cancelled.")) return;
  try { await api("quit", {}); } catch (e) {}
  if (!LOCAL) { closeDrawer(); API_BASE = null; showConnect(); poll(true); return; }
  document.body.innerHTML = '<div style="display:grid;place-items:center;height:100vh;text-align:center;font:16px system-ui;padding:16px"><div><h2>YTGuy has stopped.</h2><p style="opacity:.7">You can close this tab. Start YTGuy again from its shortcut.</p></div></div>';
});
$("#setupBtn").addEventListener("click", async () => {
  try { state.setup = await api("setup", {}); renderSetup(); poll(true); }
  catch (e) { toast(e.message, "err"); }
});

// drag & drop links
let dragDepth = 0;
window.addEventListener("dragenter", e => { if (e.dataTransfer.types.includes("text/uri-list") || e.dataTransfer.types.includes("text/plain")) { dragDepth++; $("#dropOverlay").hidden = false; } });
window.addEventListener("dragleave", () => { if (--dragDepth <= 0) { dragDepth = 0; $("#dropOverlay").hidden = true; } });
window.addEventListener("dragover", e => e.preventDefault());
window.addEventListener("drop", e => {
  e.preventDefault(); dragDepth = 0; $("#dropOverlay").hidden = true;
  const text = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
  if (text) { urlBox.value = text.trim(); autoGrow(); loadInfo(true); }
});

// global paste: if focus isn't in a field, paste into the link box
document.addEventListener("paste", e => {
  if (e.target.closest("input, textarea")) return;
  const text = e.clipboardData.getData("text").trim();
  if (parseUrls(text).length) { urlBox.value = text; autoGrow(); loadInfo(true); }
});

// connect screen
$("#connectDownload").href = DOWNLOAD_URL;
$("#connectRetry").addEventListener("click", async () => {
  $("#connectStatus").textContent = "Looking for YTGuy…";
  await poll(true);
  if (API_BASE === null) $("#connectStatus").textContent = "Still not found. Make sure YTGuy.exe is open, then try again.";
});
$("#connectLater").addEventListener("click", () => { connectDismissed = true; setConnected(false); });
$("#helperPill").addEventListener("click", () => { if (API_BASE === null) showConnect(); });

// Defaults so the page is usable (and explorable) before the helper connects.
state.settings = { out_dir: "Downloads\\YTGuy", mode: "video", height: 1080, audio_format: "mp3", bitrate: "320",
                   prefer_h264: true, embed_thumbnail: true, playlist: false, theme: "system" };
renderOptions();
applyTheme(localTheme() || "system");
poll(true);
urlBox.focus();
