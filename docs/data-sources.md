# Data sources (and why not Wowhead)

Wowhead has no public API and its guide/map content is copyrighted, so we don't scrape it.
Everything below is either official or openly licensed. **Store it all in your own DB** so the
site is fast and never depends on a third-party being up to date.

## 1. Blizzard Battle.net API — the backbone (official, free)

- **Game Data API** → full lists of every achievement, mount, and toy (names, icons, categories,
  criteria). This is the static reference data you bulk-load and refresh on patches.
- **Profile API** → a character's completed achievements, owned mounts/toys, and criteria progress.
- Get free credentials at <https://develop.battle.net>.
- Rate limits: ~100 req/s, 36,000 req/hour. Our client self-throttles and caches, so you stay under.
- Public data only via client-credentials. To read a user's *own* full/private collection, add
  **Sign in with Battle.net** (OAuth Authorization Code flow) later.

## 2. All The Things (ATT) — "how do I get this?" (MIT license)

- Repo: <https://github.com/ATTWoWAddon/AllTheThings>
- Maps every collectible to its **source** (drop, vendor, quest, achievement reward, etc.).
- MIT = free to reuse commercially; **keep ATT's attribution** visible somewhere.
- Import path: export ATT's Lua tables to JSON, then run `src/sources/allTheThings.js`.

## 3. TrinityCore world DB — NPC/rare spawn coordinates & maps (GPLv3)

- Repo: <https://github.com/TrinityCore/TrinityCore>
- The `creature` table has `map`, `position_x/y/z` for spawns — the raw material for map pins.
- You'll convert world coordinates → 0-100 map percentages (addon convention) per `UiMapID`.
- GPLv3 covers their *code*; reusing spawn coordinates as data is common practice, but if you
  redistribute their SQL wholesale, comply with the license. Loading coords into your own DB and
  attributing is the low-risk path.

## 4. Warcraft Wiki (wiki.gg) — guide text & locations (CC BY-SA 4.0)

- <https://warcraft.wiki.gg>
- Community wiki with detailed farming routes and locations.
- **CC BY-SA 4.0**: you may reuse and adapt text if you (a) attribute with a link and (b) release
  your adapted text under the same license. Store `source_name`/`source_url` in the `guides` table
  and show it. If you don't want share-alike obligations, write your own guide summaries instead
  and use the wiki only as reference.

## 5. wago.tools — the complete collectible database (shipped in `data/wago/`)

- <https://wago.tools> — free CSV exports of Blizzard's DB2 client tables (the same data
  Wowhead is built on). This is where the repo's complete database comes from:

| File | Table | What it holds |
|---|---|---|
| `Mount.csv` | `https://wago.tools/db2/Mount/csv` | every mount: name, journal source text, description |
| `Toy.csv` | `https://wago.tools/db2/Toy/csv` | every toy: toy id, item id, journal source text |
| `Achievement.csv` | `https://wago.tools/db2/Achievement/csv` | every achievement: title, description, points, flags |
| `Achievement_Category.csv` | `https://wago.tools/db2/Achievement_Category/csv` | category tree |
| `ToyNames.csv` | filtered from `https://wago.tools/db2/ItemSparse/csv` | toy item names (ItemSparse is ~50 MB; only the toy rows are kept) |

- **To refresh after a patch:** re-download the four tables above into `data/wago/`,
  regenerate `ToyNames.csv` by filtering ItemSparse to the ItemIDs present in `Toy.csv`,
  then run `npm run import:wago`.
- Licensing: this is Blizzard game data; using it in a non-commercial fan site with
  attribution is the same footing as the official API data. Credit wago.tools for the export.

## Attribution checklist

Add a footer/credits page listing: Blizzard (per their API policy), All The Things (MIT), Warcraft
Wiki (CC BY-SA 4.0, linked), and TrinityCore (GPLv3) wherever their data appears.
