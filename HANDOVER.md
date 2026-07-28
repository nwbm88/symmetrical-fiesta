# Handover — livearchiver

**For: a Claude session running on the user's Windows PC.**
**Goal: get this app running flawlessly in `C:\Users\Owner\OneDrive\Desktop\sdf`.**

The app is written and extensively tested, but every test ran on **Linux in a
container**. Nothing has ever executed on real Windows, and YouTube downloads
were never completed because the build machine's IP is rate-limited and
bot-challenged by YouTube. Those two gaps are your job. Read
"What is NOT verified" before changing anything — most of the code is proven
and does not need revisiting.

---

## 1. What the app is

A desktop tool for building a personal archive of any band's live recordings.
It searches YouTube and the Internet Archive, groups duplicate uploads of the
same show so the user can pick the best version, downloads it, records where
and when it was filmed, extracts a lossless audio master, and cuts it into
individually named, tagged FLAC tracks.

Python 3.9+, PySide6 (Qt) desktop GUI, yt-dlp for YouTube, ffmpeg for audio.
~6,200 lines across 18 modules. There is also a full CLI.

## 2. Where the code is

- GitHub repo: **`nwbm88/symmetrical-fiesta`**
- Branch: **`claude/twenty-one-pilots-archiver-wmhulf`** ← all the work is here,
  *not* on the default branch
- Latest commit at handover: `ec81c78` "Handle cloud-synced folders and the
  Windows path limit"

```
git clone -b claude/twenty-one-pilots-archiver-wmhulf \
    https://github.com/nwbm88/symmetrical-fiesta.git .
```

(Or download the branch ZIP from GitHub and extract it.) `run.bat` must end up
directly inside `sdf`.

## 3. First job: get it running

1. Python 3.9+ from python.org — **tick "Add python.exe to PATH"**.
2. ffmpeg: *release full* zip from <https://www.gyan.dev/ffmpeg/builds/>.
   Either add its `bin` to PATH, or unzip so `sdf\ffmpeg\bin\ffmpeg.exe` exists.
   The app also looks in Program Files, winget, chocolatey and scoop locations.
3. Optional but genuinely helps YouTube: `winget install DenoLand.Deno`.
4. Double-click **`run.bat`**.

Then run **`livearchiver doctor`** (or `.venv\Scripts\python -m livearchiver.cli
doctor`) — it prints exactly what was found and what is missing.

---

## 4. What IS verified (don't re-litigate these)

All of this was tested against real files, real ffmpeg and real network:

- **Search** — live against YouTube and archive.org; date/venue parsing;
  duplicate grouping by artist + show date.
- **Download from archive.org** — a real 19-file show, including resume after
  interruption, per-file retry with mirror rotation, and mid-flight
  cancellation leaving no partial files.
- **The yt-dlp download path** — exercised end to end against a local HTTP
  server (format selection, progress with speed/ETA, cancellation, manifest
  writing, folder renaming). Only YouTube's own gatekeeping is untested.
- **Audio pipeline** — extract master, silence detection, cutting, FLAC
  tagging, cue sheet (frame-accurate MM:SS:FF), embedded album art,
  per-track and per-album ReplayGain. Verified with ffprobe.
- **Integrity** — SHA-256 checksums catch a single flipped byte, truncation
  and missing files; catalogue backups recover curation from a corrupt file.
- **GUI** — all four tabs, queue operations, timeline editor (waveform,
  undo/redo, keyboard nudging, snapping), thumbnails, preview dialog.
  Tested headless with `QT_QPA_PLATFORM=offscreen`.
- **`run.bat`** — all five branches under a real `cmd.exe` via Wine: no
  Python, venv creation failure, existing venv, failed import, successful
  windowed launch.

## 5. What is NOT verified — your actual work

### A. Real Windows execution (highest priority)
`run.bat` was only ever run under **Wine**, which is an approximation. Wine
lacks `where.exe` and `find.exe`, which already caused two real bugs during
development (both fixed by switching to methods needing nothing external).
Watch for:
- Whether `py -3` / `python` detection picks the right interpreter.
- Whether `pythonw.exe` launches the GUI detached and the console closes.
- Whether the venv relocation to `%LOCALAPPDATA%` fires correctly — it should,
  because `sdf` is under OneDrive. Confirm the run says
  `location: C:\Users\Owner\AppData\Local\livearchiver\venv`.
- UTF-8 output: `chcp 65001` is set, but check the ⚠/✓/▶ characters render.

### B. A complete YouTube download
Never achieved from the build machine. On the user's home connection it should
work. Test: search a band, queue one short video, let it finish, then confirm
the show folder, `show.json`, `checksums.json` and a split into tracks.

If it fails with **"Sign in to confirm you're not a bot"**: that is handled —
Settings → *Use cookies from browser* → pick the browser they watch YouTube in
(signed in). Also try `update.bat` (stale yt-dlp is the usual cause) and
installing Deno.

### C. Audio playback in the timeline
The build machine had no sound device. Logic, state transitions and the
ffplay fallback are verified, but no sound was ever produced. Open the
timeline on a split show and confirm Play actually makes noise. There is a
watchdog: if Qt claims to play but the playhead doesn't keep up, it switches
to ffplay automatically — check it does *not* trigger spuriously on a healthy
machine (it would show "Playing through ffplay" in the Play button tooltip).

### D. Qt Multimedia video in the preview dialog
Streaming preview needs `PySide6.QtMultimediaWidgets`. Confirm the video
actually renders and the scrub bar seeks.

---

## 6. Architecture map

| Module | Responsibility |
|---|---|
| `gui.py` (1446) | Qt app: Get Music / Collection / Artists / Settings tabs |
| `timeline.py` (836) | Waveform editor: custom-painted, markers, undo, keyboard |
| `download.py` (560) | yt-dlp + archive.org fetching, resume, retry, mirrors |
| `platformsupport.py` (343) | **Windows specifics** — start here for OS bugs |
| `preview.py` (372) | Thumbnail + streaming preview dialog |
| `jobs.py` (318) | Sequential job queue (download/split/extract/verify) |
| `cli.py` (305) | CLI incl. `doctor`, `verify`, `todo` |
| `catalog.py` (266) | Catalogue, grouping, artist stats, corrupt-file recovery |
| `audio.py` (262) | ffmpeg: master extraction, silence detection, cutting |
| `tagging.py` (209) | Cue sheet, album art, ReplayGain |
| `playback.py` (215) | Qt / ffplay audio backends with stall detection |
| `integrity.py` (188) | Checksums, verification, catalogue backups, disk space |
| `pipeline.py` (182) | Shared split logic used by both GUI and CLI |
| `showinfo.py` (176) | Date/venue extraction heuristics |
| `sources.py` (169) | Search backends (add new sites here) |
| `thumbnails.py` (161) | Disk-cached thumbnail fetching |
| `tracks.py` (160) | Tracklist detection: chapters/description/comments/setlist.fm |

**Key design decisions, so you don't undo them:**
- The queue runs **one job at a time** deliberately. Not a limitation.
- Tracks are always cut from `audio/full.flac`, never the original download,
  so re-splitting is cheap and non-destructive.
- Derived audio is deliberately **not** checksummed — it can be regenerated,
  and hashing gigabytes would make verification something users skip.
- All subprocess calls go through `platformsupport.popen_kwargs()` to get
  `CREATE_NO_WINDOW`. **If you add an ffmpeg call, use it**, or the user gets
  a black console flashing dozens of times per split.
- Filenames go through `platformsupport.safe_filename()` (Windows reserved
  names, trailing dots, illegal characters — e.g. `AC/DC` → `AC_DC`).

## 7. OneDrive-specific behaviour (already implemented)

`sdf` is inside OneDrive, which matters because concert downloads are
gigabytes each. Implemented and tested:
- App detects the synced folder, explains once on launch, offers to relocate
  the **collection** (the app itself can stay in `sdf`).
- `run.bat` builds the venv under `%LOCALAPPDATA%` — a virtualenv is thousands
  of small files that must not be in cloud sync.
- Name lengths are budgeted from the real collection root so the deepest file
  stays under Windows' 260-char `MAX_PATH`; the show date is preserved when
  trimming.

**Recommend the user keeps the collection outside OneDrive**, e.g.
`C:\Users\Owner\Music\LiveArchive` (Settings tab). If they insist on keeping it
in OneDrive, tell them to right-click it in Explorer → **"Always keep on this
device"**, or ffmpeg will hit online-only placeholders.

## 8. Diagnostics

```
livearchiver doctor      # ffmpeg / ffplay / JS runtime / versions
livearchiver verify      # check downloaded files against checksums
livearchiver todo        # shows still needing manual attention
livearchiver -v ...      # verbose logging on any command
```

If the GUI won't start, run it in console mode to see the traceback:
```
.venv\Scripts\python -m livearchiver.cli gui
```
(or `%LOCALAPPDATA%\livearchiver\venv\Scripts\python` if it relocated).

## 9. How this codebase was tested — please continue the same way

Every bug fixed during development was **reproduced by a test first**, then
fixed, then re-tested. Several bugs were found only by deliberately breaking
things: corrupting a byte, truncating a file, killing a download mid-flight,
feeding malformed JSON, closing a dialog while a worker ran. Please don't
declare something working because it looks right — exercise the failure path.

Be equally careful about the opposite: during development two "bugs" turned
out to be faulty *tests* (a stripped-tags scare and a batch-file failure that
was really a broken test harness). Verify which it is before changing code.

## 10. Known remaining gaps (not bugs — deliberate)

- **No audio fingerprinting** for true duplicate detection. Grouping by date
  catches most. Would need chromaprint. Discussed and deprioritised.
- **Multi-part uploads** ("Part 1 / Part 2") are not joined automatically.
- **No bandwidth limit** on downloads.
- **Search doesn't sweep per-year** systematically, which is how you'd
  actually reach completeness for a band.

Any of these are reasonable next features once the app is confirmed running.
