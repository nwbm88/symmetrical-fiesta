// Importer for "source" strings (how you obtain each mount/toy) from All The Things (MIT).
// https://github.com/ATTWoWAddon/AllTheThings
//
// ATT ships its database as Lua tables. The practical pipeline:
//   1. Clone the ATT repo (or download a release).
//   2. Convert the Lua collectible tables to JSON (a small Lua script, or an existing
//      community exporter). Store that JSON under data/att/.
//   3. Run this importer to map ATT's source info onto your mounts/toys rows.
//
// This file is a STUB showing the shape. Fill in parseAttJson() once you have the export.
// Remember: MIT requires you keep ATT's attribution somewhere user-visible.

import 'dotenv/config';
import fs from 'node:fs/promises';
import { query, pool } from '../db.js';

// Expected shape after your Lua->JSON export: [{ itemId, kind: 'mount'|'toy', source }]
async function parseAttJson(path) {
  const raw = await fs.readFile(path, 'utf8');
  return JSON.parse(raw);
}

async function main(path = 'data/att/collectibles.json') {
  const records = await parseAttJson(path);
  let n = 0;
  for (const r of records) {
    const table = r.kind === 'mount' ? 'mounts' : 'toys';
    await query(`UPDATE ${table} SET source = $1 WHERE id = $2`, [r.source, r.itemId]);
    n++;
  }
  console.log(`Applied ATT source info to ${n} collectibles.`);
  await pool.end();
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main(process.argv[2]).catch((e) => { console.error(e); process.exit(1); });
}
