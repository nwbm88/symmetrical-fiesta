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

npm run db:init               # create tables
npm run ingest:static         # download all mounts/toys/achievements (do this after patches too)
npm run db:seed               # starter guides + map locations for famous mounts
npm run ingest:character area-52 yourcharacter   # cache one character

npm start                     # open http://localhost:3000 and track your character
```

Get free API credentials at <https://develop.battle.net>.

## Frontend

`public/` is a no-build vanilla JS app: character form → progression stat tiles →
tabs for **Closest to finishing / Missing mounts / Missing toys** → per-collectible
guide modal with step-by-step text and an SVG zone map with numbered NPC pins.
Light and dark theme via `prefers-color-scheme`. The map currently renders a
coordinate grid (0–100 in-game coords, addon convention); swapping in real map
tiles later changes nothing about the pin math.

## Roadmap (build order)

1. ✅ Static ingest + character lookup + progression % + "closest to finishing"
2. ✅ Frontend dashboard (tiles, tabs, guide modal with maps) + demo mode
3. Bulk guide/location content: run the importers in `src/sources/` (ATT export,
   TrinityCore spawn coords) instead of the small hand seed in `db/seed_guides.sql`
4. **Sign in with Battle.net** so users see their own full/private collection automatically
5. Account-wide rollups; Classic support; nightly achievement-detail backfill; real map tiles

## Attribution
Uses the Blizzard API (per Blizzard's API policy), and — where their data appears — All The Things
(MIT), Warcraft Wiki (CC BY-SA 4.0), and TrinityCore (GPLv3). Keep these credited in the UI.
