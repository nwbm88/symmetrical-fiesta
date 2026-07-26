// COMPLETE collectible database import from Blizzard's own client tables,
// served freely as CSV by https://wago.tools (data/wago/*.csv, committed to
// this repo so the site is fully self-contained):
//
//   Mount.csv                → every mount: name, journal source text, description
//   Toy.csv + ToyNames.csv   → every toy: id, item, name, journal source text
//   Achievement(.Category)   → every achievement: title, description, points,
//                              category path, account-wide flag
//
//   npm run import:wago      — load all of it into Postgres (no API keys needed)
//
// To refresh after a patch, re-download from wago.tools (URLs in
// docs/data-sources.md) and re-run. Blizzard's API ingest remains compatible:
// it upserts the same rows and adds icons/criteria on top.

import 'dotenv/config';
import fs from 'node:fs';
import { parseCsvObjects } from '../lib/csv.js';

const DIR = new URL('../../data/wago/', import.meta.url);
const read = (f) => parseCsvObjects(fs.readFileSync(new URL(f, DIR), 'utf8'));

// Strip WoW UI markup from journal text:
//   |cFFFFD200...|r color codes, |n newlines, |T<texture>|t icons (money icons
//   become g/s/c), leftover doubled spaces.
export function cleanSourceText(s) {
  if (!s) return null;
  const out = s
    .replace(/\|c[0-9a-fA-F]{8}/g, '')
    .replace(/\|r/g, '')
    .replace(/\|T[^|]*GOLD[^|]*\|t/gi, 'g')
    .replace(/\|T[^|]*SILVER[^|]*\|t/gi, 's')
    .replace(/\|T[^|]*COPPER[^|]*\|t/gi, 'c')
    .replace(/\|T[^|]*\|t/g, '')
    .replace(/\|n/g, ' · ')
    .replace(/\s+/g, ' ')
    .trim();
  return out || null;
}

export function parseMounts() {
  return read('Mount.csv').map((r) => ({
    id: Number(r.ID),
    name: r.Name_lang,
    source: cleanSourceText(r.SourceText_lang),
    description: r.Description_lang || null,
    source_type_enum: Number(r.SourceTypeEnum),
  })).filter((m) => m.name);
}

export function parseToys() {
  const names = new Map(read('ToyNames.csv').map((r) => [r.ItemID, r.Name]));
  return read('Toy.csv').map((r) => ({
    id: Number(r.ID),
    item_id: Number(r.ItemID),
    name: names.get(r.ItemID) ?? `Toy #${r.ID}`,
    source: cleanSourceText(r.SourceText_lang),
    source_type_enum: Number(r.SourceTypeEnum),
  }));
}

const ACHIEVEMENT_FLAG_ACCOUNT = 0x20000;
const STATISTICS_ROOT = 1;

export function parseAchievements() {
  const cats = new Map(read('Achievement_Category.csv')
    .map((r) => [Number(r.ID), { name: r.Name_lang, parent: Number(r.Parent) }]));

  const isStatistic = (catId) => {
    for (let id = catId, hops = 0; id !== -1 && hops < 20; hops++) {
      if (id === STATISTICS_ROOT) return true;
      id = cats.get(id)?.parent ?? -1;
    }
    return false;
  };
  const categoryPath = (catId) => {
    const parts = [];
    for (let id = catId, hops = 0; id !== -1 && hops < 20; hops++) {
      const c = cats.get(id);
      if (!c) break;
      parts.unshift(c.name);
      id = c.parent;
    }
    return parts.join(' > ') || 'Uncategorized';
  };

  return read('Achievement.csv')
    .filter((r) => r.Title_lang && !isStatistic(Number(r.Category)))
    .map((r) => ({
      id: Number(r.ID),
      name: r.Title_lang,
      description: r.Description_lang || null,
      points: Number(r.Points) || 0,
      category: categoryPath(Number(r.Category)),
      is_account_wide: (Number(r.Flags) & ACHIEVEMENT_FLAG_ACCOUNT) !== 0,
    }));
}

async function main() {
  const { query, pool } = await import('../db.js');
  const mounts = parseMounts(), toys = parseToys(), achievements = parseAchievements();

  for (const m of mounts) {
    await query(
      `INSERT INTO mounts (id, name, source, description, source_type)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (id) DO UPDATE SET
         name = EXCLUDED.name, source = EXCLUDED.source,
         description = COALESCE(mounts.description, EXCLUDED.description),
         source_type = COALESCE(mounts.source_type, EXCLUDED.source_type),
         updated_at = now()`,
      [m.id, m.name, m.source, m.description, String(m.source_type_enum)]);
  }
  for (const t of toys) {
    await query(
      `INSERT INTO toys (id, name, item_id, source, source_type)
       VALUES ($1, $2, $3, $4, $5)
       ON CONFLICT (id) DO UPDATE SET
         name = EXCLUDED.name, item_id = EXCLUDED.item_id, source = EXCLUDED.source,
         source_type = COALESCE(toys.source_type, EXCLUDED.source_type),
         updated_at = now()`,
      [t.id, t.name, t.item_id, t.source, String(t.source_type_enum)]);
  }
  for (const a of achievements) {
    await query(
      `INSERT INTO achievements (id, name, category, points, description, is_account_wide)
       VALUES ($1, $2, $3, $4, $5, $6)
       ON CONFLICT (id) DO UPDATE SET
         name = EXCLUDED.name, category = EXCLUDED.category, points = EXCLUDED.points,
         description = EXCLUDED.description, is_account_wide = EXCLUDED.is_account_wide,
         updated_at = now()`,
      [a.id, a.name, a.category, a.points, a.description, a.is_account_wide]);
  }
  console.log(`Imported ${mounts.length} mounts, ${toys.length} toys, ${achievements.length} achievements.`);
  await pool.end();
}

// --dry-run parses and reports without touching the database.
if (import.meta.url === `file://${process.argv[1]}`) {
  if (process.argv.includes('--dry-run')) {
    const m = parseMounts(), t = parseToys(), a = parseAchievements();
    console.log(`Parsed: ${m.length} mounts, ${t.length} toys, ${a.length} achievements (statistics excluded).`);
    console.log('Sample mount:', m.find((x) => x.name === 'Invincible') ?? m[0]);
    console.log('Sample toy:', t.find((x) => x.id === 378) ?? t[0]);
    console.log('Sample achievement:', a.find((x) => x.id === 2144) ?? a[0]);
  } else {
    main().catch((e) => { console.error(e); process.exit(1); });
  }
}
