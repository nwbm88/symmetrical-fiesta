// Account-wide rollup: union progress across every synced character of an account.
// Mounts/toys are account-wide anyway; achievements count as earned if ANY
// character finished them, and "closest" uses the best-progressed character.
import { query } from '../db.js';

async function rollupKind(accountId, kind, totalSql) {
  const total = (await query(totalSql)).rows[0].n;
  const { rows: [r] } = await query(
    `SELECT count(DISTINCT p.collectible_id)::int AS n
       FROM character_progress p JOIN characters c ON c.id = p.character_id
      WHERE c.account_id = $1 AND p.kind = $2 AND p.completed`,
    [accountId, kind]);
  return { have: r.n, total, percent: total ? Math.round((r.n / total) * 1000) / 10 : 0 };
}

export async function accountRollup(accountId) {
  const [achievements, mounts, toys] = await Promise.all([
    rollupKind(accountId, 'achievement', 'SELECT count(*)::int AS n FROM achievements'),
    rollupKind(accountId, 'mount', 'SELECT count(*)::int AS n FROM mounts'),
    rollupKind(accountId, 'toy', 'SELECT count(*)::int AS n FROM toys'),
  ]);

  const { rows: closest } = await query(
    `SELECT p.collectible_id AS id, a.name,
            max(p.current_amount) AS current_amount,
            max(p.required_amount) AS required_amount,
            round(100.0 * max(p.current_amount) / NULLIF(max(p.required_amount), 0), 1) AS percent,
            bool_or(g.id IS NOT NULL) AS has_guide
       FROM character_progress p
       JOIN characters c ON c.id = p.character_id
       JOIN achievements a ON a.id = p.collectible_id
       LEFT JOIN guides g ON g.collectible_type = 'achievement' AND g.collectible_id = p.collectible_id
      WHERE c.account_id = $1 AND p.kind = 'achievement'
      GROUP BY p.collectible_id, a.name
     HAVING NOT bool_or(p.completed)
        AND max(p.required_amount) > 1 AND max(p.current_amount) > 0
      ORDER BY percent DESC, max(p.required_amount) ASC
      LIMIT 25`,
    [accountId]);

  // Most recently synced character — the frontend uses it for missing-list tabs
  // (mounts/toys are account-wide, so any synced character reflects the account).
  const { rows: [proxy] } = await query(
    `SELECT realm, name FROM characters
      WHERE account_id = $1 AND last_synced IS NOT NULL
      ORDER BY last_synced DESC LIMIT 1`, [accountId]);

  return { progression: { achievements, mounts, toys }, closest, proxy_char: proxy ?? null };
}
