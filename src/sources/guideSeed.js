// Load data/guides.seed.json into the guides + locations tables.
//   npm run db:seed          (run after ingest:static so name lookups resolve)
//
// Entries with a Blizzard `id` load directly; entries without one are resolved
// by their collection-journal name against your ingested data. Idempotent:
// re-running replaces each entry's guide text and location pins.

import 'dotenv/config';
import fs from 'node:fs';
import { query, pool } from '../db.js';

const TABLES = { mount: 'mounts', toy: 'toys', achievement: 'achievements' };

const file = process.argv[2] ?? new URL('../../data/guides.seed.json', import.meta.url);
const { entries } = JSON.parse(fs.readFileSync(file, 'utf8'));

let loaded = 0;
const unresolved = [];

for (const e of entries) {
  const table = TABLES[e.kind];
  if (!table) { unresolved.push(`${e.kind}? ${e.name}`); continue; }

  let id = e.id;
  if (id == null) {
    const { rows } = await query(`SELECT id FROM ${table} WHERE name = $1`, [e.name]);
    id = rows[0]?.id;
  }
  if (id == null) { unresolved.push(`${e.kind}: ${e.name}`); continue; }

  await query(
    `INSERT INTO guides (collectible_type, collectible_id, body_md, source_name, source_url)
     VALUES ($1, $2, $3, $4, $5)
     ON CONFLICT (collectible_type, collectible_id) DO UPDATE
       SET body_md = EXCLUDED.body_md, source_name = EXCLUDED.source_name,
           source_url = EXCLUDED.source_url`,
    [e.kind, id, e.guide, e.source_name ?? null, e.source_url ?? null]);

  await query(`DELETE FROM locations WHERE collectible_type = $1 AND collectible_id = $2`, [e.kind, id]);
  for (const l of e.locations ?? []) {
    await query(
      `INSERT INTO locations (collectible_type, collectible_id, npc_name, zone, map_id, coord_x, coord_y, note)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [e.kind, id, l.npc_name, l.zone, l.map_id ?? null, l.x, l.y, l.note ?? null]);
  }
  loaded++;
}

console.log(`Seeded ${loaded}/${entries.length} guides.`);
if (unresolved.length) {
  console.log('Unresolved (name not found — run ingest:static first, or add an id):');
  for (const u of unresolved) console.log('  -', u);
}
await pool.end();
