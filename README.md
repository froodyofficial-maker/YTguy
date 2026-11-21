# YTGuy

A fast, good-looking YouTube downloader that runs **in your browser** and does the downloading on **your own PC**.

- **Video → MP4:** 4K (2160p), 1440p, 1080p, 720p, 480p, 360p, 240p, 144p
- **Audio:** MP3, M4A, Opus, FLAC, WAV at 320, 256, 192, 160, 128, 96 or 64 kbps
- A preview of each video with its thumbnail, length, the qualities it offers, and the estimated **file size for every option**
- Download **playlists**, or paste many links at once (one per line) to queue them
- A live downloads panel with speed, time left, cancel, retry and **Show in folder**
- Embeds the cover art, title and channel into the file
- Dark and light themes that work on any screen size. You can also drag a link onto the page or press Ctrl+V anywhere

## Why it isn't a hosted website
Browsers aren't allowed to download YouTube streams directly, so a website hosted on GitHub Pages can't do this.
YTGuy runs a tiny web server **on your computer only** (`127.0.0.1`), opens the page in your browser, and uses
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and [FFmpeg](https://ffmpeg.org) to download and convert. Nothing is uploaded anywhere.

## Get it (Windows)
**Easiest:** open the **Actions** tab → the latest **Build Windows app** run → download **YTGuy-windows** → unzip and run `YTGuy.exe`.

**Or build it yourself:**
1. Install Python 3.9+ from https://www.python.org/downloads/ and tick **"Add python.exe to PATH"**.
2. Double-click **`build.bat`**. The app ends up at `dist\YTGuy.exe`.

**Or run from source:** double-click **`start.bat`**, or run `python server.py`.

On first launch YTGuy offers to download its helper programs (about 100 MB, once) into
`%LOCALAPPDATA%\YTGuy\bin`: **yt-dlp**, **FFmpeg** and **Deno**. yt-dlp needs Deno to handle YouTube.

## Using it
1. Paste a YouTube link (video, Short or playlist).
2. Pick **Video** and a quality, or **Audio** with a format and bitrate.
3. Click **Download**. Files go to `Downloads\YTGuy`; you can change the folder.

To stop YTGuy, use **Settings → Quit YTGuy**. It also closes itself after 15 minutes with no browser tab open.

## Notes
- **4K and 1440p** come as VP9 video in an MP4 file, because YouTube only offers H.264 up to 1080p. They play in VLC, browsers, and the Windows Media Player app. If they don't play in the Photos app, install the free *VP9 Video Extensions* from the Microsoft Store.
- **Downloads suddenly fail?** Click **Settings → Update yt-dlp**. YouTube changes often.
- **SmartScreen warning:** the exe isn't code-signed. Click *More info → Run anyway*.
- **Security:** the server only listens on `127.0.0.1`, rejects other host names, and requires a per-session token, so other websites can't control it.

## Project layout
```
server.py      local web server + download queue
core.py        yt-dlp command building and helper-tool installer
web/           the UI (index.html, app.css, app.js)
build.bat      builds YTGuy.exe with PyInstaller
start.bat      runs from source
```

> Only download videos you own or have permission to download, and respect YouTube's Terms of Service and copyright law.
