# topsarchiver

A command-line tool for building a personal historical archive of **Twenty One
Pilots** live recordings. It searches **YouTube** and the **Internet Archive
(archive.org)**, groups duplicate uploads of the same show so you can pick the
best version, downloads your pick, records where and when it was filmed, then
extracts the audio and cuts it into individually named, tagged FLAC tracks.

## Requirements

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/) (with `ffprobe`) on your PATH
- `pip install -r requirements.txt` (pulls in `yt-dlp` and `requests`),
  or install the tool itself with `pip install .`

Optional: a free [setlist.fm API key](https://api.setlist.fm/docs/1.0/index.html)
for automatic track naming when a video has no chapters or timestamps.

## Desktop app

```
topsarchiver gui        # or: topsarchiver-gui
```

A native Qt desktop app (no browser, no HTML) with three tabs:

- **Get Music** — hit *Search sources* to query YouTube and archive.org in the
  background (add an optional extra search of your own, e.g. a specific show).
  Results appear grouped by show with every duplicate version side by side —
  source, length, quality, views — with the suggested best version marked.
  Filters: *Still to download* (what's missing from your collection), *only
  shows with multiple versions*, and free-text. Select versions and *Queue
  selected* — the **download queue runs strictly one at a time**, with a live
  progress bar. *Ignore selected version* hides duplicates you've rejected.
  Double-click any row to open the original page in your browser.
- **Collection** — everything you have: each show with its date, place,
  source link, uploader, the original description, and the track list with
  split status (split / detected-but-not-cut / not extracted yet). Buttons to
  *Extract & split tracks* (queued behind any running download, so only one
  heavy job runs at once), *Re-detect & split*, *Edit date/venue* when the
  automatic detection got it wrong, and *Open folder*.
- **Settings** — collection folder, setlist.fm API key, audio-only mode.

The GUI and CLI share the same `catalog.json` and collection folder — use
whichever you like, they stay in sync.

> Note: this tool is for personal archival use. Live recordings are still the
> band's/rights-holders' property — keep the archive private and support the
> band through official releases.

## Workflow

### 1. Search and build the catalog

```
topsarchiver search
```

Queries YouTube (several "full concert" searches, clips under 15 minutes are
filtered out) and the Internet Archive, and merges everything into
`catalog.json`. Re-running `search` only adds new finds — the catalog is
cumulative.

### 2. Pick versions — duplicates are grouped for you

```
topsarchiver list            # everything, grouped by show
topsarchiver list --dupes    # only shows that have multiple versions
topsarchiver list --new      # only shows you haven't downloaded yet
```

Recordings are grouped by **show date** (and venue when the date is unknown),
so multiple uploads of the same concert appear together:

```
2016-06-14 — Hallenstadion, Zurich  (3 versions)
  > [yt:AbCdEf12345] Twenty One Pilots FULL SHOW Zurich 2016 ...
       1:32:11 | youtube | 412,003 views  https://...
    [ia:top2016-06-14.flac16] Twenty One Pilots Live at Hallenstadion ...
       ?:?? | archive.org | FLAC  https://...
```

For each group you see duration, source, quality hints and popularity. `>`
marks the suggested best version (longest / lossless / most complete wins),
`*` marks versions you already downloaded — that's how double-ups are avoided.

### 3. Download

```
topsarchiver download yt:AbCdEf12345 ia:top2016-06-14.flac16
```

Each show gets its own folder, named by **date and place**:

```
collection/
  2016-06-14 - Hallenstadion Zurich/
    show.json           # provenance: date, venue, source URL, uploader, ...
    source.mkv          # the original download, kept for preservation
    source.info.json    # raw yt-dlp metadata (chapters, description, ...)
```

The date and place are extracted automatically from titles, descriptions and
archive.org metadata. If they can't be determined you get a warning — open
`show.json` and fill in `"show": {"date": ..., "venue": ...}` by hand; the
folder name and tags for that show come from there.

`--audio-only` skips the video stream if you only want the sound.

### 4. Extract audio and split into named tracks

```
topsarchiver split "collection/2016-06-14 - Hallenstadion Zurich" \
    --setlist-key YOUR_SETLISTFM_KEY
```

This extracts a lossless master (`audio/full.flac`) and then finds the
tracklist automatically, trying in order:

1. **YouTube chapters** — timestamps *and* song names, best case.
2. **Description tracklists** — parses `14:32 Heavydirtysoul`-style lines.
3. **setlist.fm** — looks up the setlist for the show's date to get the song
   names, and uses ffmpeg silence detection to find the cut points.
4. **Silence detection only** — cuts at quiet gaps with generic names
   (`Track 01`, …) for you to rename.

The result is one tagged FLAC per song:

```
audio/
  full.flac
  tracks.json                # the detected tracklist — editable
  01 - Heavydirtysoul.flac   # tagged: title, artist, album, track#, date,
  02 - Migraine.flac         #         "Recorded live at <venue>"
  ...
```

**Fixing mistakes is cheap:** the tracklist is saved to `tracks.json` before
cutting. Use `--dry-run` to preview it, edit titles or `start`/`end` seconds
by hand, then re-run `split` — tracks are always cut from the master, never
from the original download, so you can re-split as often as you like.
`--redetect` throws the file away and detects again; `--noise`/`--min-silence`
tune the silence detector (crowd noise between songs sometimes needs a higher
threshold like `--noise -30`).

## Avoiding double-ups

- The catalog is keyed by source + video/item id: re-searching never creates
  duplicate entries.
- Grouping by show date puts re-uploads of the same concert side by side
  before you download anything.
- Downloads are recorded in the catalog (`*` in `list`, and `--new` hides
  finished shows), so you always know what's still missing.

## Adding more sites

`topsarchiver/sources.py` defines one search function per site, all returning
the same record shape. To add a new site, write a `search_<site>()` function
and register it in the `SEARCHERS` dict at the bottom of that file.
