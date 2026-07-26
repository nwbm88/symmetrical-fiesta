-- WoW Collection Tracker schema
-- Design note: mounts & toys are ACCOUNT-WIDE in WoW; most achievements are PER-CHARACTER.
-- We model that distinction explicitly so "progression" numbers are correct.

-- ---------- Static reference data (bulk-loaded from Blizzard, refreshed on patches) ----------

CREATE TABLE IF NOT EXISTS achievements (
  id            INTEGER PRIMARY KEY,
  name          TEXT NOT NULL,
  category      TEXT,
  points        INTEGER DEFAULT 0,
  icon          TEXT,
  is_account_wide BOOLEAN DEFAULT FALSE,  -- Blizzard flags some achievements account-wide
  updated_at    TIMESTAMPTZ DEFAULT now()
);

-- Sub-steps of an achievement ("kill 100 murlocs" -> amount=100). Powers "closest to finishing".
CREATE TABLE IF NOT EXISTS achievement_criteria (
  id             INTEGER PRIMARY KEY,
  achievement_id INTEGER NOT NULL REFERENCES achievements(id) ON DELETE CASCADE,
  description    TEXT,
  amount         INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS mounts (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  source     TEXT,          -- e.g. "Drop: Invincible's Reins", filled from All The Things
  icon       TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS toys (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  source     TEXT,
  icon       TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);

-- Guide + map data from the OPEN sources (Warcraft Wiki / TrinityCore / All The Things).
-- collectible_type: 'mount' | 'toy' | 'achievement'
CREATE TABLE IF NOT EXISTS guides (
  id              SERIAL PRIMARY KEY,
  collectible_type TEXT NOT NULL,
  collectible_id  INTEGER NOT NULL,
  body_md         TEXT,          -- short guide text (yours, or CC BY-SA w/ attribution)
  source_name     TEXT,          -- for attribution: 'Warcraft Wiki', 'All The Things', etc.
  source_url      TEXT,
  UNIQUE (collectible_type, collectible_id)
);

-- NPC / rare / treasure locations. Coords are 0-100 map percentages (addon convention).
CREATE TABLE IF NOT EXISTS locations (
  id              SERIAL PRIMARY KEY,
  collectible_type TEXT NOT NULL,
  collectible_id  INTEGER NOT NULL,
  npc_name        TEXT,
  zone            TEXT,
  map_id          INTEGER,       -- Blizzard UiMapID, for rendering the right map
  coord_x         REAL,          -- 0-100
  coord_y         REAL,          -- 0-100
  note            TEXT
);
CREATE INDEX IF NOT EXISTS idx_locations_lookup ON locations (collectible_type, collectible_id);

-- ---------- Per-user / per-character data (fetched on demand, cached) ----------

CREATE TABLE IF NOT EXISTS characters (
  id          SERIAL PRIMARY KEY,
  region      TEXT NOT NULL,
  realm       TEXT NOT NULL,     -- realm slug
  name        TEXT NOT NULL,
  last_synced TIMESTAMPTZ,       -- used to throttle re-fetches (respect API rate limits)
  UNIQUE (region, realm, name)
);

-- One row per collectible per character. For account-wide things you can key on any
-- character of the account; keep it simple for the MVP.
-- kind: 'mount' | 'toy' | 'achievement'
CREATE TABLE IF NOT EXISTS character_progress (
  character_id   INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  kind           TEXT NOT NULL,
  collectible_id INTEGER NOT NULL,
  completed      BOOLEAN DEFAULT FALSE,
  current_amount INTEGER DEFAULT 0,   -- criteria progress so far
  required_amount INTEGER DEFAULT 1,  -- criteria total (for % done)
  PRIMARY KEY (character_id, kind, collectible_id)
);
CREATE INDEX IF NOT EXISTS idx_progress_char ON character_progress (character_id, completed);
