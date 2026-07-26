// Validates the complete collectible database shipped in data/wago/
// (Blizzard client tables via wago.tools) and its parsers.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseMounts, parseToys, parseAchievements, cleanSourceText }
  from '../src/sources/wagoDb2.js';

test('cleanSourceText strips WoW UI markup', () => {
  assert.equal(
    cleanSourceText('|cFFFFD200Vendor: |rUnger Statforth|n|cFFFFD200Cost: |r1|TINTERFACE\\MONEYFRAME\\UI-GOLDICON.BLP:0|t'),
    'Vendor: Unger Statforth · Cost: 1g');
  assert.equal(cleanSourceText(''), null);
});

test('complete mount database: every mount, with journal source text', () => {
  const mounts = parseMounts();
  assert.ok(mounts.length > 1600, `${mounts.length} mounts`);
  const inv = mounts.find((m) => m.id === 363);
  assert.equal(inv.name, 'Invincible');
  assert.match(inv.source, /Drop: The Lich King/);
  assert.match(inv.source, /Icecrown Citadel/);
  assert.ok(!/\|c|\|r|\|n|\|T/.test(inv.source), 'markup stripped');
  const withSource = mounts.filter((m) => m.source).length;
  assert.ok(withSource / mounts.length > 0.9, 'vast majority have source text');
});

test('complete toy database: ids are Toy ids, names joined from items', () => {
  const toys = parseToys();
  assert.ok(toys.length > 1100, `${toys.length} toys`);
  const piccolo = toys.find((t) => t.id === 378);
  assert.equal(piccolo.name, 'Piccolo of the Flaming Fire');
  assert.equal(piccolo.item_id, 13379);
  assert.match(piccolo.source, /Hearthsinger Forresten/);
  const named = toys.filter((t) => !t.name.startsWith('Toy #')).length;
  assert.ok(named / toys.length > 0.95, 'toys resolve to real item names');
});

test('complete achievement database: statistics excluded, flags + categories real', () => {
  const achievements = parseAchievements();
  assert.ok(achievements.length > 9000, `${achievements.length} achievements`);
  const trip = achievements.find((a) => a.id === 2144);
  assert.equal(trip.name, "What a Long, Strange Trip It's Been");
  assert.equal(trip.points, 50);
  assert.equal(trip.is_account_wide, true);
  assert.equal(trip.category, 'World Events');
  assert.ok(achievements.every((a) => !/^Statistics/.test(a.category)), 'no statistics rows');
  assert.ok(achievements.some((a) => a.category.includes(' > ')), 'nested category paths');
});

test('every guide entry resolves to a real collectible in the database', async () => {
  const fs = await import('node:fs');
  const { entries } = JSON.parse(
    fs.readFileSync(new URL('../data/guides.seed.json', import.meta.url), 'utf8'));
  const db = { mount: parseMounts(), toy: parseToys(), achievement: parseAchievements() };
  for (const e of entries) {
    const hit = db[e.kind].find((x) => x.id === e.id);
    assert.ok(hit, `${e.kind} ${e.name} (id ${e.id}) exists in the database`);
    assert.equal(hit.name, e.name, `${e.name}: guide name matches journal name`);
  }
});
