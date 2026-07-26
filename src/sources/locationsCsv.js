// Bulk-import NPC/collectible locations from a CSV — the scale path for map pins.
// Works with any legally-sourced coordinate data: your own notes, TrinityCore
// world-DB exports converted to map coords, Warcraft Wiki (CC BY-SA, attribute!).
//
//   node src/sources/locationsCsv.js data/locations.csv
//
// CSV columns (header required):
//   kind,collectible,npc_name,zone,map_id,x,y,note
// `collectible` may be a numeric id OR an exact name (looked up per kind).
// x/y are in-game map coordinates 0-100 (the addon convention).
// See data/examples/locations.example.csv.

import 'dotenv/config';
import fs from 'node:fs/promises';
import { query, pool } from '../db.js';
import { parseCsv } from '../lib/csv.js';

const TABLES = { mount: 'mounts', toy: 'toys', achievement: 'achievements' };

async function resolveId(kind, collectible) {
  if (/^\d+$/.test(collectible)) return Number(collectible);
  const { rows } = await query(`SELECT id FROM ${TABLES[kind]} WHERE name = $1`, [collectible]);
  return rows[0]?.id ?? null;
}

async function main(file) {
  if (!file) { console.error('Usage: node locationsCsv.js <file.csv>'); process.exit(1); }
  const [header, ...rows] = parseCsv(await fs.readFile(file, 'utf8'));
  const col = Object.fromEntries(header.map((h, i) => [h.trim().toLowerCase(), i]));
  for (const need of ['kind', 'collectible', 'npc_name', 'zone', 'x', 'y'])
    if (!(need in col)) throw new Error(`CSV missing required column: ${need}`);

  let ok = 0, skipped = 0;
  for (const r of rows) {
    const kind = r[col.kind].trim().toLowerCase();
    if (!TABLES[kind]) { skipped++; continue; }
    const id = await resolveId(kind, r[col.collectible].trim());
    if (id == null) { console.warn(`No ${kind} named "${r[col.collectible]}" — skipped`); skipped++; continue; }
    await query(
      `INSERT INTO locations (collectible_type, collectible_id, npc_name, zone, map_id, coord_x, coord_y, note)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8)`,
      [kind, id, r[col.npc_name].trim(), r[col.zone].trim(),
       col.map_id != null && r[col.map_id] ? Number(r[col.map_id]) : null,
       Number(r[col.x]), Number(r[col.y]),
       col.note != null ? r[col.note].trim() || null : null]);
    ok++;
  }
  console.log(`Imported ${ok} locations (${skipped} skipped).`);
  await pool.end();
}

main(process.argv[2]).catch((e) => { console.error(e); process.exit(1); });
