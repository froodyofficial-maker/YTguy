# YTGuy

A fast, good-looking YouTube downloader. Use it at **https://froodyofficial-maker.github.io/ytguy/**; the downloading happens on **your own PC**.

- **Video → MP4:** 4K (2160p), 1440p, 1080p, 720p, 480p, 360p, 240p, 144p
- **Audio:** MP3, M4A, Opus, FLAC, WAV at 320, 256, 192, 160, 128, 96 or 64 kbps
- A preview of each video with its thumbnail, length, the qualities it offers, and the estimated **file size for every option**
- Download **playlists**, or paste many links at once (one per line) to queue them
- A live downloads panel with speed, time left, cancel, retry and **Show in folder**
- Embeds the cover art, title and channel into the file
- Dark and light themes that work on any screen size. You can also drag a link onto the page or press Ctrl+V anywhere

## Website: https://froodyofficial-maker.github.io/ytguy/

The site is hosted on GitHub Pages. Browsers aren't allowed to download YouTube streams by themselves, so the
site works together with the small **YTGuy helper app** on your PC, which uses [yt-dlp](https://github.com/yt-dlp/yt-dlp)
and [FFmpeg](https://ffmpeg.org). Nothing is uploaded anywhere: the helper only listens on `127.0.0.1` and only
accepts requests from the YTGuy site and its own local page.

1. Open the website. If the helper isn't running, the site shows a **Download YTGuy.exe** button.
2. Run `YTGuy.exe`. If SmartScreen appears, click *More info → Run anyway*.
3. The website connects automatically. If your browser asks to **allow access to devices on your local network**, click **Allow**.

YTGuy.exe also opens its own copy of the page, which works without internet access to GitHub.

On first launch YTGuy offers to download its helper programs (about 170 MB, once) into
`%LOCALAPPDATA%\YTGuy\bin`: **yt-dlp**, **FFmpeg** and **Deno**. yt-dlp needs Deno to handle YouTube.

### Other ways to run it
- **Build it yourself:** install Python 3.9+ (tick *Add python.exe to PATH*), then double-click **`build.bat`**. The app ends up at `dist\YTGuy.exe`.
- **Run from source:** double-click **`start.bat`**, or run `python server.py`.

### How the site is published
- `.github/workflows/pages.yml` deploys the `web/` folder to GitHub Pages on every push to `main`.
- `.github/workflows/build.yml` builds `YTGuy.exe` on Windows and publishes it as the **latest** release, which the site's download button links to.

## Using it
1. Paste a YouTube link (video, Short or playlist).
2. Pick **Video** and a quality, or **Audio** with a format and bitrate.
3. Click **Download**. Files go to `Downloads\YTGuy`; you can change the folder.

To stop YTGuy, use **Settings → Quit YTGuy**. It also closes itself after 15 minutes with no browser tab open.

## Notes
- **4K and 1440p** come as VP9 video in an MP4 file, because YouTube only offers H.264 up to 1080p. They play in VLC, browsers, and the Windows Media Player app. If they don't play in the Photos app, install the free *VP9 Video Extensions* from the Microsoft Store.
- **Downloads suddenly fail?** Click **Settings → Update yt-dlp**. YouTube changes often.
- **SmartScreen warning:** the exe isn't code-signed. Click *More info → Run anyway*.
- **Security:** the helper only listens on `127.0.0.1` and rejects other host names. It accepts requests only from its own page, which carries a per-session token, or from the GitHub Pages site, so other websites can't control it.

## Project layout
```
server.py      local web server + download queue
core.py        yt-dlp command building and helper-tool installer
web/           the UI (index.html, app.css, app.js)
build.bat      builds YTGuy.exe with PyInstaller
start.bat      runs from source
```

> Only download videos you own or have permission to download, and respect YouTube's Terms of Service and copyright law.
