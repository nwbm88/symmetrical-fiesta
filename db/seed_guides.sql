-- Starter guide + location seed (run AFTER `npm run ingest:static`).
-- Looks collectibles up BY NAME so ids always match your own Blizzard data.
-- Guide text is original; coordinates are approximate community knowledge —
-- the TrinityCore importer replaces/expands these with authoritative spawns.

-- Helper pattern: insert guide + locations for one mount.
DO $$
DECLARE mid INTEGER;
BEGIN
  -- Invincible's Reins
  SELECT id INTO mid FROM mounts WHERE name = 'Invincible''s Reins';
  IF mid IS NOT NULL THEN
    INSERT INTO guides (collectible_type, collectible_id, body_md, source_name)
    VALUES ('mount', mid,
      E'**Weekly routine — 10 minutes.**\n1. Fly to Icecrown Citadel in Icecrown (Northrend).\n2. Set raid difficulty to **25 Heroic** before entering.\n3. Clear to The Lich King.\n4. Kill him; ~1% personal drop.\n\n*Once per character per week — bring alts.*',
      'Original guide')
    ON CONFLICT (collectible_type, collectible_id) DO NOTHING;
    INSERT INTO locations (collectible_type, collectible_id, npc_name, zone, map_id, coord_x, coord_y, note)
    SELECT 'mount', mid, 'The Lich King', 'Icecrown Citadel, Icecrown', 118, 75.2, 21.5, 'Raid entrance (approx.)'
    WHERE NOT EXISTS (SELECT 1 FROM locations WHERE collectible_type='mount' AND collectible_id=mid);
  END IF;

  -- Time-Lost Proto-Drake
  SELECT id INTO mid FROM mounts WHERE name = 'Time-Lost Proto-Drake';
  IF mid IS NOT NULL THEN
    INSERT INTO guides (collectible_type, collectible_id, body_md, source_name)
    VALUES ('mount', mid,
      E'**Camping a rare spawn** that shares a timer with Vyragosa in The Storm Peaks.\n1. Park an alt at a patrol point below.\n2. Keybind a targeting macro: `/tar Time-Lost`.\n3. It flies a fixed circuit — engage fast.',
      'Original guide')
    ON CONFLICT (collectible_type, collectible_id) DO NOTHING;
    INSERT INTO locations (collectible_type, collectible_id, npc_name, zone, map_id, coord_x, coord_y, note)
    SELECT 'mount', mid, 'Time-Lost Proto-Drake', 'The Storm Peaks', 120, x, y, n
    FROM (VALUES (35.0, 76.0, 'Bor''s Breath circuit (approx.)'),
                 (30.0, 66.0, 'Ulduar plateau circuit (approx.)'),
                 (49.0, 71.0, 'Brunnhildar circuit (approx.)')) AS v(x, y, n)
    WHERE NOT EXISTS (SELECT 1 FROM locations WHERE collectible_type='mount' AND collectible_id=mid);
  END IF;

  -- Mimiron's Head
  SELECT id INTO mid FROM mounts WHERE name = 'Mimiron''s Head';
  IF mid IS NOT NULL THEN
    INSERT INTO guides (collectible_type, collectible_id, body_md, source_name)
    VALUES ('mount', mid,
      E'**Weekly Ulduar run.**\n1. Enter Ulduar (The Storm Peaks) on 25-player.\n2. Activate **no Keepers** — the mount only drops from zero-Keeper Yogg-Saron.\n3. Enter portals in phase 2 to finish the fight.',
      'Original guide')
    ON CONFLICT (collectible_type, collectible_id) DO NOTHING;
    INSERT INTO locations (collectible_type, collectible_id, npc_name, zone, map_id, coord_x, coord_y, note)
    SELECT 'mount', mid, 'Yogg-Saron', 'Ulduar, The Storm Peaks', 120, 41.6, 17.8, 'Raid entrance (approx.)'
    WHERE NOT EXISTS (SELECT 1 FROM locations WHERE collectible_type='mount' AND collectible_id=mid);
  END IF;

  -- Ashes of Al'ar
  SELECT id INTO mid FROM mounts WHERE name = 'Ashes of Al''ar';
  IF mid IS NOT NULL THEN
    INSERT INTO guides (collectible_type, collectible_id, body_md, source_name)
    VALUES ('mount', mid,
      E'**Weekly Tempest Keep run.**\n1. Fly to Tempest Keep in Netherstorm (Outland).\n2. Clear The Eye to Kael''thas Sunstrider.\n3. ~2% drop, once per character per week.',
      'Original guide')
    ON CONFLICT (collectible_type, collectible_id) DO NOTHING;
    INSERT INTO locations (collectible_type, collectible_id, npc_name, zone, map_id, coord_x, coord_y, note)
    SELECT 'mount', mid, 'Kael''thas Sunstrider', 'Tempest Keep, Netherstorm', 109, 73.9, 63.6, 'Raid entrance (approx.)'
    WHERE NOT EXISTS (SELECT 1 FROM locations WHERE collectible_type='mount' AND collectible_id=mid);
  END IF;

  -- Reins of the Onyxian Drake
  SELECT id INTO mid FROM mounts WHERE name = 'Reins of the Onyxian Drake';
  IF mid IS NOT NULL THEN
    INSERT INTO guides (collectible_type, collectible_id, body_md, source_name)
    VALUES ('mount', mid,
      E'**Fastest weekly lockout in the game.**\n1. Onyxia''s Lair: Wyrmbog cave, Dustwallow Marsh.\n2. One boss, ~60 seconds at max level.\n3. ~1% drop — run it every week forever.',
      'Original guide')
    ON CONFLICT (collectible_type, collectible_id) DO NOTHING;
    INSERT INTO locations (collectible_type, collectible_id, npc_name, zone, map_id, coord_x, coord_y, note)
    SELECT 'mount', mid, 'Onyxia', 'Onyxia''s Lair, Dustwallow Marsh', 70, 52.2, 76.5, 'Lair entrance (approx.)'
    WHERE NOT EXISTS (SELECT 1 FROM locations WHERE collectible_type='mount' AND collectible_id=mid);
  END IF;
END $$;
