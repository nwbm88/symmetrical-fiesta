// Vanilla JS frontend — no build step, talks to the local API only.
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

let current = null; // { realm, name }
const missingCache = {};

// ---------- boot ----------
fetch('/api/config').then((r) => r.json()).then((cfg) => {
  if (cfg.demo) {
    $('#demo-badge').hidden = false;
    $('#realm').value = 'proudmoore';
    $('#name').value = 'Demo';
  }
  $('#region').value = cfg.region ?? 'us';
});

// Signed in? Show battletag + character chips + account-total view.
fetch('/api/me').then((r) => (r.ok ? r.json() : null)).then((me) => {
  if (!me) return;
  $('#auth').innerHTML =
    `<span class="tag">${esc(me.battletag)}</span><a class="chip" href="/auth/logout">Log out</a>`;
  $('#mychars-list').innerHTML = me.characters.map((c) =>
    `<button class="chip" data-char="${esc(c.realm)}:${esc(c.name)}">
       ${esc(c.name)} <small>· ${esc(c.realm)}${c.level ? ` · ${c.level}` : ''}</small>
     </button>`).join('');
  $('#mychars').hidden = false;
});

document.addEventListener('click', (e) => {
  const chip = e.target.closest('[data-char]');
  if (!chip) return;
  const [realm, name] = chip.dataset.char.split(':');
  loadCharacter(realm.toLowerCase(), name);
});

// Account-wide rollup (union across all your characters).
$('#account-view')?.addEventListener('click', async () => {
  const res = await fetch('/api/me/rollup');
  const data = await res.json();
  if (!res.ok) return;
  current = data.proxy_char ?? current; // missing-lists use the freshest synced char
  Object.keys(missingCache).forEach((k) => delete missingCache[k]);
  render({
    name: 'Account total', realm: data.battletag,
    progression: data.progression, closest: data.closest,
  });
});

$('#lookup').addEventListener('submit', async (e) => {
  e.preventDefault();
  const realm = $('#realm').value.trim().toLowerCase().replace(/[\s']/g, '-');
  const name = $('#name').value.trim();
  if (!realm || !name) return;
  await loadCharacter(realm, name);
});

// ---------- character dashboard ----------
async function loadCharacter(realm, name) {
  const btn = $('#lookup button');
  btn.disabled = true; btn.textContent = 'Loading…';
  try {
    const res = await fetch(`/api/character/${encodeURIComponent(realm)}/${encodeURIComponent(name)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    current = { realm, name };
    Object.keys(missingCache).forEach((k) => delete missingCache[k]);
    render(data);
  } catch (err) {
    $('#empty').insertAdjacentHTML('beforeend',
      `<div class="error">Couldn't load <b>${esc(name)}</b> on <b>${esc(realm)}</b>: ${esc(err.message)}</div>`);
  } finally {
    btn.disabled = false; btn.textContent = 'Track';
  }
}

function render(data) {
  $('#empty').hidden = true;
  $('#dashboard').hidden = false;
  $('#char-title').textContent = data.name;
  $('#char-sub').textContent = `${data.realm} · ${($('#region').value || 'us').toUpperCase()}`;

  const p = data.progression;
  $('#tiles').innerHTML = [
    tile('Achievements', p.achievements),
    tile('Mounts', p.mounts),
    tile('Toys', p.toys),
  ].join('');

  $('#panel-closest').innerHTML = data.closest.length
    ? data.closest.map(closestRow).join('')
    : `<p class="sub">No partially-complete achievements found yet — sync may still be filling in criteria.</p>`;

  selectTab('closest');
}

function tile(label, s) {
  const pct = Math.min(100, s.percent ?? 0);
  return `<div class="tile">
    <div class="label">${esc(label)}</div>
    <div class="value">${pct}%</div>
    <div class="counts">${s.have.toLocaleString()} / ${s.total.toLocaleString()} collected</div>
    <div class="meter" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"
         aria-label="${esc(label)} completion"><i style="width:${pct}%"></i></div>
  </div>`;
}

function closestRow(a) {
  const pct = Math.min(100, Number(a.percent) || 0);
  return `<div class="row">
    <span class="name">${esc(a.name)}</span>
    <div class="meter" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"
         aria-label="${esc(a.name)} progress"><i style="width:${pct}%"></i></div>
    <span class="frac"><b>${a.current_amount}</b> / ${a.required_amount}</span>
    ${a.has_guide
      ? `<button class="chip" data-guide="achievement:${a.id}" data-name="${esc(a.name)}">Guide</button>`
      : `<span class="frac">${pct}%</span>`}
  </div>`;
}

// ---------- tabs + filter ----------
$('#tabs').addEventListener('click', (e) => {
  const tab = e.target.closest('[data-tab]');
  if (tab) selectTab(tab.dataset.tab);
});

// Live text filter over whichever panel is visible.
$('#filter').addEventListener('input', () => {
  const q = $('#filter').value.trim().toLowerCase();
  const panel = document.querySelector('.panel:not([hidden])');
  for (const item of panel.querySelectorAll('.row, .card'))
    item.style.display = !q || item.textContent.toLowerCase().includes(q) ? '' : 'none';
});

async function selectTab(id) {
  for (const b of document.querySelectorAll('#tabs [data-tab]'))
    b.setAttribute('aria-selected', String(b.dataset.tab === id));
  for (const p of document.querySelectorAll('.panel'))
    p.hidden = p.id !== `panel-${id}`;
  $('#filter').value = '';
  if ((id === 'mount' || id === 'toy') && current) await loadMissing(id);
}

async function loadMissing(kind) {
  const panel = $(`#panel-${kind}`);
  if (missingCache[kind]) return;
  panel.innerHTML = '<p class="sub">Loading…</p>';
  const res = await fetch(
    `/api/character/${encodeURIComponent(current.realm)}/${encodeURIComponent(current.name)}/missing/${kind}`);
  const data = await res.json();
  missingCache[kind] = true;
  panel.innerHTML = `<div class="cards">${data.missing.map((m) => `
    <div class="card">
      <span class="name">${esc(m.name)}</span>
      <span class="source">${esc(m.source || 'Source not imported yet — run the All The Things importer.')}</span>
      ${m.has_guide
        ? `<button class="chip" data-guide="${kind}:${m.id}" data-name="${esc(m.name)}">Guide &amp; map</button>`
        : `<button class="chip" disabled>No guide yet</button>`}
    </div>`).join('')}</div>`;
}

// ---------- guide modal + map ----------
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('[data-guide]');
  if (!btn) return;
  const [kind, id] = btn.dataset.guide.split(':');
  const res = await fetch(`/api/guide/${kind}/${id}`);
  const data = await res.json();
  openGuide(btn.dataset.name, data);
});

$('#guide-close').addEventListener('click', () => $('#guide-modal').close());
$('#guide-modal').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) e.currentTarget.close();
});

function openGuide(name, { guide, locations }) {
  $('#guide-title').textContent = name;
  $('#guide-body').innerHTML = guide ? miniMarkdown(guide.body_md) : '<p>No guide text yet.</p>';
  $('#guide-map').innerHTML = locations?.length ? mapFigure(locations) : '';
  $('#guide-attrib').textContent = guide?.source_name
    ? `Guide: ${guide.source_name}${guide.source_url ? ` — ${guide.source_url}` : ''}`
    : '';
  $('#guide-modal').showModal();
}

// Zone map: 3:2 SVG (WoW map aspect) with a coordinate grid and numbered pins.
// Coords are the addon-standard 0–100 map percentages. If you drop a zone image
// at public/maps/<map_id>.jpg it renders as the background automatically
// (pin math is identical either way); otherwise you get the grid.
function mapFigure(locs) {
  const W = 100, H = 66.6, sy = H / 100;
  const grid = [];
  for (let x = 10; x < 100; x += 10)
    grid.push(`<line x1="${x}" y1="0" x2="${x}" y2="${H}"/>`);
  for (let y = 10; y < 100; y += 10)
    grid.push(`<line x1="0" y1="${y * sy}" x2="${W}" y2="${y * sy}"/>`);
  const tile = locs[0].map_id
    ? `<image href="/maps/${Number(locs[0].map_id)}.jpg" x="0" y="0" width="${W}" height="${H}"
              preserveAspectRatio="xMidYMid slice"/>` // absent file renders nothing → grid shows
    : '';

  const pins = locs.map((l, i) => {
    const x = Math.min(98, Math.max(2, l.coord_x));
    const y = Math.min(98, Math.max(2, l.coord_y)) * sy;
    return `<g>
      <circle cx="${x}" cy="${y}" r="3.4" fill="var(--surface)"/>
      <circle cx="${x}" cy="${y}" r="2.6" fill="var(--accent)"/>
      <text x="${x}" y="${y + 1.05}" text-anchor="middle" font-size="3"
            font-weight="700" fill="#fff">${i + 1}</text>
    </g>`;
  }).join('');

  const zone = esc(locs[0].zone ?? '');
  return `<figure class="map-figure">
    <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Map of ${zone} with ${locs.length} location pin(s)">
      <rect width="${W}" height="${H}" fill="var(--page)" stroke="var(--grid)" stroke-width="0.3"/>
      <g stroke="var(--grid)" stroke-width="0.2">${grid.join('')}</g>
      ${tile}
      <text x="2.5" y="5" font-size="3.2" font-weight="600" fill="var(--muted)"
            paint-order="stroke" stroke="var(--surface)" stroke-width="0.6">${zone}</text>
      ${pins}
    </svg>
    <figcaption class="map-caption">Grid = in-game map coordinates (0–100)</figcaption>
    <ul class="loc-list">${locs.map((l, i) => `
      <li><span class="pin-n">${i + 1}</span>
        <span><b>${esc(l.npc_name)}</b> — ${esc(l.zone)}
          <span class="coords">(${l.coord_x}, ${l.coord_y})</span>
          ${l.note ? `· ${esc(l.note)}` : ''}</span></li>`).join('')}
    </ul>
  </figure>`;
}

// Tiny, safe markdown subset: escapes HTML, then supports **bold**, *italic*,
// `code`, "1." ordered lists and "-" bullets.
function miniMarkdown(md) {
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`(.+?)`/g, '<code>$1</code>');

  const out = [];
  let list = null; // 'ol' | 'ul'
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null; } };

  for (const raw of String(md).split('\n')) {
    const line = raw.trim();
    const ol = line.match(/^\d+\.\s+(.*)/);
    const ul = line.match(/^[-*]\s+(.*)/);
    if (ol || ul) {
      const want = ol ? 'ol' : 'ul';
      if (list !== want) { closeList(); out.push(`<${want}>`); list = want; }
      out.push(`<li>${inline((ol || ul)[1])}</li>`);
    } else {
      closeList();
      if (line) out.push(`<p>${inline(line)}</p>`);
    }
  }
  closeList();
  return out.join('');
}
