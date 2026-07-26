// Thin, rate-limit-aware wrapper over the Battle.net API.
// Blizzard caps at ~100 req/s and 36,000 req/hour; we self-throttle and back off on 429.

import { getAccessToken } from './auth.js';

const REGION = process.env.REGION || 'us';
const API_HOST = REGION === 'cn'
  ? 'https://gateway.battlenet.com.cn'
  : `https://${REGION}.api.blizzard.com`;

// Namespaces tell Blizzard which data set to return.
// GAME_VERSION=retail (default) | classic (Cata/MoP progression) | classic1x (Era).
const GAME = process.env.GAME_VERSION || 'retail';
const FLAVOR = GAME === 'retail' ? '' : `-${GAME}`;
export const NS = {
  static: `static${FLAVOR}-${REGION}`,   // game data (achievements, mounts, toys)
  profile: `profile${FLAVOR}-${REGION}`, // character data
  dynamic: `dynamic${FLAVOR}-${REGION}`,
};

let lastCall = 0;
const MIN_GAP_MS = 40; // ~25 req/s, comfortably under the cap

async function throttle() {
  const wait = lastCall + MIN_GAP_MS - Date.now();
  if (wait > 0) await new Promise((r) => setTimeout(r, wait));
  lastCall = Date.now();
}

// path e.g. "/data/wow/mount/index"; returns parsed JSON, or null for 404 (private/missing).
export async function apiGet(path, namespace, { retries = 3 } = {}) {
  const token = await getAccessToken();
  const url = `${API_HOST}${path}?namespace=${namespace}&locale=en_US`;

  for (let attempt = 0; ; attempt++) {
    await throttle();
    const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });

    if (res.ok) return res.json();
    if (res.status === 404) return null;
    if ((res.status === 429 || res.status >= 500) && attempt < retries) {
      await new Promise((r) => setTimeout(r, 2 ** attempt * 1000));
      continue;
    }
    throw new Error(`API ${res.status} for ${path}: ${await res.text()}`);
  }
}
