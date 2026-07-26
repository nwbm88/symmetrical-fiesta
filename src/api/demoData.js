// Demo fixtures so the UI runs with ZERO setup (no Postgres, no API keys):
//   npm run demo
// Shapes mirror the real endpoints exactly, so the frontend can't drift from prod.
// Only the CHARACTER is fake. Everything else is the real local database:
//   - collectible names/sources/totals come from data/wago/ (Blizzard's own
//     client tables via wago.tools — the complete mount/toy/achievement DB)
//   - guides and map pins come from data/guides.seed.json
// Both are the exact files `npm run import:wago` / `npm run db:seed` load
// into Postgres, so demo mode and real mode share one source of truth.

import fs from 'node:fs';
import { parseMounts, parseToys, parseAchievements } from '../sources/wagoDb2.js';

const { entries: guideEntries } = JSON.parse(
  fs.readFileSync(new URL('../../data/guides.seed.json', import.meta.url), 'utf8'));

// Entries with a Blizzard id are addressable in demo mode (`/api/guide/kind/id`).
export const demoGuides = Object.fromEntries(
  guideEntries.filter((e) => e.id != null).map((e) => [`${e.kind}:${e.id}`, {
    guide: { body_md: e.guide, source_name: e.source_name ?? null, source_url: e.source_url ?? null },
    locations: (e.locations ?? []).map((l) => ({
      npc_name: l.npc_name, zone: l.zone, map_id: l.map_id ?? null,
      coord_x: l.x, coord_y: l.y, note: l.note ?? null,
    })),
  }]));

const hasGuide = (kind, id) => `${kind}:${id}` in demoGuides;

// The complete collectible database (same rows import:wago loads into Postgres).
const db = { mount: parseMounts(), toy: parseToys(), achievement: parseAchievements() };

const stat = (have, total) => ({ have, total, percent: Math.round((have / total) * 1000) / 10 });

// Look a collectible up by its real name; throws on typos so tests catch drift.
function row(kind, name) {
  const c = db[kind].find((x) => x.name === name);
  if (!c) throw new Error(`demoData: no ${kind} named "${name}" in data/wago`);
  return { id: c.id, name: c.name, source: c.source, has_guide: hasGuide(kind, c.id) };
}

export const demoCharacter = {
  realm: 'proudmoore',
  name: 'demo',
  demo: true,
  progression: {
    achievements: stat(2847, db.achievement.length),
    mounts: stat(412, db.mount.length),
    toys: stat(589, db.toy.length),
  },
  closest: [
    { id: 12909, name: 'Mount Armada', current_amount: 393, required_amount: 400, percent: 98.3, has_guide: hasGuide('achievement', 12909) },
    { id: 40724, name: 'Toybox Tycoon', current_amount: 389, required_amount: 400, percent: 97.3, has_guide: hasGuide('achievement', 40724) },
    { id: 2144, name: "What a Long, Strange Trip It's Been", current_amount: 7, required_amount: 8, percent: 87.5, has_guide: hasGuide('achievement', 2144) },
    { id: 19458, name: 'Glory of the Dream Raider', current_amount: 11, required_amount: 13, percent: 84.6, has_guide: hasGuide('achievement', 19458) },
    { id: 1956, name: 'Higher Learning', current_amount: 6, required_amount: 8, percent: 75.0, has_guide: hasGuide('achievement', 1956) },
    { id: 2336, name: 'Insane in the Membrane', current_amount: 6, required_amount: 8, percent: 75.0, has_guide: hasGuide('achievement', 2336) },
    { id: 9924, name: 'The Loremaster', current_amount: 5, required_amount: 7, percent: 71.4, has_guide: hasGuide('achievement', 9924) },
  ],
};

// Account-wide rollup (union across the demo account's three characters).
export const demoRollup = {
  progression: {
    achievements: stat(3012, db.achievement.length),
    mounts: stat(412, db.mount.length),
    toys: stat(589, db.toy.length),
  },
  closest: demoCharacter.closest.slice(0, 5),
  proxy_char: { realm: 'proudmoore', name: 'demo' },
};

export const demoMissing = {
  mount: [
    row('mount', 'Invincible'),
    row('mount', 'Time-Lost Proto-Drake'),
    row('mount', "Mimiron's Head"),
    row('mount', "Ashes of Al'ar"),
    row('mount', 'Onyxian Drake'),
    row('mount', 'Son of Galleon'),
    row('mount', 'Phosphorescent Stone Drake'),
    row('mount', 'Voidtalon of the Dark Star'),
  ],
  toy: [
    row('toy', 'Piccolo of the Flaming Fire'),
    row('toy', 'Blazing Wings'),
    row('toy', 'Iron Boot Flask'),
    row('toy', 'Orb of Deception'),
  ],
};
