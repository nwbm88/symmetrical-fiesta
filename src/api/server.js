// Minimal JSON API + a tiny demo page. Grow this into your real frontend.
import 'dotenv/config';
import express from 'express';
import { query } from '../db.js';
import { syncCharacter } from '../ingest/characterProgress.js';
import { overall, closestAchievements } from '../logic/closest.js';

const app = express();
const REGION = process.env.REGION || 'us';

async function findCharacter(realm, name) {
  const { rows } = await query(
    `SELECT id, last_synced FROM characters WHERE region=$1 AND realm=$2 AND name=$3`,
    [REGION, realm, name.toLowerCase()],
  );
  return rows[0];
}

// Look up a character; re-fetch from Blizzard only if stale (>6h) — respects rate limits.
app.get('/api/character/:realm/:name', async (req, res) => {
  try {
    const { realm, name } = req.params;
    let char = await findCharacter(realm, name);
    const stale = !char || !char.last_synced ||
      Date.now() - new Date(char.last_synced).getTime() > 6 * 3600_000;

    if (stale) { await syncCharacter(realm, name); char = await findCharacter(realm, name); }
    if (!char) return res.status(404).json({ error: 'Character not found or private.' });

    const [ach, mount, toy, closest] = await Promise.all([
      overall(char.id, 'achievement'),
      overall(char.id, 'mount'),
      overall(char.id, 'toy'),
      closestAchievements(char.id),
    ]);
    res.json({ realm, name, progression: { achievements: ach, mounts: mount, toys: toy }, closest });
  } catch (e) {
    console.error(e);
    res.status(500).json({ error: e.message });
  }
});

// Guides + map pins for one collectible (served from your local open-source data).
app.get('/api/guide/:kind/:id', async (req, res) => {
  const { kind, id } = req.params;
  const [g, locs] = await Promise.all([
    query(`SELECT body_md, source_name, source_url FROM guides
             WHERE collectible_type=$1 AND collectible_id=$2`, [kind, id]),
    query(`SELECT npc_name, zone, map_id, coord_x, coord_y, note FROM locations
             WHERE collectible_type=$1 AND collectible_id=$2`, [kind, id]),
  ]);
  res.json({ guide: g.rows[0] ?? null, locations: locs.rows });
});

app.get('/', (_req, res) => res.type('html').send(`<!doctype html>
<h1>WoW Collection Tracker</h1>
<p>Try <code>/api/character/area-52/somename</code></p>`));

const port = process.env.PORT || 3000;
app.listen(port, () => console.log(`Listening on http://localhost:${port}`));
