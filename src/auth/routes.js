// "Sign in with Battle.net" (OAuth Authorization Code flow, scope wow.profile).
// Lets a user list ALL their characters automatically (including ones not public)
// and unlocks the account-wide rollup view.
//
// Sessions are in-memory (cookie sid → session). Fine for a personal/self-hosted
// site; move to a session store if you ever run multiple server processes.

import crypto from 'node:crypto';

const REGION = process.env.REGION || 'us';
const OAUTH_HOST = REGION === 'cn' ? 'https://oauth.battlenet.com.cn' : 'https://oauth.battle.net';
const API_HOST = REGION === 'cn' ? 'https://gateway.battlenet.com.cn' : `https://${REGION}.api.blizzard.com`;
const PUBLIC_URL = process.env.PUBLIC_URL || `http://localhost:${process.env.PORT || 3000}`;

const sessions = new Map(); // sid → { battletag, accountId, characters, expiresAt }
const pendingStates = new Map(); // state → expiry

function cleanup(map) {
  const now = Date.now();
  for (const [k, v] of map) if ((v.expiresAt ?? v) < now) map.delete(k);
}

export function getSession(req) {
  cleanup(sessions);
  const sid = (req.headers.cookie ?? '').split(/;\s*/)
    .find((c) => c.startsWith('sid='))?.slice(4);
  return sid ? sessions.get(sid) : undefined;
}

function startSession(res, data) {
  const sid = crypto.randomBytes(24).toString('base64url');
  sessions.set(sid, { ...data, expiresAt: Date.now() + 24 * 3600_000 });
  res.setHeader('Set-Cookie', `sid=${sid}; HttpOnly; SameSite=Lax; Path=/; Max-Age=86400`);
}

export function mountAuth(app, { demo, db }) {
  app.get('/auth/login', (req, res) => {
    if (demo) { // fake login so the whole flow is testable with zero setup
      startSession(res, {
        battletag: 'Demo#1234', accountId: 1,
        characters: [
          { name: 'Demo', realm: 'proudmoore', level: 80 },
          { name: 'Altoholic', realm: 'proudmoore', level: 80 },
          { name: 'Bankchar', realm: 'stormrage', level: 70 },
        ],
      });
      return res.redirect('/');
    }
    const state = crypto.randomBytes(16).toString('base64url');
    cleanup(pendingStates);
    pendingStates.set(state, Date.now() + 10 * 60_000);
    const q = new URLSearchParams({
      client_id: process.env.BLIZZARD_CLIENT_ID,
      response_type: 'code',
      scope: 'wow.profile',
      state,
      redirect_uri: `${PUBLIC_URL}/auth/callback`,
    });
    res.redirect(`${OAUTH_HOST}/authorize?${q}`);
  });

  app.get('/auth/callback', async (req, res) => {
    try {
      const { code, state } = req.query;
      if (!pendingStates.delete(state)) return res.status(400).send('Bad state — try logging in again.');

      // 1. code → user access token
      const tokenRes = await fetch(`${OAUTH_HOST}/token`, {
        method: 'POST',
        headers: {
          Authorization: 'Basic ' + Buffer.from(
            `${process.env.BLIZZARD_CLIENT_ID}:${process.env.BLIZZARD_CLIENT_SECRET}`).toString('base64'),
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: new URLSearchParams({
          grant_type: 'authorization_code', code,
          redirect_uri: `${PUBLIC_URL}/auth/callback`,
        }),
      });
      if (!tokenRes.ok) throw new Error(`token exchange failed: ${tokenRes.status}`);
      const { access_token } = await tokenRes.json();
      const bearer = { Authorization: `Bearer ${access_token}` };

      // 2. who is this? → battletag
      const user = await (await fetch(`${OAUTH_HOST}/userinfo`, { headers: bearer })).json();

      // 3. their full character list (protected profile — needs the user token)
      const prof = await (await fetch(
        `${API_HOST}/profile/user/wow?namespace=profile-${REGION}&locale=en_US`,
        { headers: bearer })).json();
      const characters = (prof.wow_accounts ?? []).flatMap((a) => a.characters ?? [])
        .map((c) => ({ name: c.name, realm: c.realm.slug, level: c.level }))
        .sort((a, b) => b.level - a.level);

      // 4. persist account + link characters
      const { rows: [acct] } = await db.query(
        `INSERT INTO accounts (battletag) VALUES ($1)
         ON CONFLICT (battletag) DO UPDATE SET battletag = EXCLUDED.battletag
         RETURNING id`, [user.battletag]);
      for (const c of characters) {
        await db.query(
          `INSERT INTO characters (region, realm, name, account_id) VALUES ($1, $2, $3, $4)
           ON CONFLICT (region, realm, name) DO UPDATE SET account_id = EXCLUDED.account_id`,
          [REGION, c.realm, c.name.toLowerCase(), acct.id]);
      }

      startSession(res, { battletag: user.battletag, accountId: acct.id, characters });
      res.redirect('/');
    } catch (e) {
      console.error(e);
      res.status(500).send('Login failed — check server logs.');
    }
  });

  app.get('/auth/logout', (req, res) => {
    const sid = (req.headers.cookie ?? '').split(/;\s*/)
      .find((c) => c.startsWith('sid='))?.slice(4);
    if (sid) sessions.delete(sid);
    res.setHeader('Set-Cookie', 'sid=; Path=/; Max-Age=0');
    res.redirect('/');
  });

  app.get('/api/me', (req, res) => {
    const s = getSession(req);
    if (!s) return res.status(401).json({ error: 'Not signed in.' });
    res.json({ battletag: s.battletag, characters: s.characters });
  });
}
