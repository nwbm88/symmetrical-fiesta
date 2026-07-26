# WoW Collection Tracker

Self-hosted tracker for **achievements, mounts, and toys**. You enter a character, it shows your
progression, what you're **closest to finishing**, and easy guides with **maps & NPC locations** —
all served from data stored on your own machine, so it's fast and never shows an out-of-date page.

**No Wowhead scraping.** Data comes from the official Blizzard API plus openly licensed community
sources. See [`docs/data-sources.md`](docs/data-sources.md).

## What's here

```
db/schema.sql                 Postgres schema (mounts, toys, achievements, guides, locations, progress)
src/blizzard/                 OAuth + rate-limited API client
src/ingest/staticData.js      Bulk-load all mounts/toys/achievements into your DB (run on patches)
src/ingest/characterProgress  Fetch + cache one character's collection & achievement progress
src/logic/closest.js          Progression %, and the "closest to finishing" ranking
src/sources/allTheThings.js   Importer stub for collectible sources (All The Things, MIT)
src/api/server.js             JSON API + placeholder page
docs/                         data-sources.md, architecture.md
```

## Quick start — demo mode (zero setup)

```bash
npm install
npm run demo                  # open http://localhost:3000 — full UI on sample data
```

Demo mode needs no database and no API keys — use it to develop and restyle the
frontend instantly. The fixtures in `src/api/demoData.js` mirror the real API
shapes exactly.

## Quick start — real data

```bash
cp .env.example .env          # add your Battle.net client id/secret + DATABASE_URL

npm run db:init               # create tables (idempotent — also migrates old DBs)
npm run ingest:static         # download all mounts/toys/achievements (fast: index only)
npm run ingest:enrich         # fill sources, categories, points, criteria, icons (resumable)
npm run db:seed               # starter guides + map locations for famous mounts
npm run ingest:character area-52 yourcharacter   # cache one character

npm start                     # open http://localhost:3000 and track your character
npm test                      # 8 end-to-end API tests (run against demo mode)
```

Get free API credentials at <https://develop.battle.net>.

**Keep it fresh automatically** — `npm run refresh` re-pulls the static lists and
continues enrichment where it left off. Cron it nightly:

```cron
0 4 * * * cd /path/to/repo && npm run refresh >> refresh.log 2>&1
```

**Sign in with Battle.net** — register `PUBLIC_URL/auth/callback` as a Redirect URL
on your develop.battle.net client, then hit "Sign in with Battle.net" in the top
bar. You get your full character list as one-click chips plus an **Account total**
view (union of progress across all your characters). Demo mode fakes the login so
the whole flow is testable offline.

**Bulk map pins** — `npm run import:locations data/your.csv` loads NPC locations
from CSV (columns in `data/examples/locations.example.csv`; collectibles by id or
exact name). Drop zone images at `public/maps/<map_id>.jpg` and the guide maps use
them as backgrounds automatically — pins and coordinates work either way.

**Classic** — set `GAME_VERSION=classic` (or `classic1x`) in `.env`; use a separate
database per game version.

## Frontend

`public/` is a no-build vanilla JS app: character form → progression stat tiles →
tabs for **Closest to finishing / Missing mounts / Missing toys** → per-collectible
guide modal with step-by-step text and an SVG zone map with numbered NPC pins.
Light and dark theme via `prefers-color-scheme`. The map currently renders a
coordinate grid (0–100 in-game coords, addon convention); swapping in real map
tiles later changes nothing about the pin math.

## Status

1. ✅ Static ingest + character lookup + progression % + "closest to finishing"
2. ✅ Frontend dashboard (tiles, tabs, filter, guide modal with maps) + demo mode
3. ✅ Detail enrichment from Blizzard (sources, categories, points, criteria, icons)
   + CSV location importer + ATT importer stub + hand seed for famous mounts
4. ✅ Sign in with Battle.net (OAuth) → your character list + private profiles
5. ✅ Account-wide rollup · Classic env switch · nightly `refresh` · map-tile support
6. ✅ End-to-end API test suite (`npm test`)

**What grows from here is content, not code:** run `ingest:enrich` to completion,
feed `import:locations` with coordinate data, write/import guide text, and
optionally add zone images under `public/maps/`.

## Attribution
Uses the Blizzard API (per Blizzard's API policy), and — where their data appears — All The Things
(MIT), Warcraft Wiki (CC BY-SA 4.0), and TrinityCore (GPLv3). Keep these credited in the UI.
