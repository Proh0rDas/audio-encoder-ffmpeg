
# Audio Normalization to AAC (PyQt) — with Per‑File Progress & ETA

A tiny desktop app for batch‑converting media to **AAC** while keeping video/subtitles as you choose.  
Shows **per‑file progress**, **ETA**, and an **overall queue**. Includes robust fallbacks so you still see progress even when FFmpeg reports `out_time=N/A` for audio‑only inputs.

> If you're only converting audio (no video), FFmpeg sometimes returns `N/A` for `out_time` and even `speed`. This app handles that by switching to an indeterminate bar first and then estimating progress using output file size once available.

---

## ✨ Features

- Queue multiple files or a whole folder
- **Per‑file progress bar + ETA** (and a **queue** progress bar)
- Works with **audio‑only** (e.g., `.flac`, `.wav`, `.m4a`, `.mp3`, `.mka`) and **video** containers (e.g., `.mkv`, `.mp4`)
- Consistent AAC settings applied to **each audio stream**:
  - bitrate (`-b:a`), channels (`-ac`), sample rate (`-ar`), `-aac_coder twoloop`
- Keeps video/subtitles as `copy` by default (no re‑encode), configurable
- Transparent logs, plus a post‑convert probe to show actual output bitrates

---

## ✅ Prerequisites

- **Python** 3.8–3.12
- **FFmpeg** (with `ffprobe`) installed and available on `PATH`
  - Verify: `ffmpeg -version` and `ffprobe -version` should work in your terminal
- OS: Windows 10/11, macOS 12+, or recent Linux
  - Tested primarily on Windows; macOS/Linux should work the same

---

## 📦 Install

```bash
git clone https://github.com/Proh0rDas/audio-encoder-ffmpeg.git
cd audio-encoder-ffmpeg
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

If you don’t want a venv:

```bash
pip install PyQt5
```

---

## ▶️ Run

```bash
python audionormalizationeaac_v5.py
```

> Prefer **v5** for audio‑only workflows. If you only process video+audio and never see `N/A`, v3 is also fine.

---

## 🛠️ Usage

1. Click **Select Files** or **Select Directory**.
2. Choose your **output directory** (defaults to `converted/`).
3. Pick audio settings (bitrate, channels, sample rate).  
   Video/subtitle codecs default to **copy**.
4. Hit **Convert**.
5. Watch:
   - **Current file** progress + ETA
   - **Overall** queue progress
   - Detailed **log**

Outputs are written next to the app in the chosen output folder, preserving the original filenames.

---

## 📏 Progress & ETA details (audio‑only cases)

FFmpeg’s structured `-progress` output is parsed live. When processing **audio‑only**, FFmpeg may emit `out_time=N/A` (and sometimes `speed=N/A`). The app handles this by:

1. Switching the per‑file bar to **indeterminate** (a moving marquee) until more info is available.
2. Polling the on‑disk **output file size** to estimate processed seconds:
   \n`processed_seconds ≈ (bytes_written * 8) / target_bitrate`\n
3. Falling back to wall‑clock × speed if/when `speed` becomes available.
4. Using `out_time_ms` as soon as FFmpeg starts reporting it.

> Note: AAC `-b:a` is usually **ABR**, not strict CBR, so file‑size estimates are inherently approximate. The app clamps progress < 100% until the process exits.

---

## ⚙️ Configuration Notes

- **Audio**: bitrate (e.g., `224k`, `320k`, `640k`), channels (`1/2/6`), sample rate (`44100/48000/96000`)
- **Video**: `copy` (default), or re‑encode with `libx264` / `libx265`
- **Subtitles**: `copy` (default) or `mov_text` for MP4 compatibility
- **Metadata**: sets audio stream title to `"AAC Stereo"` by default

---

## 🧪 Supported Formats (examples)

- **Video containers**: `.mkv`, `.mp4`
- **Audio containers**: `.mka`, `.m4a`, `.mp3`
- **Raw/other audio**: `.flac`, `.wav`

> If your container has multiple audio streams (e.g., dubs), the same AAC settings are applied to **each** audio stream explicitly.

---

## 🧰 Troubleshooting

- **“FFmpeg/ffprobe not found”**  
  Install FFmpeg and ensure it’s on your `PATH`. Reopen the terminal/app after installation.
- **No progress showing at first**  
  Audio‑only often starts as **indeterminate**. Once enough data appears (file grows or FFmpeg reports time), the bar switches to % with ETA.
- **Progress/ETA looks “off”**  
  ABR and container overhead make size‑based estimation approximative. This improves once `out_time_ms` shows up.
- **UI seems to freeze**  
  This build uses a worker thread + non‑blocking reads, and drains stderr in the background to avoid deadlocks. If it still stalls, check antivirus or Controlled Folder Access rules blocking write/polling on the output directory.
- **Different final bitrates**  
  `-b:a` targets **average** bitrate. Actual bitrates can vary by content/encoder. The app probes and logs what was written per stream.

---

## 🧯 Known Limitations / Future Ideas

- Single‑process queue (no parallel transcodes yet)
- No per‑stream custom bitrates in the UI (applies the same settings across streams)
- Optional FDK‑AAC support could be added if you have an FFmpeg build with it enabled
- Pause/resume per file could be added in a future update

---

## 🧱 Packaging (optional)

Create a standalone EXE (Windows) using PyInstaller:

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed audionormalizationeaac_v5.py
# Your EXE will be in dist/
```

For macOS `.app`, use `--windowed` similarly. You may need to codesign/notarize for Gatekeeper.

---

## 🤝 Contributing

PRs and issues welcome. Keep changes platform‑agnostic when possible.

---

## 📜 License

MIT

