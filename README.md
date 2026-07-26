# livearchiver

A desktop tool for building a personal historical archive of **any band's**
live recordings. It searches **YouTube** and the **Internet Archive
(archive.org)**, groups duplicate uploads of the same show so you can pick the
best version, downloads your pick, records where and when it was filmed, then
extracts the audio and cuts it into individually named, tagged FLAC tracks.

## Requirements

- Python 3.9+
- [ffmpeg](https://ffmpeg.org/) (with `ffprobe`) on your PATH
- `pip install -r requirements.txt` (pulls in `yt-dlp`, `requests`, `PySide6`),
  or install the tool itself with `pip install .`

Optional: a free [setlist.fm API key](https://api.setlist.fm/docs/1.0/index.html)
for automatic track naming when a video has no chapters or timestamps.

> Note: this tool is for personal archival use. Live recordings are still the
> artists'/rights-holders' property — keep the archive private and support the
> artists through official releases.

## Desktop app

```
livearchiver gui        # or: livearchiver-gui
```

A native Qt desktop app (no browser, no HTML) with three tabs:

- **Get Music** — type the **band/artist** at the top, hit *Search sources*
  to query YouTube and archive.org in the background (plus an optional extra
  search of your own, e.g. a specific venue or year). Results appear grouped
  by show with every duplicate version side by side — source, length,
  quality, views — with the suggested best version marked. Filters: *Still to
  download* (what's missing from your collection), *only shows with multiple
  versions*, and free-text. Select versions and *Queue selected* — the
  **download queue runs strictly one at a time**, with a live progress bar.
  *Ignore selected version* hides duplicates you've rejected. Double-click
  any row to open the original page in your browser.
- **Collection** — everything you have (all artists, one folder each): each
  show with its date, place, source link, uploader, the original description,
  and the track list with split status. Shows that still need a human get a
  **⚠ marking** — date or place unknown, tracks not identified, or no
  tracklist found — and a *Needs attention only* filter lists just those.
- **Settings** — collection folder, setlist.fm API key, audio-only mode.

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
