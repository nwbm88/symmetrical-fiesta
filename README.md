# livearchiver

A desktop tool for building a personal historical archive of **any band's**
live recordings. It searches **YouTube** and the **Internet Archive
(archive.org)**, groups duplicate uploads of the same show so you can pick the
best version, downloads your pick, records where and when it was filmed, then
extracts the audio and cuts it into individually named, tagged FLAC tracks.

## Installing on Windows

1. Install **Python 3.9 or newer** from <https://www.python.org/downloads/> —
   tick **"Add python.exe to PATH"** in the installer.
2. Install **ffmpeg**: grab the *release full* zip from
   <https://www.gyan.dev/ffmpeg/builds/>, unzip it, and either add its `bin`
   folder to PATH **or** drop the whole `ffmpeg` folder next to `run.bat` so
   that `ffmpeg\bin\ffmpeg.exe` sits beside it. The app looks in both places,
   plus the usual winget/chocolatey/scoop locations.
3. Double-click **`run.bat`**.

The first run builds a private Python environment in `.venv` and installs
what it needs — a minute or two, once. Every run after that just opens the
app. If Python or ffmpeg is missing, `run.bat` says so in plain English
instead of flashing a console and vanishing.

**`update.bat`** updates yt-dlp. Run it when YouTube downloads start
failing — that is nearly always the cause.

Downloads work without ffmpeg; extracting and splitting audio do not. The
Settings tab tells you which state you are in.

### macOS / Linux

```
./run.sh
```

Same idea: `python3`, `ffmpeg` (`brew install ffmpeg` / `apt install ffmpeg`),
then the script sets up `.venv` on first run.

### Manual install (any platform)

```
pip install -r requirements.txt     # yt-dlp, requests, PySide6
python -m livearchiver.cli gui
```

Optional: a free [setlist.fm API key](https://api.setlist.fm/docs/1.0/index.html)
for automatic track naming when a video has no chapters or timestamps.

### If YouTube says "sign in to confirm you're not a bot"

YouTube sometimes challenges a computer it doesn't recognise. In **Settings →
Use cookies from browser**, pick the browser you normally watch YouTube in
(sign in there first). Downloads and previews then reuse that browser's
session. Nothing is uploaded anywhere.

> Note: this tool is for personal archival use. Live recordings are still the
> artists'/rights-holders' property — keep the archive private and support the
> artists through official releases.

## Desktop app

```
livearchiver gui        # or: livearchiver-gui
```

A native Qt desktop app (no browser, no HTML) with four tabs:

- **Get Music** — type the **band/artist** at the top, hit *Search sources*
  to query YouTube and archive.org in the background (plus an optional extra
  search of your own, e.g. a specific venue or year). Results appear grouped
  by show with every duplicate version side by side — source, length,
  quality, views — with the suggested best version marked. Filters: a band
  dropdown, *Still to download*, *only shows with multiple versions*, and
  free-text. **Preview** a version before committing to it (see below), then
  *Queue selected* at whatever quality you choose.
  *Ignore selected version* hides duplicates you've rejected. Double-click
  any row to open the original page in your browser.
- **Artists** — every band in the catalog with its progress: shows found,
  downloaded, split, ignored, still to get, and whether it's finished. Mark a
  band done, filter the hub to it, search it again, or **remove it from the
  hub** entirely when you've lost interest — that clears it from the catalog
  and leaves any files already on disk alone.
- **Collection** — everything you have (all artists, one folder each): each
  show with its date, place, source link, uploader, the original description,
  and the track list with split status. Shows that still need a human get a
  **⚠ marking** — date or place unknown, tracks not identified, or no
  tracklist found — and a *Needs attention only* filter lists just those.
- **Settings** — collection folder, setlist.fm API key, default download
  quality, browser cookies, and whether ffmpeg was found.

## The download queue

The queue behaves like a download manager, not a fire-and-forget list. It
still runs **one job at a time** on purpose — a dozen parallel downloads of
multi-gigabyte concert video helps nobody — but you control it:

- **Pause / resume** the queue. Pausing stops *new* jobs starting; whatever
  is already downloading is left to finish.
- **Cancel** a download in flight. Partly-written files are deleted, so a
  cancelled download leaves nothing behind.
- **Remove** items, **retry** failed or cancelled ones, **reorder** with the
  up/down buttons, and **clear finished** or **clear all**.
- **Per-item quality**: best available, 1080p, 720p, 480p, or audio only.
  New items take the quality shown next to *Queue selected*; right-click any
  waiting item to change just that one.
- Right-click also offers cancel, retry, remove and *open source page*.
- Live progress with speed and ETA, and a status column showing exactly what
  each item is doing or why it failed.

Queueing something you already have — or already queued — is skipped rather
than duplicated.

## Previewing before you download

Select a version and press **Preview**. You get the thumbnail, the full
metadata, and the source link straight away. Press **Stream preview** and the
video plays *in the app* without anything being written to disk — yt-dlp
resolves a direct stream URL and Qt plays it.

The point is skipping around: drag the scrub bar, or use the **Skip to**
buttons (start / 10% / 25% / 50% / 75% / near end). Ten seconds at three
points in an hour-long upload tells you whether it's the whole show, a phone
recording from the back of the room, or a re-upload of something you already
have. If you like it, **Queue download** straight from the preview.

Streaming is a YouTube feature; for archive.org items use *Open in browser*,
which streams them directly. If a stream can't start, the thumbnail and
metadata are still there and downloading is unaffected.

The GUI and CLI share the same `catalog.json` and collection folder — use
whichever you like, they stay in sync.

## How it finds the date, the place, and the tracklist

When a show is downloaded, livearchiver stores the title, the full
description, **and the comments/reviews from the page** (top YouTube comments;
archive.org reviews and notes) in `show.json`, then mines all of it:

1. Date and place are parsed from the title and description; if either is
   still missing, every comment is scanned too — fans often post "I was
   there, June 14 2016 at the Hallenstadion!".
2. The tracklist is taken from (in order): YouTube **chapters**, a timestamp
   tracklist in the **description**, a timestamp tracklist **in the
   comments**, the **setlist.fm** setlist for that date (song names +
   silence detection for cut points), and finally silence detection alone
   with generic names.

When the heuristics come up empty, the show gets the ⚠ marking, and the
**"Find details in page text"** button opens the resolver: the whole wall of
text scraped from the page (title, description, every comment). Highlight the
right piece and click *Use selection as date*, *Use selection as place*, or
*Parse selection as tracklist* (understands `14:32 Song Name` lines) — then
re-split. Nothing is ever lost: tracks are always cut from the extracted
master, so re-splitting is cheap and repeatable.

## Timeline editor — when automation can't do it

Some shows have no chapters, no tracklist anywhere, and a crowd too loud for
silence detection. For those, **"Timeline editor"** in the Collection tab
opens the whole concert as a waveform, Audacity/Premiere style, and you cut
it by eye and ear:

```
double-click   add a cut marker        drag a marker   move it
right-click    delete a marker         click           seek / play from there
Ctrl+wheel     zoom                    wheel           pan
```

The table below the waveform lists the resulting songs with start, end and
length — double-click a title to name it, click a row to jump the playhead
there. **Detect silences** seeds candidate markers from the quiet gaps so you
start from a rough cut rather than nothing, then drag the ones that landed
wrong and delete the ones that aren't song boundaries. *Save tracklist*
writes the same `audio/tracks.json` the splitter uses, so *Save & queue split*
cuts exactly what you drew.

**Boost quiet audio** (on by default) scales the waveform display up to its
loudest peak. Audience recordings are often very quiet — at true scale they
draw as a flat line you can't read boundaries off. This affects the display
only; the audio itself is never touched.

The waveform is decoded once with ffmpeg and cached (`audio/peaks.json`), so
re-opening a long show is instant.

### Audio preview

Click anywhere on the waveform to seek, press Play to listen, click a row in
the track table to jump to that song — placing cuts by ear is usually faster
than by eye.

Playback uses Qt Multimedia when the machine has a working audio device, and
falls back to driving `ffplay` (bundled with ffmpeg) when it doesn't. The
fallback also kicks in automatically if Qt *claims* to be playing but the
playhead doesn't actually keep up — Qt reports success in that case and plays
nothing, which would otherwise leave you with a Play button that does nothing.
If neither backend is usable the button is disabled and its tooltip says why,
rather than looking available and staying silent.

### When the tracklist disagrees with the audio

A setlist posted in the comments sometimes belongs to a *different, longer*
upload of the same show. Before cutting, every tracklist is checked against
the real audio length: segments past the end are dropped and overshooting
ends are trimmed, with a plain-language warning naming each affected song
(both in the app and in `livearchiver split` output). The cuts that make
sense are still written — the split never dies half-way leaving a partial
track set behind.

## CLI

Everything works headless too, with `--artist` on search:

```
livearchiver search --artist "Twenty One Pilots"
livearchiver list --dupes            # duplicate versions grouped per show
livearchiver list --new              # only shows you don't have yet
livearchiver download yt:VIDEOID     # into collection/<Artist>/<date - venue>/
livearchiver split "collection/Twenty One Pilots/2016-06-14 - Hallenstadion"
livearchiver todo                    # every show still needing manual attention
```

`split --dry-run` previews the tracklist (written to an editable
`audio/tracks.json`); `--noise`/`--min-silence` tune the silence detector;
`--redetect` re-detects from scratch. `todo` prints the same ⚠ list the GUI
shows: missing dates/places, unidentified tracks, unsplit shows.

## Layout on disk

```
collection/
  Twenty One Pilots/
    2016-06-14 - Hallenstadion Zurich/
      show.json           # provenance: artist, date, place, source URL,
                          # uploader, description, captured comments
      source.mkv          # the original download, kept for preservation
      audio/
        full.flac         # lossless master extracted from the source
        tracks.json       # the detected tracklist — editable
        01 - Heavydirtysoul.flac   # tagged: title/artist/album/track#/date
        ...
```

## Avoiding double-ups

- The catalog is keyed by source + video/item id: re-searching never creates
  duplicate entries.
- Grouping by artist + show date puts re-uploads of the same concert side by
  side before you download anything; the suggested best version (longest /
  lossless / most complete) is marked.
- Downloads are recorded in the catalog (✓ in the app, `*` in the CLI), and
  ignored versions stay hidden — so you always know what's still missing.

## Adding more sites

`livearchiver/sources.py` defines one search function per site, all returning
the same record shape. To add a new site, write a `search_<site>()` function
and register it in the `SEARCHERS` dict at the bottom of that file.
