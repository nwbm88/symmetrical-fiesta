// End-to-end API tests against the demo server (no DB or API keys needed).
//   npm test
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';

const PORT = 3210;
const BASE = `http://127.0.0.1:${PORT}`;
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

test('config reports demo mode', async () => {
  const cfg = await (await fetch(`${BASE}/api/config`)).json();
  assert.equal(cfg.demo, true);
  assert.ok(cfg.region);
});

test('character overview has progression + closest', async () => {
  const res = await fetch(`${BASE}/api/character/proudmoore/demo`);
  assert.equal(res.status, 200);
  const data = await res.json();
  for (const k of ['achievements', 'mounts', 'toys']) {
    const s = data.progression[k];
    assert.ok(s.have <= s.total, `${k} have<=total`);
    assert.ok(s.percent >= 0 && s.percent <= 100, `${k} percent sane`);
  }
  assert.ok(Array.isArray(data.closest) && data.closest.length > 0);
  const first = data.closest[0];
  assert.ok(first.current_amount < first.required_amount, 'closest items are incomplete');
  // sorted by percent desc
  const pcts = data.closest.map((c) => Number(c.percent));
  assert.deepEqual(pcts, [...pcts].sort((a, b) => b - a));
});

test('missing lists include source + guide flag', async () => {
  for (const kind of ['mount', 'toy']) {
    const data = await (await fetch(`${BASE}/api/character/proudmoore/demo/missing/${kind}`)).json();
    assert.equal(data.kind, kind);
    assert.ok(data.missing.length > 0);
    assert.ok('source' in data.missing[0] && 'has_guide' in data.missing[0]);
  }
});

test('invalid missing kind is a 400', async () => {
  const res = await fetch(`${BASE}/api/character/x/y/missing/pet`);
  assert.equal(res.status, 400);
});

test('guide returns text + map locations', async () => {
  const data = await (await fetch(`${BASE}/api/guide/mount/265`)).json();
  assert.ok(data.guide.body_md.length > 50);
  assert.equal(data.locations.length, 4); // the four wiki-verified TLPD spawn points
  for (const l of data.locations) {
    assert.ok(l.coord_x >= 0 && l.coord_x <= 100);
    assert.ok(l.coord_y >= 0 && l.coord_y <= 100);
  }
});

test('unknown guide is empty, not an error', async () => {
  const res = await fetch(`${BASE}/api/guide/mount/999999`);
  assert.equal(res.status, 200);
  assert.deepEqual(await res.json(), { guide: null, locations: [] });
});

test('auth: /api/me requires login; demo login grants a session + rollup', async () => {
  assert.equal((await fetch(`${BASE}/api/me`)).status, 401);

  const login = await fetch(`${BASE}/auth/login`, { redirect: 'manual' });
  assert.equal(login.status, 302);
  const cookie = login.headers.get('set-cookie').split(';')[0];
  assert.match(cookie, /^sid=/);

  const me = await (await fetch(`${BASE}/api/me`, { headers: { cookie } })).json();
  assert.equal(me.battletag, 'Demo#1234');
  assert.equal(me.characters.length, 3);

  const roll = await (await fetch(`${BASE}/api/me/rollup`, { headers: { cookie } })).json();
  assert.ok(roll.progression.achievements.have >= 2847, 'rollup >= best single character');
  assert.ok(roll.proxy_char.realm);

  // logout kills the session
  await fetch(`${BASE}/auth/logout`, { headers: { cookie }, redirect: 'manual' });
  assert.equal((await fetch(`${BASE}/api/me`, { headers: { cookie } })).status, 401);
});

test('frontend assets are served', async () => {
  const html = await (await fetch(`${BASE}/`)).text();
  assert.match(html, /CollectionTracker|Collection<b>Tracker<\/b>/);
  assert.equal((await fetch(`${BASE}/app.js`)).status, 200);
  assert.equal((await fetch(`${BASE}/style.css`)).status, 200);
});
