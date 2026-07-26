// Guide dataset integrity + demo-server serving of every addressable guide.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';

const PORT = 3211;
const BASE = `http://127.0.0.1:${PORT}`;
const dataset = JSON.parse(fs.readFileSync(new URL('../data/guides.seed.json', import.meta.url), 'utf8'));
let server;

before(async () => {
  server = spawn(process.execPath, ['src/api/server.js'], {
    env: { ...process.env, DEMO: '1', PORT: String(PORT), NO_PROXY: '*' },
    stdio: 'ignore',
  });
  for (let i = 0; i < 50; i++) {
    try { await fetch(`${BASE}/api/config`); return; }
    catch { await new Promise((r) => setTimeout(r, 100)); }
  }
  throw new Error('server did not start');
});

after(() => server.kill());

test('dataset shape: kinds, names, guide text, coordinate bounds', () => {
  assert.ok(dataset.entries.length >= 40, 'has a substantial guide set');
  const seen = new Set();
  for (const e of dataset.entries) {
    assert.ok(['mount', 'toy', 'achievement'].includes(e.kind), `${e.name}: valid kind`);
    assert.ok(e.name?.length > 2, 'has a name');
    assert.ok(e.guide?.length > 60, `${e.name}: guide text is substantial`);
    assert.ok(e.source_name, `${e.name}: has attribution`);
    const key = `${e.kind}:${e.name}`;
    assert.ok(!seen.has(key), `${key}: no duplicates`);
    seen.add(key);
    for (const l of e.locations ?? []) {
      assert.ok(l.x >= 0 && l.x <= 100 && l.y >= 0 && l.y <= 100, `${e.name}: coords in 0-100`);
      assert.ok(l.npc_name && l.zone, `${e.name}: pins have npc + zone`);
    }
  }
});

test('no leftover drafting artifacts in guide text', () => {
  for (const e of dataset.entries) {
    assert.ok(!/\?\s*(No|no)[:\s—-]/.test(e.guide), `${e.name}: no self-correction artifacts`);
    assert.ok(!/TODO|FIXME|XXX/.test(e.guide), `${e.name}: no TODO markers`);
  }
});

test('wiki-sourced entries carry a source_url for CC BY-SA attribution', () => {
  for (const e of dataset.entries.filter((x) => /Wiki/i.test(x.source_name)))
    assert.match(e.source_url ?? '', /^https:\/\//, `${e.name}: wiki source has URL`);
});

test('every id-bearing entry is served by the demo guide endpoint', async () => {
  const addressable = dataset.entries.filter((e) => e.id != null);
  assert.ok(addressable.length >= 10);
  for (const e of addressable) {
    const res = await fetch(`${BASE}/api/guide/${e.kind}/${e.id}`);
    assert.equal(res.status, 200, `${e.kind}:${e.id} responds`);
    const data = await res.json();
    assert.ok(data.guide?.body_md?.length > 60, `${e.name}: guide served`);
    assert.equal(data.locations.length, e.locations?.length ?? 0, `${e.name}: pin count matches`);
  }
});

test('TLPD guide reflects verified 12.0.7 spawn data (4 pins, shared timer)', async () => {
  const data = await (await fetch(`${BASE}/api/guide/mount/265`)).json();
  assert.equal(data.locations.length, 4, 'four verified spawn points');
  assert.match(data.guide.body_md, /Vyragosa/, 'mentions the shared spawn');
  assert.match(data.guide.source_name ?? '', /Warcraft Wiki/, 'attributed to the wiki');
});

test('demo missing lists agree with the dataset about guide availability', async () => {
  const ids = new Set(dataset.entries.filter((e) => e.id != null).map((e) => `${e.kind}:${e.id}`));
  for (const kind of ['mount', 'toy']) {
    const { missing } = await (await fetch(`${BASE}/api/character/proudmoore/demo/missing/${kind}`)).json();
    for (const m of missing)
      assert.equal(m.has_guide, ids.has(`${kind}:${m.id}`), `${m.name}: has_guide consistent`);
  }
});
