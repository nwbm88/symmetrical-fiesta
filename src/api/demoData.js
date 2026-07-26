// Demo fixtures so the UI runs with ZERO setup (no Postgres, no API keys):
//   npm run demo
// Shapes mirror the real endpoints exactly, so the frontend can't drift from prod.
// Character numbers are realistic but fake. GUIDES ARE REAL: demo mode serves
// the same data/guides.seed.json that db:seed loads into Postgres — one source
// of truth for guide text and map pins.

import fs from 'node:fs';

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

export const demoCharacter = {
  realm: 'proudmoore',
  name: 'demo',
  demo: true,
  progression: {
    achievements: { have: 2847, total: 4322, percent: 65.9 },
    mounts: { have: 412, total: 953, percent: 43.2 },
    toys: { have: 589, total: 1219, percent: 48.3 },
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
    achievements: { have: 3012, total: 4322, percent: 69.7 },
    mounts: { have: 412, total: 953, percent: 43.2 },
    toys: { have: 589, total: 1219, percent: 48.3 },
  },
  closest: [
    { id: 12909, name: 'Mount Armada', current_amount: 393, required_amount: 400, percent: 98.3, has_guide: false },
    { id: 40724, name: 'Toybox Tycoon', current_amount: 389, required_amount: 400, percent: 97.3, has_guide: false },
    { id: 2144, name: "What a Long, Strange Trip It's Been", current_amount: 7, required_amount: 8, percent: 87.5, has_guide: hasGuide('achievement', 2144) },
    { id: 1956, name: 'Higher Learning', current_amount: 7, required_amount: 8, percent: 87.5, has_guide: hasGuide('achievement', 1956) },
    { id: 19458, name: 'Glory of the Dream Raider', current_amount: 11, required_amount: 13, percent: 84.6, has_guide: hasGuide('achievement', 19458) },
  ],
  proxy_char: { realm: 'proudmoore', name: 'demo' },
};

const mountRow = (id, name, source) => ({ id, name, source, has_guide: hasGuide('mount', id) });
const toyRow = (id, name, source) => ({ id, name, source, has_guide: hasGuide('toy', id) });

export const demoMissing = {
  mount: [
    mountRow(363, 'Invincible', 'Drop: The Lich King (25H), Icecrown Citadel — ~1%'),
    mountRow(265, 'Time-Lost Proto-Drake', 'Rare spawn: The Storm Peaks (shares timer with Vyragosa)'),
    mountRow(304, "Mimiron's Head", 'Drop: Yogg-Saron (0 Keepers), Ulduar'),
    mountRow(183, "Ashes of Al'ar", "Drop: Kael'thas Sunstrider, Tempest Keep"),
    mountRow(349, 'Onyxian Drake', "Drop: Onyxia, Onyxia's Lair"),
    mountRow(758, 'Son of Galleon', 'Drop: Galleon (world boss), Valley of the Four Winds'),
  ],
  toy: [
    toyRow(13379, 'Piccolo of the Flaming Fire', 'Drop: Hearthsinger Forresten, Stratholme'),
    toyRow(122304, 'Blazing Wings', "Darkmoon Faire: Firebird's Challenge"),
    toyRow(44430, 'Iron Boot Flask', 'Vendor: K3, The Storm Peaks (Relics of Ulduar)'),
    toyRow(1973, 'Orb of Deception', 'World drop (or Auction House)'),
  ],
};
