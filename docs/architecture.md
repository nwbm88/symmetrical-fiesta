# Architecture

```
                 ┌────────────────────────┐
   Blizzard API ─┤ ingest/staticData.js    ├─▶ mounts / toys / achievements (static, cached)
                 └────────────────────────┘
  ATT / Wiki /   ┌────────────────────────┐
  TrinityCore  ──┤ src/sources/*.js        ├─▶ guides / locations (open-source data)
                 └────────────────────────┘
                 ┌────────────────────────┐
 Character name ─┤ ingest/characterProgress├─▶ characters / character_progress (per user, cached)
                 └────────────────────────┘
                             │
                    logic/closest.js  ──▶  api/server.js  ──▶  frontend
```

## Why data lives in your DB
Two write paths (static game data, per-character progress) both land in Postgres. The API and
frontend only ever read from your DB, so pages are instant and survive third-party outages.
Freshness comes from re-running ingest: static on patches, character on a 6h TTL (see server.js).

## Account-wide vs per-character
Mounts and toys are account-wide; most achievements are per-character. The schema keeps `kind` on
`character_progress` so you can compute both correctly. For a true account view later, group
characters under an account id (add an `accounts` table) once you add Battle.net login.

## Reading a user's OWN collection (implemented)
Client-credentials only sees public data. `src/auth/routes.js` implements the OAuth
Authorization Code flow (scope `wow.profile`): `/auth/login` → Battle.net →
`/auth/callback` exchanges the code, reads the battletag from `/userinfo`, lists all
characters via `/profile/user/wow`, links them to an `accounts` row, and starts an
in-memory cookie session. `/api/me` returns the character list; `/api/me/rollup`
(logic/account.js) unions progress across the account's synced characters. Sessions
are in-memory — fine single-process; add a store if you scale out.

## Postgres vs SQLite
The scaffold uses Postgres (`pg`). To prototype with zero setup, SQLite works too — swap `src/db.js`
for `better-sqlite3` and change `SERIAL`→`INTEGER PRIMARY KEY AUTOINCREMENT`, `TIMESTAMPTZ`→`TEXT`,
`BOOLEAN`→`INTEGER` in the schema.

## Retail vs Classic
This targets Retail (`static-us` / `profile-us`). Classic uses different namespaces
(e.g. `static-classic-us`) and item lists — pick one first; don't mix.
```
