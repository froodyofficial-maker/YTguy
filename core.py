"""Shared logic: helper-tool management and yt-dlp command building."""

import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

APP_NAME = "YTGuy"
APP_VERSION = "2.0.0"
IS_WIN = os.name == "nt"
EXE = ".exe" if IS_WIN else ""
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "YTGuy"
BIN_DIR = APP_DIR / "bin"
SETTINGS_FILE = APP_DIR / "settings.json"

VIDEO_QUALITIES = [
    ("Best available", None),
    ("4K (2160p)", 2160),
    ("1440p (2K)", 1440),
    ("1080p (Full HD)", 1080),
    ("720p (HD)", 720),
    ("480p", 480),
    ("360p", 360),
    ("240p", 240),
    ("144p", 144),
]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "flac", "wav"]
LOSSLESS = {"flac", "wav"}
BITRATES = ["320", "256", "192", "160", "128", "96", "64"]

# Components the app needs. Downloaded on first run (Windows builds).
TOOLS = {
    "yt-dlp": {
        "exe": "yt-dlp" + EXE,
        "urls": ["https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"],
        "members": None,  # direct executable, not a zip
    },
    "ffmpeg": {
        "exe": "ffmpeg" + EXE,
        "urls": [
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        ],
        "members": ["ffmpeg.exe", "ffprobe.exe"],
    },
    "deno": {
        "exe": "deno" + EXE,
        "urls": ["https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip"],
        "members": ["deno.exe"],
    },
}

PROGRESS_TEMPLATE = (
    "download:[PROG]%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
    "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|"
    "%(info.vcodec)s|%(info.playlist_index)s|%(info.n_entries)s|%(info.title)s"
)


# --------------------------------------------------------------------------- helpers

def tool_path(name):
    """Return the path to a tool: our bundled copy first, then PATH."""
    local = BIN_DIR / TOOLS[name]["exe"]
    if local.exists():
        return str(local)
    return shutil.which(name)


def missing_tools():
    return [n for n in TOOLS if not tool_path(n)]


def subprocess_env():
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # Put our bin dir first so yt-dlp finds deno + ffmpeg.
    env["PATH"] = str(BIN_DIR) + os.pathsep + env.get("PATH", "")
    return env


def human_size(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_time(sec):
    if sec is None:
        return "--:--"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def to_num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def build_command(ytdlp, urls, out_dir, mode, height=None, prefer_h264=True,
                  audio_format="mp3", bitrate="320", playlist=False,
                  embed_thumbnail=True, filepath_log=None, temp_dir=None):
    """Build the yt-dlp command line. Pure function (easy to test)."""
    ffmpeg = tool_path("ffmpeg")
    cmd = [
        ytdlp,
        "--newline", "--no-colors", "--encoding", "utf-8",
        "--progress-template", PROGRESS_TEMPLATE,
        "--windows-filenames",
        "--embed-metadata",
        "-P", out_dir,
    ]
    if ffmpeg:
        cmd += ["--ffmpeg-location", str(Path(ffmpeg).parent)]

    if temp_dir:
        # Work in a private temp folder so a job never reuses/deletes files
        # that another download already saved in the output folder.
        cmd += ["-P", f"temp:{temp_dir}"]

    # Video names include the resolution, so 1080p and 4K copies can coexist.
    name = "%(title)s (%(height&{}p|best)s).%(ext)s" if mode == "video" else "%(title)s.%(ext)s"
    if playlist:
        cmd += ["--yes-playlist", "-o", "%(playlist_title|Downloads)s/%(playlist_index|0)03d - " + name]
    else:
        cmd += ["--no-playlist", "-o", name]

    if mode == "video":
        if height:
            cmd += ["-f", f"bv*[height<={height}]+ba/b[height<={height}]/bv*+ba/b"]
            sort = [f"res:{height}"]
        else:
            cmd += ["-f", "bv*+ba/b"]
            sort = ["res"]
        # H.264 + AAC plays in every Windows player. YouTube only offers
        # H.264 up to 1080p, so for 1440p/4K we take VP9 instead.
        if prefer_h264 and height and height <= 1080:
            sort += ["vcodec:h264", "fps", "acodec:aac"]
        else:
            # VP9 decodes far more smoothly than AV1 on most PCs at 1440p/4K.
            sort += ["vcodec:vp9", "fps", "acodec:aac"]
        cmd += ["-S", ",".join(sort), "--merge-output-format", "mp4",
                "--remux-video", "mp4"]
        if embed_thumbnail:
            cmd += ["--embed-thumbnail"]
    else:
        cmd += ["-f", "ba/b", "-x", "--audio-format", audio_format]
        if audio_format not in LOSSLESS:
            cmd += ["--audio-quality", f"{bitrate}K"]
        if embed_thumbnail and audio_format != "wav":
            cmd += ["--embed-thumbnail", "--convert-thumbnails", "jpg"]

    if filepath_log:
        # Record each finished file so the UI can reveal it in Explorer.
        cmd += ["--print-to-file", "after_move:%(filepath)s", str(filepath_log)]

    cmd += ["--"] + list(urls)
    return cmd


def download_file(url, dest, on_progress):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            on_progress(done, total)


def install_tool(name, on_progress):
    """Download a tool into BIN_DIR. on_progress(done, total)."""
    spec = TOOLS[name]
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    last_err = None
    for url in spec["urls"]:
        try:
            if spec["members"] is None:
                tmp = BIN_DIR / (spec["exe"] + ".part")
                download_file(url, tmp, on_progress)
                os.replace(tmp, BIN_DIR / spec["exe"])
                return
            with tempfile.TemporaryDirectory() as td:
                zpath = Path(td) / "pkg.zip"
                download_file(url, zpath, on_progress)
                with zipfile.ZipFile(zpath) as z:
                    for member in z.namelist():
                        base = member.rsplit("/", 1)[-1]
                        if base in spec["members"]:
                            with z.open(member) as src, open(BIN_DIR / base, "wb") as dst:
                                shutil.copyfileobj(src, dst)
            if (BIN_DIR / spec["exe"]).exists():
                return
            last_err = RuntimeError(f"{spec['exe']} not found in archive")
        except Exception as e:  # try next mirror
            last_err = e
    raise RuntimeError(f"Could not download {name}: {last_err}")


def kill_process_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    if IS_WIN:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                       creationflags=NO_WINDOW, capture_output=True)
    else:
        proc.terminate()
