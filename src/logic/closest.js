// Progression math: overall %, per-category %, and the headline "closest to finishing" list.
import { query } from '../db.js';

// Overall completion for a character across a collectible kind.
export async function overall(characterId, kind) {
  const totalRow = await query(
    kind === 'achievement' ? 'SELECT count(*)::int AS n FROM achievements'
    : kind === 'mount' ? 'SELECT count(*)::int AS n FROM mounts'
    : 'SELECT count(*)::int AS n FROM toys',
  );
  const total = totalRow.rows[0].n;
  const owned = await query(
    `SELECT count(*)::int AS n FROM character_progress
      WHERE character_id = $1 AND kind = $2 AND completed = true`,
    [characterId, kind],
  );
  const have = owned.rows[0].n;
  return { have, total, percent: total ? Math.round((have / total) * 1000) / 10 : 0 };
}

// Incomplete achievements sorted by how close they are to done (highest % first).
// This is what makes the site fun: "you're 9/10 on these — go finish them."
export async function closestAchievements(characterId, limit = 25) {
  const { rows } = await query(
    `SELECT p.collectible_id AS id, a.name, a.icon,
            p.current_amount, p.required_amount,
            round(100.0 * p.current_amount / NULLIF(p.required_amount, 0), 1) AS percent
       FROM character_progress p
       JOIN achievements a ON a.id = p.collectible_id
      WHERE p.character_id = $1
        AND p.kind = 'achievement'
        AND p.completed = false
        AND p.required_amount > 1        -- only multi-step achievements have "closeness"
        AND p.current_amount > 0
      ORDER BY percent DESC, p.required_amount ASC
      LIMIT $2`,
    [characterId, limit],
  );
  return rows;
}
