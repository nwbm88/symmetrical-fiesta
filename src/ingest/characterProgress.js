// Fetch one character's collection + achievement progress and cache it.
// Usage: node src/ingest/characterProgress.js <realmSlug> <characterName>
//   e.g. node src/ingest/characterProgress.js area-52 Mycharacter
//
// Only PUBLIC data is visible via client-credentials. For a user's own full collection,
// add "Sign in with Battle.net" (Authorization Code flow) later — see docs/architecture.md.

import 'dotenv/config';
import { apiGet, NS } from '../blizzard/client.js';
import { query, pool } from '../db.js';

const REGION = process.env.REGION || 'us';

async function upsertCharacter(realm, name) {
  const { rows } = await query(
    `INSERT INTO characters (region, realm, name) VALUES ($1, $2, $3)
     ON CONFLICT (region, realm, name) DO UPDATE SET last_synced = now()
     RETURNING id`,
    [REGION, realm, name.toLowerCase()],
  );
  return rows[0].id;
}

async function saveProgress(charId, kind, id, completed, current = 1, required = 1) {
  await query(
    `INSERT INTO character_progress
       (character_id, kind, collectible_id, completed, current_amount, required_amount)
     VALUES ($1, $2, $3, $4, $5, $6)
     ON CONFLICT (character_id, kind, collectible_id) DO UPDATE
       SET completed = EXCLUDED.completed,
           current_amount = EXCLUDED.current_amount,
           required_amount = EXCLUDED.required_amount`,
    [charId, kind, id, completed, current, required],
  );
}

async function syncCharacter(realm, name) {
  const charId = await upsertCharacter(realm, name);
  const base = `/profile/wow/character/${realm}/${name.toLowerCase()}`;

  // Mounts (account-wide) — endpoint returns only what the character/account owns.
  const mounts = await apiGet(`${base}/collections/mounts`, NS.profile);
  for (const m of mounts?.mounts ?? []) await saveProgress(charId, 'mount', m.mount.id, true);

  // Toys (account-wide).
  const toys = await apiGet(`${base}/collections/toys`, NS.profile);
  for (const t of toys?.toys ?? []) await saveProgress(charId, 'toy', t.toy.id, true);

  // Achievements — includes criteria progress, which drives "closest to finishing".
  const ach = await apiGet(`${base}/achievements`, NS.profile);
  for (const a of ach?.achievements ?? []) {
    const completed = Boolean(a.completed_timestamp);
    // criteria.child_criteria gives partial progress on multi-step achievements.
    const child = a.criteria?.child_criteria ?? [];
    const required = child.length || 1;
    const current = completed ? required : child.filter((c) => c.is_completed).length;
    await saveProgress(charId, 'achievement', a.id, completed, current, required);
  }

  console.log(`Synced ${name}-${realm}: ${mounts?.mounts?.length ?? 0} mounts, ` +
    `${toys?.toys?.length ?? 0} toys, ${ach?.achievements?.length ?? 0} achievements.`);
  return charId;
}

// Run directly from CLI
if (import.meta.url === `file://${process.argv[1]}`) {
  const [realm, name] = process.argv.slice(2);
  if (!realm || !name) { console.error('Usage: node characterProgress.js <realmSlug> <name>'); process.exit(1); }
  syncCharacter(realm, name).then(() => pool.end()).catch((e) => { console.error(e); process.exit(1); });
}

export { syncCharacter };
