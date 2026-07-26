// Bulk-load the static game data (mounts, toys, achievements) into your DB.
// Run on setup and again after each content patch:  npm run ingest:static
//
// This is the "stored on my end so it's not constantly loading out-of-date sites" part.

import 'dotenv/config';
import { apiGet, NS } from '../blizzard/client.js';
import { query, pool } from '../db.js';

async function ingestMounts() {
  const index = await apiGet('/data/wow/mount/index', NS.static);
  console.log(`Mounts: ${index.mounts.length}`);
  for (const m of index.mounts) {
    await query(
      `INSERT INTO mounts (id, name) VALUES ($1, $2)
       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, updated_at = now()`,
      [m.id, m.name],
    );
  }
}

async function ingestToys() {
  const index = await apiGet('/data/wow/toy/index', NS.static);
  console.log(`Toys: ${index.toys.length}`);
  for (const t of index.toys) {
    await query(
      `INSERT INTO toys (id, name) VALUES ($1, $2)
       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, updated_at = now()`,
      [t.id, t.name],
    );
  }
}

async function ingestAchievements() {
  const index = await apiGet('/data/wow/achievement/index', NS.static);
  console.log(`Achievements: ${index.achievements.length} (storing index; enrich details lazily)`);
  for (const a of index.achievements) {
    await query(
      `INSERT INTO achievements (id, name) VALUES ($1, $2)
       ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, updated_at = now()`,
      [a.id, a.name],
    );
  }
  // NOTE: per-achievement points/category/criteria come from /data/wow/achievement/{id}.
  // That's ~15k extra calls, so enrich on demand (when a user opens an achievement) or
  // as a slow nightly backfill rather than all at once here.
}

async function main() {
  await ingestMounts();
  await ingestToys();
  await ingestAchievements();
  console.log('Static ingest complete.');
  await pool.end();
}

main().catch((e) => { console.error(e); process.exit(1); });
