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

## Quick start

```bash
npm install
cp .env.example .env          # add your Battle.net client id/secret + DATABASE_URL

npm run db:init               # create tables
npm run ingest:static         # download all mounts/toys/achievements (do this after patches too)
npm run ingest:character area-52 yourcharacter   # cache one character

npm start                     # then open http://localhost:3000/api/character/area-52/yourcharacter
```

Get free API credentials at <https://develop.battle.net>.

## Roadmap (build order)

1. ✅ Static ingest + character lookup + progression % + "closest to finishing" (this scaffold)
2. Real frontend: dashboard, per-category bars, search
3. Guides + map pins from the open sources (`src/sources/`, `guides`/`locations` tables)
4. **Sign in with Battle.net** so users see their own full/private collection automatically
5. Account-wide rollups; Classic support; nightly achievement-detail backfill

## Attribution
Uses the Blizzard API (per Blizzard's API policy), and — where their data appears — All The Things
(MIT), Warcraft Wiki (CC BY-SA 4.0), and TrinityCore (GPLv3). Keep these credited in the UI.
