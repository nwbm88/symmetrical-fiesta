# wowdb

An offline, readable database of everything in retail World of Warcraft.

`wowdb` mirrors the DB2 client database tables that Blizzard ships with every
WoW build — published by [wago.tools](https://wago.tools) — and turns them into
a single SQLite file you can search, query and browse without a network
connection.

Verified against **Midnight, build 12.0.7.68887** (live retail, 23 Jul 2026):
**1,100 tables, 15.5 million rows, a 1.5 GB database built in under 8 minutes.**

```
python3 -m wowdb update       # download everything and build wow.db
python3 -m wowdb serve        # browse it at http://127.0.0.1:8000
```

No dependencies — the standard library only, Python 3.10+.

---

## What you get

`wow.db` has four layers, from raw to readable. All of them live in the same
file, so you can drop down a layer whenever the friendly one isn't enough.

### 1. Readable views

Plain-English views over the things people actually look up. Codes are already
resolved to names:

```sql
SELECT name, quality, item_level, class, subclass, slot
FROM items WHERE name LIKE '%Ashbringer%' ORDER BY item_level DESC;
```

```
Ashbringer            Artifact    48   Weapon  Sword  Two-Hand
Corrupted Ashbringer  Epic        30   Weapon  Sword  Two-Hand
Ashbringer            Legendary   29   Weapon  Sword  Two-Hand
```

The full set: `items`, `item_spells`, `spells`, `spell_effects`, `creatures`,
`achievements`, `zones`, `maps`, `dungeons`, `encounters`, `mounts`,
`battle_pets`, `toys`, `heirlooms`, `currencies`, `factions`, `classes`,
`races`, `specializations`, `professions`, `recipes`, `talents`, `pvp_talents`,
`quest_lines`, `quest_objectives`, `item_sets`, `transmog_sets`, `titles`,
`battlegrounds`, `lfg_dungeons`, `emote_commands`, `ui_maps`.

Run `python3 -m wowdb info` for row counts and descriptions.

### 2. Decoded tables — `v_<Table>`

Every DB2 table also gets a `v_` view with its integer codes translated:
enum columns gain a `_label`, bitmask columns gain a `_flags` list, and
foreign keys gain a `_name` resolved from the table they point at.

```sql
SELECT ID, Display_lang, OverallQualityID_label, InventoryType_label
FROM v_ItemSparse WHERE ID = 19019;
-- 19019 | Thunderfury, Blessed Blade of the Windseeker | Legendary | One-Hand
```

### 3. Raw DB2 tables

All 1,100 tables exactly as Blizzard ships them — `ItemSparse`, `SpellEffect`,
`Map`, `TraitNode` and the rest. Typed, indexed on `ID`, indexed on every
foreign key column.

### 4. The schema itself

The database describes itself, so you never need the internet to work out what
a column means:

| table | what's in it |
|---|---|
| `db2_table` | every table, its row count, and its display column |
| `db2_column` | every column, its type, and its foreign key target |
| `db2_enum` | integer code → label, per column |
| `db2_flag` | bitmask bit → label, per column |
| `wowdb_info` | which build, product, locale and scrape date this came from |
| `wowdb_view` | which readable views exist, and why any were skipped |

```sql
-- What does ItemSparse.Bonding = 4 mean?
SELECT label FROM db2_enum
WHERE table_name='ItemSparse' AND column_name='Bonding' AND value=4;
-- Quest Item
```

### Plus: search everything

An FTS5 index over every string in the database — item names, spell
descriptions, zone names, quest objectives, NPC titles, achievement text.

```
$ python3 -m wowdb search "Thunderfury"
ItemSparse    19019  Thunderfury, Blessed Blade of the Windseeker
                     Display_lang: Thunderfury, Blessed Blade of the Windseeker
SpellName     21992  Thunderfury
                     Name_lang: Thunderfury
```

---

## Commands

| command | what it does |
|---|---|
| `wowdb update` | scrape + build in one step — the usual way to refresh for a new patch |
| `wowdb scrape` | download every table to `data/` (CSV + schema JSON) |
| `wowdb build` | turn `data/` into `wow.db` |
| `wowdb search TERM` | full-text search from the terminal |
| `wowdb sql "SELECT ..."` | run a read-only query |
| `wowdb serve` | browse in a web browser |
| `wowdb info` | build, row counts, views, largest tables |
| `wowdb builds` | list builds available on wago.tools |

Run any of them as `python3 -m wowdb <command> --help`.

### The browser

`python3 -m wowdb serve` starts a local, offline web UI on `127.0.0.1:8000`:
search across everything, page through any table or view, and click through
foreign keys — from an item to the spell it casts to the effects that spell
applies. Each row page also lists what references it.

### Other builds and languages

```bash
python3 -m wowdb builds --product wowt          # what PTR builds exist
python3 -m wowdb update --product wowt          # build the PTR database
python3 -m wowdb update --build 12.0.7.68453    # an older build
python3 -m wowdb update --locale deDE --db wow-de.db
```

Products: `wow` (live retail), `wowt` (PTR), `wowxptr`, `wow_beta`, plus the
Classic lines (`wow_classic`, `wow_classic_era`, `wow_anniversary`, ...).
Locales: `enUS`, `deDE`, `frFR`, `esES`, `esMX`, `ptBR`, `ruRU`, `koKR`,
`zhCN`, `zhTW`, `itIT`.

### Options worth knowing

```bash
python3 -m wowdb scrape --tables ItemSparse Spell SpellEffect   # just a few tables
python3 -m wowdb scrape --workers 3                             # be gentler on wago
python3 -m wowdb build --no-fts                                 # skip search index
```

A scrape is resumable — re-run the same command after an interruption and it
picks up where it stopped. `--refresh` forces a re-download.

---

## Cost and footprint

Measured on build 12.0.7.68887:

| | |
|---|---|
| Download | 830 MB of CSV, 4 minutes at 6 workers |
| Build | 3 minutes |
| `wow.db` | 1.47 GB, of which the search index is 343 MB (`--no-fts` drops it) |

`data/` is only needed to build the database; delete it afterwards if you only
want `wow.db`.

---

## What is and isn't in here

This is the **client** database — everything the game client needs to know
locally. That covers items, spells, talents, achievements, maps, zones,
dungeons, mounts, pets, factions, currencies, professions and much more.

It does **not** include server-side data, because Blizzard never ships it:

- **Quest titles, descriptions and reward text.** Only `QuestLine` names,
  `QuestObjective` descriptions and quest IDs are client-side. (`QuestV2`
  really does only have three columns.)
- **Loot tables** — which boss drops what, and vendor inventories.
- **Creature spawn points, health and damage.**

For those you need a server project's database or a site like Wowhead. Anything
the client renders by itself is here.

Note that spell and item descriptions contain the game's formatting codes —
`$s1` for a scaling value, `|cFFFFD200...|r` for colour. They are stored
verbatim rather than guessed at.

---

## Tests

```
python3 -m unittest discover tests
```

They build a small fixture database from hand-written CSV, so they run offline
and in well under a second.

---

## Notes

- Data comes from [wago.tools](https://wago.tools), which is a community
  project. `--workers 6` is a reasonable default; please don't hammer it.
- Table and column names are Blizzard's own, matching the community
  [WoWDBDefs](https://github.com/wowdev/WoWDBDefs) definitions that wago uses,
  so anything you learn here transfers to other WoW data tools.
- DB2 schemas change every patch. Readable views declare the columns they
  need and are skipped — not failed — when a patch renames something. Check
  `SELECT * FROM wowdb_view WHERE status = 'skipped'` after a new patch.
- Blizzard owns the underlying game data; this is a tool for reading it.
