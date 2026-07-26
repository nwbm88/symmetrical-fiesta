// Enrich the bulk-loaded rows with per-item detail from Blizzard:
//   mounts  → source type ("DROP"/"VENDOR"/…) + flavor description
//   toys    → source type, item id
//   achievements → category, points, description, account-wide flag, icon,
//                  and the CRITERIA tree that powers "closest to finishing"
//
// Resumable by design: it only touches rows still missing data, in batches, so
// run it nightly (npm run refresh) and it chips away until everything is filled.
//   node src/ingest/enrichDetails.js [batchSize]     (default 500 per type)

import 'dotenv/config';
import { apiGet, NS } from '../blizzard/client.js';
import { query, pool } from '../db.js';

const BATCH = Number(process.argv[2]) || 500;

async function enrichMounts() {
  const { rows } = await query(
    `SELECT id FROM mounts WHERE source_type IS NULL ORDER BY id LIMIT $1`, [BATCH]);
  for (const { id } of rows) {
    const d = await apiGet(`/data/wow/mount/${id}`, NS.static);
    await query(
      `UPDATE mounts SET source_type = $2, description = $3,
              source = COALESCE(source, $4), updated_at = now() WHERE id = $1`,
      [id, d?.source?.type ?? 'UNKNOWN', d?.description ?? null, d?.source?.name ?? null]);
  }
  console.log(`Mounts enriched: ${rows.length}`);
}

async function enrichToys() {
  const { rows } = await query(
    `SELECT id FROM toys WHERE source_type IS NULL ORDER BY id LIMIT $1`, [BATCH]);
  for (const { id } of rows) {
    const d = await apiGet(`/data/wow/toy/${id}`, NS.static);
    await query(
      `UPDATE toys SET source_type = $2, item_id = $3,
              source = COALESCE(source, $4), updated_at = now() WHERE id = $1`,
      [id, d?.source?.type ?? 'UNKNOWN', d?.item?.id ?? null, d?.source?.name ?? null]);
  }
  console.log(`Toys enriched: ${rows.length}`);
}

// Flatten the (possibly nested) criteria tree into achievement_criteria rows.
function flattenCriteria(node, out = []) {
  if (!node) return out;
  for (const c of node.child_criteria ?? []) {
    out.push({ id: c.id, description: c.description ?? c.achievement?.name ?? null, amount: c.amount ?? 1 });
    flattenCriteria(c, out);
  }
  return out;
}

async function enrichAchievements() {
  const { rows } = await query(
    `SELECT id FROM achievements WHERE category IS NULL ORDER BY id LIMIT $1`, [BATCH]);
  for (const { id } of rows) {
    const d = await apiGet(`/data/wow/achievement/${id}`, NS.static);
    if (!d) { // gone from the game; mark so we don't refetch forever
      await query(`UPDATE achievements SET category = 'Unknown', updated_at = now() WHERE id = $1`, [id]);
      continue;
    }
    const media = await apiGet(`/data/wow/media/achievement/${id}`, NS.static);
    const icon = media?.assets?.find((a) => a.key === 'icon')?.value ?? null;
    await query(
      `UPDATE achievements SET category = $2, points = $3, description = $4,
              is_account_wide = $5, icon = $6, updated_at = now() WHERE id = $1`,
      [id, d.category?.name ?? 'Uncategorized', d.points ?? 0, d.description ?? null,
       d.is_account_wide ?? false, icon]);

    for (const c of flattenCriteria(d.criteria ? { child_criteria: [d.criteria] } : null)) {
      await query(
        `INSERT INTO achievement_criteria (id, achievement_id, description, amount)
         VALUES ($1, $2, $3, $4)
         ON CONFLICT (id) DO UPDATE SET description = EXCLUDED.description, amount = EXCLUDED.amount`,
        [c.id, id, c.description, c.amount]);
    }
  }
  console.log(`Achievements enriched: ${rows.length}`);
}

async function main() {
  await enrichMounts();
  await enrichToys();
  await enrichAchievements();
  const left = await query(`SELECT
    (SELECT count(*) FROM mounts WHERE source_type IS NULL) AS mounts,
    (SELECT count(*) FROM toys WHERE source_type IS NULL) AS toys,
    (SELECT count(*) FROM achievements WHERE category IS NULL) AS achievements`);
  console.log('Still to enrich:', left.rows[0]);
  await pool.end();
}

main().catch((e) => { console.error(e); process.exit(1); });
