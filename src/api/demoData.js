// Demo fixtures so the UI runs with ZERO setup (no Postgres, no API keys):
//   npm run demo
// Shapes mirror the real endpoints exactly, so the frontend can't drift from prod.
// Numbers are realistic but fake; guide text is original; coords are approximate
// community knowledge — the real pipeline (TrinityCore import) replaces them.

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
    { id: 12909, name: 'Mount Armada', current_amount: 393, required_amount: 400, percent: 98.3, has_guide: false },
    { id: 40724, name: 'Toybox Tycoon', current_amount: 389, required_amount: 400, percent: 97.3, has_guide: false },
    { id: 2144, name: "What a Long, Strange Trip It's Been", current_amount: 7, required_amount: 8, percent: 87.5, has_guide: false },
    { id: 19458, name: 'Glory of the Dream Raider', current_amount: 11, required_amount: 13, percent: 84.6, has_guide: false },
    { id: 1956, name: 'Higher Learning', current_amount: 6, required_amount: 8, percent: 75.0, has_guide: false },
    { id: 46, name: 'Universal Explorer', current_amount: 3, required_amount: 4, percent: 75.0, has_guide: false },
    { id: 9924, name: 'The Loremaster', current_amount: 5, required_amount: 7, percent: 71.4, has_guide: false },
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
    { id: 2144, name: "What a Long, Strange Trip It's Been", current_amount: 7, required_amount: 8, percent: 87.5, has_guide: false },
    { id: 1956, name: 'Higher Learning', current_amount: 7, required_amount: 8, percent: 87.5, has_guide: false },
    { id: 19458, name: 'Glory of the Dream Raider', current_amount: 11, required_amount: 13, percent: 84.6, has_guide: false },
  ],
  proxy_char: { realm: 'proudmoore', name: 'demo' },
};

export const demoMissing = {
  mount: [
    { id: 363, name: "Invincible's Reins", source: 'Drop: The Lich King (25H), Icecrown Citadel — ~1%', has_guide: true },
    { id: 265, name: 'Time-Lost Proto-Drake', source: 'Rare spawn: The Storm Peaks', has_guide: true },
    { id: 304, name: "Mimiron's Head", source: 'Drop: Yogg-Saron (0 Keepers), Ulduar', has_guide: true },
    { id: 183, name: "Ashes of Al'ar", source: "Drop: Kael'thas Sunstrider, Tempest Keep", has_guide: true },
    { id: 349, name: 'Reins of the Onyxian Drake', source: "Drop: Onyxia, Onyxia's Lair", has_guide: true },
    { id: 758, name: 'Son of Galleon', source: 'Drop: Galleon (world boss), Valley of the Four Winds', has_guide: false },
  ],
  toy: [
    { id: 13379, name: 'Piccolo of the Flaming Fire', source: 'Drop: Hearthsinger Forresten, Stratholme', has_guide: true },
    { id: 122304, name: 'Blazing Wings', source: "Darkmoon Faire: Firebird's Challenge", has_guide: false },
    { id: 44430, name: 'Iron Boot Flask', source: 'Vendor: K3, The Storm Peaks (Relics of Ulduar)', has_guide: false },
    { id: 1973, name: 'Orb of Deception', source: 'World drop (or Auction House)', has_guide: false },
  ],
};

// Keyed `${kind}:${id}` — same payload shape as /api/guide/:kind/:id.
export const demoGuides = {
  'mount:363': {
    guide: {
      body_md: [
        '**Weekly routine — 10 minutes.**',
        '1. Fly to Icecrown Citadel in Icecrown (Northrend).',
        '2. Set raid difficulty to **25 Heroic** before entering.',
        '3. Clear to The Lich King (skippable trash with the teleporter after first wing).',
        '4. Kill him; the mount is a personal-loot roll at roughly 1%.',
        '',
        '*Tip: it is once per character per week — bring alts.*',
      ].join('\n'),
      source_name: 'Original guide',
      source_url: null,
    },
    locations: [
      { npc_name: 'The Lich King', zone: 'Icecrown Citadel, Icecrown', map_id: 118, coord_x: 75.2, coord_y: 21.5, note: 'Raid entrance (approx.)' },
    ],
  },
  'mount:265': {
    guide: {
      body_md: [
        '**Camping a rare spawn.** The Time-Lost Proto-Drake shares a spawn timer with Vyragosa in The Storm Peaks.',
        '1. Add the patrol points below to your map and park an alt at one.',
        '2. Use a targeting macro: `/tar Time-Lost` and keybind it.',
        '3. When it spawns it flies a fixed circuit — engage fast, it despawns on evade.',
        '',
        '*Tip: NPCScan-style addons alert you the moment it enters range.*',
      ].join('\n'),
      source_name: 'Original guide',
      source_url: null,
    },
    locations: [
      { npc_name: 'Time-Lost Proto-Drake', zone: 'The Storm Peaks', map_id: 120, coord_x: 35.0, coord_y: 76.0, note: "Bor's Breath circuit (approx.)" },
      { npc_name: 'Time-Lost Proto-Drake', zone: 'The Storm Peaks', map_id: 120, coord_x: 30.0, coord_y: 66.0, note: 'Ulduar plateau circuit (approx.)' },
      { npc_name: 'Time-Lost Proto-Drake', zone: 'The Storm Peaks', map_id: 120, coord_x: 49.0, coord_y: 71.0, note: 'Brunnhildar circuit (approx.)' },
    ],
  },
  'mount:304': {
    guide: {
      body_md: [
        '**Weekly Ulduar run.**',
        '1. Enter Ulduar in The Storm Peaks on 25-player difficulty.',
        '2. Do **not** activate any Keepers — the mount only drops from Yogg-Saron with zero Keepers ("Alone in the Darkness").',
        '3. Solo is trivial at max level; the fight still requires entering portals in phase 2.',
      ].join('\n'),
      source_name: 'Original guide',
      source_url: null,
    },
    locations: [
      { npc_name: 'Yogg-Saron', zone: 'Ulduar, The Storm Peaks', map_id: 120, coord_x: 41.6, coord_y: 17.8, note: 'Raid entrance (approx.)' },
    ],
  },
  'mount:183': {
    guide: {
      body_md: [
        '**Weekly Tempest Keep run.**',
        '1. Fly to Tempest Keep in Netherstorm (Outland) — The Eye is the main structure.',
        '2. Clear to Kael\'thas Sunstrider (last boss); all trash is soloable.',
        '3. ~2% drop, once per character per week.',
      ].join('\n'),
      source_name: 'Original guide',
      source_url: null,
    },
    locations: [
      { npc_name: "Kael'thas Sunstrider", zone: 'Tempest Keep, Netherstorm', map_id: 109, coord_x: 73.9, coord_y: 63.6, note: 'Raid entrance (approx.)' },
    ],
  },
  'mount:349': {
    guide: {
      body_md: [
        '**Fastest weekly lockout in the game.**',
        '1. Onyxia\'s Lair is in Dustwallow Marsh (Kalimdor), inside the Wyrmbog cave.',
        '2. One boss, ~60 seconds start to finish at max level.',
        '3. ~1% drop — a classic "do it every week forever" mount.',
      ].join('\n'),
      source_name: 'Original guide',
      source_url: null,
    },
    locations: [
      { npc_name: 'Onyxia', zone: "Onyxia's Lair, Dustwallow Marsh", map_id: 70, coord_x: 52.2, coord_y: 76.5, note: 'Lair entrance (approx.)' },
    ],
  },
  'toy:13379': {
    guide: {
      body_md: [
        '**Farm route.**',
        '1. Enter Stratholme (Eastern Plaguelands) via the main gate.',
        '2. Hearthsinger Forresten is a rare that can appear along the first streets — clear toward Festival Lane.',
        '3. Reset and re-run if the rare is up but doesn\'t drop it (~3%).',
      ].join('\n'),
      source_name: 'Original guide',
      source_url: null,
    },
    locations: [
      { npc_name: 'Hearthsinger Forresten', zone: 'Stratholme, Eastern Plaguelands', map_id: 23, coord_x: 27.0, coord_y: 12.0, note: 'Main gate (approx.)' },
    ],
  },
};
