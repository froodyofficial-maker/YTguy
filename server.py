"""
YTGuy - local web app for downloading YouTube videos as MP4 (144p-4K) or audio
as MP3/M4A/Opus/FLAC/WAV.

Runs a tiny web server on 127.0.0.1 (your PC only) and opens the UI in your
browser. Nothing is hosted online; all downloading happens on this machine.
"""

import json
import re
import os
import secrets
import socket
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import core

HOST = "127.0.0.1"
PORTS = range(8765, 8770)  # the GitHub Pages site looks for the helper on these ports
# Web pages allowed to control this helper from the browser (the GitHub Pages site).
ALLOWED_ORIGINS = {"https://froodyofficial-maker.github.io"} | {
    o.strip().rstrip("/") for o in os.environ.get("YTGUY_ALLOWED_ORIGINS", "").split(",") if o.strip()}
WEB_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "web"
TOKEN = secrets.token_urlsafe(24)
MAX_PARALLEL = 3
INFO_CACHE_SECONDS = 2 * 3600  # YouTube stream URLs stay valid for ~6 h
IDLE_EXIT_SECONDS = 15 * 60  # quit when the browser has been gone this long

STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml",
                ".png": "image/png", ".ico": "image/x-icon"}

QUALITY_HEIGHTS = [2160, 1440, 1080, 720, 480, 360, 240, 144]


# --------------------------------------------------------------------------- settings

class Settings:
    DEFAULTS = {
        "out_dir": str(Path.home() / "Downloads" / "YTGuy"),
        "mode": "video", "height": 1080, "audio_format": "mp3", "bitrate": "320",
        "prefer_h264": True, "embed_thumbnail": True, "playlist": False, "theme": "system",
        "fast_mode": True,
    }

    def __init__(self):
        self.lock = threading.Lock()
        self.data = dict(self.DEFAULTS)
        try:
            self.data.update(json.loads(core.SETTINGS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    def get(self):
        with self.lock:
            return dict(self.data)

    def update(self, values):
        with self.lock:
            for k, v in values.items():
                if k in self.DEFAULTS:
                    self.data[k] = v
            try:
                core.APP_DIR.mkdir(parents=True, exist_ok=True)
                core.SETTINGS_FILE.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            except Exception:
                pass
            return dict(self.data)


# --------------------------------------------------------------------------- setup

class Setup:
    def __init__(self):
        self.lock = threading.Lock()
        self.state = {"running": False, "current": None, "index": 0, "count": 0,
                      "done": 0, "total": 0, "error": None}

    def status(self):
        with self.lock:
            s = dict(self.state)
        s["missing"] = core.missing_tools()
        return s

    def start(self, only=None):
        with self.lock:
            if self.state["running"]:
                return
            names = only or core.missing_tools()
            if not names:
                return
            self.state.update(running=True, error=None, count=len(names), index=0)
        threading.Thread(target=self._run, args=(names,), daemon=True).start()

    def _run(self, names):
        try:
            for i, name in enumerate(names, 1):
                with self.lock:
                    self.state.update(current=name, index=i, done=0, total=0)

                def prog(done, total):
                    with self.lock:
                        self.state.update(done=done, total=total)

                core.install_tool(name, prog)
        except Exception as e:
            with self.lock:
                self.state["error"] = str(e)
        finally:
            with self.lock:
                self.state.update(running=False, current=None)


# --------------------------------------------------------------------------- jobs

class Job:
    def __init__(self, url, opts, meta):
        self.id = uuid.uuid4().hex[:10]
        self.url = url
        self.opts = opts
        self.title = meta.get("title") or url
        self.thumbnail = meta.get("thumbnail")
        self.duration = meta.get("duration")
        self.status = "queued"   # queued|downloading|processing|done|error|cancelled
        self.stage = ""
        self.progress = 0.0
        self.downloaded = None
        self.total = None
        self.speed = None
        self.eta = None
        self.item = None
        self.items = None
        self.files = []
        self.error = None
        self.log = []
        self.created = time.time()
        self.proc = None
        self.cancelled = False

    def label(self):
        o = self.opts
        if o["mode"] == "video":
            h = o.get("height")
            return f"MP4 · {'4K' if h == 2160 else (f'{h}p' if h else 'Best')}"
        fmt = o["audio_format"].upper()
        return fmt if o["audio_format"] in core.LOSSLESS else f"{fmt} · {o['bitrate']} kbps"

    def to_dict(self):
        return {k: getattr(self, k) for k in (
            "id", "url", "title", "thumbnail", "duration", "status", "stage", "progress",
            "downloaded", "total", "speed", "eta", "item", "items", "files", "error", "created")
        } | {"label": self.label(), "mode": self.opts["mode"], "log": self.log[-60:]}


class JobManager:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.jobs = {}
        self.order = []
        self.wake = threading.Condition(self.lock)
        for _ in range(MAX_PARALLEL):
            threading.Thread(target=self._worker, daemon=True).start()

    def list(self):
        with self.lock:
            return [self.jobs[i].to_dict() for i in reversed(self.order)]

    def active(self):
        with self.lock:
            return any(j.status in ("queued", "downloading", "processing") for j in self.jobs.values())

    def add(self, url, opts, meta):
        """Queue a download; returns (job, is_new). An identical download that is
        still queued or running is reused instead of fighting over the same file."""
        with self.lock:
            for existing in self.jobs.values():
                if (existing.url == url and existing.opts == opts
                        and existing.status in ("queued", "downloading", "processing")):
                    return existing, False
            job = Job(url, opts, meta)
            self.jobs[job.id] = job
            self.order.append(job.id)
            self.wake.notify()
        return job, True

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            job.cancelled = True
            if job.status == "queued":
                job.status = "cancelled"
        if job.proc:
            core.kill_process_tree(job.proc)

    def retry(self, job_id):
        with self.lock:
            old = self.jobs.get(job_id)
        if old and old.status in ("error", "cancelled"):
            self.remove(job_id)
            return self.add(old.url, old.opts, {"title": old.title, "thumbnail": old.thumbnail,
                                                "duration": old.duration})[0]

    def remove(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if job and job.status not in ("downloading", "processing"):
                self.jobs.pop(job_id)
                self.order.remove(job_id)

    def clear_finished(self):
        with self.lock:
            for jid in list(self.order):
                if self.jobs[jid].status in ("done", "error", "cancelled"):
                    self.jobs.pop(jid)
                    self.order.remove(jid)

    def cancel_all(self):
        with self.lock:
            ids = list(self.jobs)
        for jid in ids:
            self.cancel(jid)

    # ----- worker
    def _next(self):
        with self.lock:
            while True:
                for jid in self.order:
                    job = self.jobs[jid]
                    if job.status == "queued":
                        job.status = "downloading"
                        job.stage = "Starting…"
                        return job
                self.wake.wait()

    def _worker(self):
        while True:
            job = self._next()
            try:
                self._run(job)
            except Exception as e:
                job.status, job.error = "error", str(e)

    def _run(self, job):
        ytdlp = core.tool_path("yt-dlp")
        if not ytdlp or not core.tool_path("ffmpeg"):
            job.status, job.error = "error", "Required components are missing. Run setup first."
            return
        o = job.opts
        Path(o["out_dir"]).mkdir(parents=True, exist_ok=True)
        fast = o.get("fast_mode", True) and bool(core.tool_path("aria2c"))
        info = None if o["playlist"] else cached_info(job.url)
        code, errors = self._attempt(job, ytdlp, fast=fast, info=info)
        if code not in (0, None) and not job.cancelled and (fast or info):
            # Fast path failed (e.g. a server refusing parallel connections or an
            # expired link): retry once the plain, proven way.
            job.log.append("[YTGuy] Retrying with the standard downloader…")
            job.status, job.stage, job.progress = "downloading", "Retrying", 0.0
            code, errors = self._attempt(job, ytdlp, fast=False, info=None)

        job.speed = job.eta = None
        if job.cancelled:
            job.status, job.stage = "cancelled", ""
        elif code == 0:
            job.status, job.stage, job.progress = "done", "", 100.0
        else:
            job.status = "error"
            job.error = (errors[-1] if errors else f"yt-dlp exited with code {code}")

    def _attempt(self, job, ytdlp, fast, info):
        o = job.opts
        work_dir = tempfile.mkdtemp(prefix="ytguy-")
        files_log = os.path.join(work_dir, "files.txt")
        info_json = None
        if info:
            info_json = os.path.join(work_dir, "info.json")
            with open(info_json, "wb") as f:
                f.write(info)
        cmd = core.build_command(
            ytdlp, [job.url], o["out_dir"], o["mode"], height=o.get("height"),
            prefer_h264=o["prefer_h264"], audio_format=o["audio_format"], bitrate=o["bitrate"],
            playlist=o["playlist"], embed_thumbnail=o["embed_thumbnail"], filepath_log=files_log,
            temp_dir=os.path.join(work_dir, "parts"), fast=fast, info_json=info_json)
        errors = []
        code = None
        stop_watch = threading.Event()
        if fast:
            threading.Thread(target=self._watch_aria2, args=(job, work_dir, stop_watch), daemon=True).start()
        try:
            job.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        creationflags=core.NO_WINDOW, env=core.subprocess_env())
            for raw in job.proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                if not line:
                    continue
                if line.startswith("[PROG]"):
                    self._progress(job, line[6:])
                    continue
                if line.startswith("[#") and self._aria2_progress(job, line):
                    continue
                job.log.append(line)
                del job.log[:-300]
                if line.startswith("ERROR"):
                    errors.append(line.replace("ERROR: ", "", 1))
                elif line.startswith(("[Merger]", "[VideoRemuxer]", "[VideoConvertor]")):
                    job.status, job.stage = "processing", "Merging video + audio"
                elif line.startswith("[ExtractAudio]"):
                    job.status, job.stage = "processing", f"Converting to {o['audio_format'].upper()}"
                elif line.startswith(("[EmbedThumbnail]", "[Metadata]")):
                    job.status, job.stage = "processing", "Adding tags & cover art"
                elif "has already been downloaded" in line:
                    job.stage = "Already downloaded"
            code = job.proc.wait()
        finally:
            stop_watch.set()
            job.proc = None
            try:
                job.files = [l for l in Path(files_log).read_text(encoding="utf-8").splitlines() if l]
            except Exception:
                pass
            shutil.rmtree(work_dir, ignore_errors=True)
        return code, errors

    @staticmethod
    def _watch_aria2(job, work_dir, stop):
        """Live progress for aria2c downloads, read from its .aria2 control file."""
        last = {}  # control file -> (time, bytes) for speed
        speed = None
        while not stop.wait(0.5):
            try:
                ctl = [os.path.join(dp, f) for dp, _, fs in os.walk(work_dir) for f in fs if f.endswith(".aria2")]
                if not ctl:
                    continue
                path = max(ctl, key=os.path.getmtime)
                done, total = core.read_aria2_control(path)
            except Exception:
                continue
            now = time.time()
            prev = last.get(path)
            if prev and now - prev[0] >= 0.9 and done >= prev[1]:
                inst = (done - prev[1]) / (now - prev[0])
                speed = inst if speed is None else 0.6 * speed + 0.4 * inst
                last[path] = (now, done)
            elif not prev:
                last[path] = (now, done)
            if not total:
                continue
            job.status = "downloading"
            if not job.stage.startswith("Downloading"):
                job.stage = "Downloading audio" if job.opts["mode"] == "audio" else "Downloading"
            job.downloaded, job.total = done, total
            job.progress = min(100.0, done * 100 / total)
            job.speed = speed
            job.eta = (total - done) / speed if speed else None

    _ARIA2 = re.compile(r"^\[#\w+ ([\d.]+)(\w*)/([\d.]+)(\w*)\((\d+)%\)"
                        r"(?: CN:\d+)?(?: DL:([\d.]+)(\w*))?(?: ETA:(\w+))?\]")
    _UNITS = {"": 1, "B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4}

    @classmethod
    def _aria2_progress(cls, job, line):
        """Parse aria2c's once-a-second readout, e.g. [#d30ded 15MiB/38MiB(39%) CN:8 DL:15MiB ETA:1s]."""
        m = cls._ARIA2.match(line)
        if not m:
            return False
        u = cls._UNITS
        done = float(m.group(1)) * u.get(m.group(2), 1)
        total = float(m.group(3)) * u.get(m.group(4), 1)
        job.status = "downloading"
        if not job.stage.startswith("Downloading"):
            job.stage = "Downloading audio" if job.opts["mode"] == "audio" else "Downloading"
        job.downloaded, job.total = done, total
        job.progress = float(m.group(5))
        job.speed = float(m.group(6)) * u.get(m.group(7), 1) if m.group(6) else None
        eta = m.group(8)
        if eta:
            secs = 0
            for num, unit in re.findall(r"(\d+)([hms])", eta):
                secs += int(num) * {"h": 3600, "m": 60, "s": 1}[unit]
            job.eta = secs
        else:
            job.eta = None
        return True

    @staticmethod
    def _progress(job, payload):
        parts = payload.split("|", 8)
        if len(parts) < 9:
            return
        done, total, est, speed, eta, vcodec, idx, n, title = parts
        done, total, est, speed, eta = map(core.to_num, (done, total, est, speed, eta))
        total = total or est
        job.status = "downloading"
        if title and title != "NA" and (job.title == job.url or job.opts["playlist"]):
            job.title = title
        if core.to_num(n):
            job.item, job.items = int(core.to_num(idx) or 0), int(core.to_num(n))
        if job.opts["mode"] == "audio":
            job.stage = "Downloading audio"
        else:
            job.stage = "Downloading audio track" if vcodec == "none" else "Downloading video"
        job.downloaded, job.total, job.speed, job.eta = done, total, speed, eta
        if total and done is not None:
            job.progress = min(100.0, done * 100 / total)


# --------------------------------------------------------------------------- info

_info_cache = {}  # url -> (time, raw -J output), reused by downloads
_info_lock = threading.Lock()


def cached_info(url):
    with _info_lock:
        hit = _info_cache.get(url)
    if hit and time.time() - hit[0] < INFO_CACHE_SECONDS:
        return hit[1]
    return None


def fetch_info(url, playlist):
    ytdlp = core.tool_path("yt-dlp")
    if not ytdlp:
        raise RuntimeError("Setup isn't finished yet.")
    cmd = [ytdlp, "-J", "--no-warnings", "--encoding", "utf-8"]
    cmd += ["--flat-playlist", "--yes-playlist"] if playlist else ["--no-playlist"]
    r = subprocess.run(cmd + ["--", url], capture_output=True, creationflags=core.NO_WINDOW,
                       env=core.subprocess_env(), timeout=120)
    if r.returncode != 0:
        lines = r.stderr.decode("utf-8", "replace").strip().splitlines() or ["Unknown error"]
        raise RuntimeError(lines[-1].replace("ERROR: ", "", 1))
    raw = r.stdout
    info = json.loads(raw.decode("utf-8", "replace"))
    has_list = "list=" in url
    if info.get("_type") != "playlist":
        with _info_lock:
            _info_cache[url] = (time.time(), raw)
            for k in [k for k, (t, _) in _info_cache.items() if time.time() - t > INFO_CACHE_SECONDS]:
                _info_cache.pop(k, None)

    if info.get("_type") == "playlist":
        entries = info.get("entries") or []
        thumbs = info.get("thumbnails") or []
        first_thumb = None
        for e in entries:
            if e.get("thumbnails"):
                first_thumb = e["thumbnails"][-1].get("url")
                break
        return {
            "kind": "playlist", "url": url, "title": info.get("title"),
            "uploader": info.get("uploader") or info.get("channel"),
            "count": info.get("playlist_count") or len(entries),
            "thumbnail": (thumbs[-1]["url"] if thumbs else first_thumb),
            "entries": [{"title": e.get("title"), "duration": e.get("duration")} for e in entries[:50]],
            "duration": sum(e.get("duration") or 0 for e in entries) or None,
            "has_playlist": True,
        }

    formats = info.get("formats") or []
    duration = info.get("duration") or 0
    videos = [f for f in formats if f.get("height") and f.get("vcodec") not in (None, "none")]
    audios = [f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]

    def size(f):
        if f.get("filesize") or f.get("filesize_approx"):
            return f.get("filesize") or f.get("filesize_approx")
        if f.get("tbr") and duration:
            return f["tbr"] * 1000 / 8 * duration
        return None

    audio_size = max((size(a) or 0 for a in audios if a.get("ext") == "m4a"), default=0) or \
        max((size(a) or 0 for a in audios), default=0)
    heights = sorted({f["height"] for f in videos}, reverse=True)
    sizes = {}
    for h in QUALITY_HEIGHTS:
        cand = [f for f in videos if f["height"] <= h]
        if not cand:
            continue
        top = max(f["height"] for f in cand)
        s = [size(f) for f in cand if f["height"] == top and size(f)]
        if s:
            sizes[h] = int(min(s) + audio_size)
    return {
        "kind": "video", "url": url, "id": info.get("id"), "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"), "duration": duration or None,
        "thumbnail": info.get("thumbnail"), "view_count": info.get("view_count"),
        "heights": heights, "sizes": sizes, "has_playlist": has_list,
    }


# --------------------------------------------------------------------------- desktop helpers

_dialog_lock = threading.Lock()


def pick_folder(initial):
    with _dialog_lock:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            raise RuntimeError("Folder picker isn't available — type the path instead.")
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            path = filedialog.askdirectory(parent=root, initialdir=initial or str(Path.home()),
                                           title="Choose where to save downloads")
        finally:
            root.destroy()
        return str(Path(path)) if path else None


def reveal(path):
    p = Path(path)
    if core.IS_WIN:
        if p.is_file():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            p.mkdir(parents=True, exist_ok=True)
            os.startfile(str(p))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R" if p.is_file() else "", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent if p.is_file() else p)])


def tool_versions():
    out = {}
    for name, args in (("yt-dlp", ["--version"]), ("ffmpeg", ["-version"]),
                       ("aria2c", ["--version"]), ("deno", ["--version"])):
        path = core.tool_path(name)
        if not path:
            out[name] = None
            continue
        try:
            r = subprocess.run([path] + args, capture_output=True, text=True, timeout=15,
                               creationflags=core.NO_WINDOW)
            first = (r.stdout or r.stderr).strip().splitlines()[0]
            out[name] = first.replace("ffmpeg version ", "").split(" Copyright")[0][:40]
        except Exception:
            out[name] = "installed"
    return out


# --------------------------------------------------------------------------- HTTP

class Server(ThreadingHTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets two processes share a port, so a second
    # YTGuy could silently run alongside an old one. Claim the port exclusively.
    allow_reuse_address = not core.IS_WIN

    def server_bind(self):
        if core.IS_WIN and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class State:
    settings = Settings()
    setup = Setup()
    jobs = None
    last_seen = time.time()
    server = None


State.jobs = JobManager(State.settings)


class Handler(BaseHTTPRequestHandler):
    server_version = "YTGuy"

    def log_message(self, fmt, *args):
        pass

    # ----- helpers
    def _host_ok(self):
        port = self.server.server_address[1]
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _origin_ok(self):
        return self.headers.get("Origin") in ALLOWED_ORIGINS

    def _cors_headers(self):
        if self._origin_ok():
            self.send_header("Access-Control-Allow-Origin", self.headers["Origin"])
            self.send_header("Vary", "Origin")

    def do_OPTIONS(self):  # CORS preflight from the GitHub Pages site
        if not self._host_ok() or not self._origin_ok():
            return self._send(403, {"error": "forbidden"})
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    # ----- GET
    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        path = self.path.split("?", 1)[0]
        if path == "/api/ping":
            return self._send(200, {"app": core.APP_NAME, "version": core.APP_VERSION})
        if path.startswith("/api/"):
            return self._api("GET", path)
        if path == "/":
            path = "/index.html"
        f = (WEB_DIR / path.lstrip("/")).resolve()
        if WEB_DIR.resolve() not in f.parents or not f.is_file():
            return self._send(404, {"error": "not found"})
        data = f.read_bytes()
        if f.name == "index.html":
            data = data.replace(b"{{TOKEN}}", TOKEN.encode())
        self._send(200, data, STATIC_TYPES.get(f.suffix, "application/octet-stream"))

    def do_POST(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        self._api("POST", self.path.split("?", 1)[0])

    # ----- API
    def _api(self, method, path):
        # Allowed: the local page (has the per-session token) or the GitHub Pages
        # site (browsers set Origin themselves, so other sites can't fake it).
        if not (secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN) or self._origin_ok()):
            return self._send(403, {"error": "forbidden"})
        State.last_seen = time.time()
        try:
            body = self._json() if method == "POST" else {}
            result = self._route(method, path, body)
            if result is None:
                return self._send(404, {"error": "unknown endpoint"})
            self._send(200, result)
        except Exception as e:
            self._send(400, {"error": str(e)})

    def _route(self, method, path, b):
        if method == "GET":
            if path == "/api/state":
                return {"settings": State.settings.get(), "setup": State.setup.status(),
                        "jobs": State.jobs.list(), "version": core.APP_VERSION,
                        "platform": "windows" if core.IS_WIN else sys.platform}
            if path == "/api/versions":
                return tool_versions()
            return None

        if path == "/api/settings":
            return State.settings.update(b)
        if path == "/api/setup":
            State.setup.start()
            return State.setup.status()
        if path == "/api/update":
            if core.tool_path("yt-dlp") and Path(core.tool_path("yt-dlp")).parent != core.BIN_DIR:
                raise RuntimeError("yt-dlp is installed outside the app — update it with 'yt-dlp -U'.")
            State.setup.start(only=["yt-dlp"])
            return State.setup.status()
        if path == "/api/info":
            url = str(b.get("url", "")).strip()
            if not url:
                raise RuntimeError("Paste a link first.")
            return fetch_info(url, bool(b.get("playlist")))
        if path == "/api/download":
            urls = [u for u in b.get("urls", []) if isinstance(u, str) and u.strip()]
            if not urls:
                raise RuntimeError("Paste a link first.")
            s = State.settings.get()
            height = b.get("height", s["height"])
            opts = {
                "mode": "audio" if b.get("mode", s["mode"]) == "audio" else "video",
                "height": int(height) if height else None,
                "audio_format": b.get("audio_format", s["audio_format"]),
                "bitrate": str(b.get("bitrate", s["bitrate"])),
                "prefer_h264": bool(b.get("prefer_h264", s["prefer_h264"])),
                "embed_thumbnail": bool(b.get("embed_thumbnail", s["embed_thumbnail"])),
                "playlist": bool(b.get("playlist", s["playlist"])),
                "out_dir": s["out_dir"],
                "fast_mode": bool(s.get("fast_mode", True)),
            }
            if opts["audio_format"] not in core.AUDIO_FORMATS:
                raise RuntimeError("Unknown audio format.")
            if opts["bitrate"] not in core.BITRATES:
                raise RuntimeError("Unknown bitrate.")
            meta = b.get("meta") or {}
            added = [State.jobs.add(u.strip(), opts, meta if len(urls) == 1 else {}) for u in urls]
            return {"ids": [j.id for j, _ in added], "added": sum(new for _, new in added),
                    "duplicates": sum(not new for _, new in added)}
        if path == "/api/cancel":
            State.jobs.cancel(b.get("id"))
            return {"ok": True}
        if path == "/api/retry":
            State.jobs.retry(b.get("id"))
            return {"ok": True}
        if path == "/api/remove":
            State.jobs.remove(b.get("id"))
            return {"ok": True}
        if path == "/api/clear":
            State.jobs.clear_finished()
            return {"ok": True}
        if path == "/api/pick-folder":
            chosen = pick_folder(State.settings.get()["out_dir"])
            if chosen:
                State.settings.update({"out_dir": chosen})
            return {"out_dir": State.settings.get()["out_dir"], "changed": bool(chosen)}
        if path == "/api/reveal":
            target = b.get("path")
            allowed = {f for j in State.jobs.list() for f in j["files"]}
            if target not in allowed:
                target = State.settings.get()["out_dir"]
            reveal(target)
            return {"ok": True}
        if path == "/api/quit":
            State.jobs.cancel_all()
            threading.Thread(target=State.server.shutdown, daemon=True).start()
            return {"ok": True}
        return None


# --------------------------------------------------------------------------- main

def running_instance(port):
    """Return the ping reply of a YTGuy already on this port, else None."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/ping", timeout=1.5) as r:
            info = json.loads(r.read())
            return info if info.get("app") == core.APP_NAME else None
    except Exception:
        return None


def replace_old_instance(port):
    """Ask an older YTGuy on this port to quit so this (newer) copy can take over."""
    req = urllib.request.Request(
        f"http://{HOST}:{port}/api/quit", data=b"{}", method="POST",
        headers={"Content-Type": "application/json", "Origin": next(iter(ALLOWED_ORIGINS))})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        return False
    for _ in range(30):  # wait up to ~6 s for the port to free up
        time.sleep(0.2)
        try:
            return Server((HOST, port), Handler)
        except OSError:
            pass
    return False


def idle_watchdog():
    while True:
        time.sleep(30)
        busy = State.jobs.active() or State.setup.status()["running"]
        if not busy and time.time() - State.last_seen > IDLE_EXIT_SECONDS:
            State.server.shutdown()
            return


def main():
    no_browser = "--no-browser" in sys.argv
    ports = list(PORTS)
    for a in sys.argv[1:]:
        if a.startswith("--port="):
            ports = [int(a.split("=", 1)[1])]
    server = None
    for port in ports:
        try:
            server = Server((HOST, port), Handler)
            break
        except OSError:
            other = running_instance(port)
            if not other:
                continue
            if other.get("version") == core.APP_VERSION:
                if not no_browser:
                    webbrowser.open(f"http://{HOST}:{port}/")
                return
            replacement = replace_old_instance(port)  # an older version is running
            if replacement:
                server = replacement
                break
    if server is None:
        server = Server((HOST, 0), Handler)
    State.server = server
    url = f"http://{HOST}:{server.server_address[1]}/"
    if sys.stdout:  # None in the windowless .exe build
        print(f"{core.APP_NAME} is running at {url}  (press Ctrl+C or use Quit in the app to stop)",
              flush=True)
    threading.Thread(target=idle_watchdog, daemon=True).start()
    if not no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        State.jobs.cancel_all()


if __name__ == "__main__":
    main()
