// JSON API + static frontend.
//   npm start          — real mode (needs Postgres + Battle.net credentials)
//   npm run demo       — DEMO=1: serves fixture data, zero setup, for UI work
import 'dotenv/config';
import express from 'express';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const DEMO = process.env.DEMO === '1';
const REGION = process.env.REGION || 'us';
const __dirname = path.dirname(fileURLToPath(import.meta.url));

const app = express();
app.use(express.static(path.join(__dirname, '../../public')));

// Real-mode deps are imported lazily so demo mode never touches pg.
let db, syncCharacter, logic;
async function real() {
  db ??= await import('../db.js');
  ({ syncCharacter } = await import('../ingest/characterProgress.js'));
  logic ??= await import('../logic/closest.js');
}

async function findCharacter(realm, name) {
  const { rows } = await db.query(
    `SELECT id, last_synced FROM characters WHERE region=$1 AND realm=$2 AND name=$3`,
    [REGION, realm, name.toLowerCase()],
  );
  return rows[0];
}

app.get('/api/config', (_req, res) => res.json({ region: REGION, demo: DEMO }));

// Character overview: progression % + closest-to-finishing.
// Re-fetches from Blizzard only when stale (>6h) — respects rate limits.
app.get('/api/character/:realm/:name', async (req, res) => {
  const { realm, name } = req.params;
  if (DEMO) {
    const { demoCharacter } = await import('./demoData.js');
    return res.json({ ...demoCharacter, realm, name });
  }
  try {
    await real();
    let char = await findCharacter(realm, name);
    const stale = !char || !char.last_synced ||
      Date.now() - new Date(char.last_synced).getTime() > 6 * 3600_000;

    if (stale) { await syncCharacter(realm, name); char = await findCharacter(realm, name); }
    if (!char) return res.status(404).json({ error: 'Character not found or private.' });

    const [ach, mount, toy, closest] = await Promise.all([
      logic.overall(char.id, 'achievement'),
      logic.overall(char.id, 'mount'),
      logic.overall(char.id, 'toy'),
      logic.closestAchievements(char.id),
    ]);
    res.json({ realm, name, progression: { achievements: ach, mounts: mount, toys: toy }, closest });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// Uncollected mounts/toys with their source ("how do I get this?") + guide availability.
app.get('/api/character/:realm/:name/missing/:kind', async (req, res) => {
  const { realm, name, kind } = req.params;
  if (!['mount', 'toy'].includes(kind)) return res.status(400).json({ error: 'kind must be mount|toy' });
  if (DEMO) {
    const { demoMissing } = await import('./demoData.js');
    return res.json({ kind, missing: demoMissing[kind] });
  }
  try {
    await real();
    const char = await findCharacter(realm, name);
    if (!char) return res.status(404).json({ error: 'Character not synced yet.' });
    const table = kind === 'mount' ? 'mounts' : 'toys';
    const { rows } = await db.query(
      `SELECT c.id, c.name, c.source,
              (g.id IS NOT NULL OR EXISTS (
                 SELECT 1 FROM locations l
                  WHERE l.collectible_type=$2 AND l.collectible_id=c.id)) AS has_guide
         FROM ${table} c
         LEFT JOIN guides g ON g.collectible_type=$2 AND g.collectible_id=c.id
        WHERE c.id NOT IN (
                SELECT collectible_id FROM character_progress
                 WHERE character_id=$1 AND kind=$2 AND completed)
        ORDER BY has_guide DESC, c.name
        LIMIT 500`,
      [char.id, kind],
    );
    res.json({ kind, missing: rows });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// Guide + map pins for one collectible (served entirely from your local data).
app.get('/api/guide/:kind/:id', async (req, res) => {
  const { kind, id } = req.params;
  if (DEMO) {
    const { demoGuides } = await import('./demoData.js');
    return res.json(demoGuides[`${kind}:${id}`] ?? { guide: null, locations: [] });
  }
  try {
    await real();
    const [g, locs] = await Promise.all([
      db.query(`SELECT body_md, source_name, source_url FROM guides
                 WHERE collectible_type=$1 AND collectible_id=$2`, [kind, id]),
      db.query(`SELECT npc_name, zone, map_id, coord_x, coord_y, note FROM locations
                 WHERE collectible_type=$1 AND collectible_id=$2`, [kind, id]),
    ]);
    res.json({ guide: g.rows[0] ?? null, locations: locs.rows });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

const port = process.env.PORT || 3000;
app.listen(port, () =>
  console.log(`${DEMO ? '[DEMO] ' : ''}Listening on http://localhost:${port}`));
