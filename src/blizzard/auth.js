// Battle.net OAuth (client-credentials grant) for reading public game + profile data.
// Token is cached in memory and refreshed automatically before expiry.

const REGION = process.env.REGION || 'us';
const OAUTH_HOST = REGION === 'cn' ? 'https://oauth.battlenet.com.cn' : 'https://oauth.battle.net';

let cached = { token: null, expiresAt: 0 };

export async function getAccessToken() {
  if (cached.token && Date.now() < cached.expiresAt - 60_000) return cached.token;

  const id = process.env.BLIZZARD_CLIENT_ID;
  const secret = process.env.BLIZZARD_CLIENT_SECRET;
  if (!id || !secret) throw new Error('Set BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET in .env');

  const res = await fetch(`${OAUTH_HOST}/token`, {
    method: 'POST',
    headers: {
      Authorization: 'Basic ' + Buffer.from(`${id}:${secret}`).toString('base64'),
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body: 'grant_type=client_credentials',
  });
  if (!res.ok) throw new Error(`OAuth failed: ${res.status} ${await res.text()}`);

  const json = await res.json();
  cached = { token: json.access_token, expiresAt: Date.now() + json.expires_in * 1000 };
  return cached.token;
}
