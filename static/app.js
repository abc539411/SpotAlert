/* SpotAlert PWA — vanilla JS, no build tooling */
'use strict';

// ── Utilities ────────────────────────────────────────────────────────────────

function $(id) { return document.getElementById(id); }

function toast(msg, ms = 2000) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(el._t);
  el._t = setTimeout(() => el.classList.remove('show'), ms);
}

async function api(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json', ...opts.headers },
    ...opts,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function fmtTs(ts, opts = {}) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  return d.toLocaleString(undefined, { hour: '2-digit', minute: '2-digit', ...opts });
}

function fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function chipClass(type) {
  const map = {
    special_livery: 'chip-livery', livery: 'chip-livery',
    rare_plane: 'chip-rare', rare: 'chip-rare',
    rego_watchlist: 'chip-rego', rego: 'chip-rego',
    type_watchlist: 'chip-type', type: 'chip-type',
    airline_watchlist: 'chip-airline', airline: 'chip-airline',
    military: 'chip-military',
    route_type: 'chip-route', route: 'chip-route',
  };
  return map[type] || 'chip-unknown';
}

function chipLabel(type) {
  const map = {
    special_livery: 'Livery', livery: 'Livery',
    rare_plane: 'Rare', rare: 'Rare',
    rego_watchlist: 'Rego', rego: 'Rego',
    type_watchlist: 'Type', type: 'Type',
    airline_watchlist: 'Airline', airline: 'Airline',
    military: 'Military',
    route_type: 'Route', route: 'Route',
  };
  return map[type] || type || '?';
}

function flightCard(r) {
  const arrTime = fmtTs(r.arrival_ts, { hour: '2-digit', minute: '2-digit' });
  const type = r.notif_type || '';
  const detail = r.detail || '';
  const extra = r.extra_info || '';
  return `<div class="card">
    <div class="card-row">
      <span class="rego">${esc(r.registration)}</span>
      <span class="flight-num">${esc(r.flight_number || '')}</span>
      <span class="chip ${chipClass(type)}">${chipLabel(type)}</span>
    </div>
    ${detail ? `<div class="card-row"><span class="detail">${esc(detail)}</span></div>` : ''}
    ${extra  ? `<div class="card-row"><span class="detail"><strong>Note:</strong> ${esc(extra)}</span></div>` : ''}
    <div class="card-row"><span class="ts">Arr ${arrTime}</span></div>
  </div>`;
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Tab navigation ────────────────────────────────────────────────────────────

const TABS = ['feed', 'history', 'stats', 'filters', 'settings'];
let activeTab = 'feed';

function switchTab(name) {
  if (!TABS.includes(name)) return;
  activeTab = name;
  TABS.forEach(t => {
    $('tab-' + t).classList.toggle('hidden', t !== name);
  });
  document.querySelectorAll('.nav-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  loadTab(name);
}

$('nav-tabs').addEventListener('click', e => {
  const btn = e.target.closest('.nav-tab');
  if (btn) switchTab(btn.dataset.tab);
});

// ── Feed ──────────────────────────────────────────────────────────────────────

async function loadFeed() {
  const el = $('feed-list');
  try {
    const rows = await api('/daily');
    if (!rows.length) { el.innerHTML = '<div class="empty">No flights today.</div>'; return; }
    // newest first by arrival_ts
    rows.sort((a, b) => (b.arrival_ts || 0) - (a.arrival_ts || 0));
    el.innerHTML = rows.map(flightCard).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

// ── History ───────────────────────────────────────────────────────────────────

async function loadHistory() {
  const el = $('history-list');
  try {
    const rows = await api('/history?days=7');
    if (!rows.length) { el.innerHTML = '<div class="empty">No notifications in the last 7 days.</div>'; return; }

    // Group by date
    const groups = {};
    rows.forEach(r => {
      const dateKey = fmtDate(r.notified_ts);
      if (!groups[dateKey]) groups[dateKey] = [];
      groups[dateKey].push(r);
    });

    el.innerHTML = Object.entries(groups).map(([date, items]) => `
      <div class="date-group">
        <div class="section-heading">${esc(date)}</div>
        ${items.map(r => {
          const type = r.notif_type || '';
          const detail = r.detail || '';
          return `<div class="card">
            <div class="card-row">
              <span class="rego">${esc(r.registration)}</span>
              <span class="flight-num">${esc(r.flight_number || '')}</span>
              <span class="chip ${chipClass(type)}">${chipLabel(type)}</span>
              <span class="ts" style="margin-left:auto">${fmtTs(r.notified_ts, {hour:'2-digit',minute:'2-digit'})}</span>
            </div>
            ${detail ? `<div class="card-row"><span class="detail">${esc(detail)}</span></div>` : ''}
          </div>`;
        }).join('')}
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

// ── Stats ─────────────────────────────────────────────────────────────────────

async function loadStats() {
  const el = $('stats-grid');
  try {
    const s = await api('/stats');
    const labels = {
      special_liveries: 'Special Liveries',
      military: 'Military',
      rego_hits: 'Rego Watchlist',
      type_hits: 'Type Watchlist',
      airline_hits: 'Airline Watchlist',
    };
    el.innerHTML = Object.entries(labels).map(([k, label]) => `
      <div class="stat-card">
        <div class="stat-value">${s[k] ?? 0}</div>
        <div class="stat-label">${label}</div>
      </div>`).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

// ── Filters ───────────────────────────────────────────────────────────────────

let _filtersCache = null;

async function loadFilters() {
  try {
    _filtersCache = await api('/filters');
    renderFilters();
  } catch (e) {
    toast('Failed to load filters: ' + e.message);
  }
}

function renderFilters() {
  if (!_filtersCache) return;
  const f = _filtersCache;

  $('fl-exclusion').innerHTML = (f.exclusion_list || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.registration)}</div>
        ${r.description ? `<div class="filter-secondary">${esc(r.description)}</div>` : ''}
      </div>
      <button class="del-btn" title="Remove" onclick="delExclusion('${esc(r.registration)}')">✕</button>
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-rego').innerHTML = (f.rego_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.registration)}</div>
        ${r.description ? `<div class="filter-secondary">${esc(r.description)}</div>` : ''}
      </div>
      <button class="del-btn" onclick="delRego('${esc(r.registration)}')">✕</button>
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-type').innerHTML = (f.type_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.aircraft_type)}</div>
        <div class="filter-secondary">${esc(r.airline)}</div>
      </div>
      <button class="del-btn" onclick="delType('${esc(r.airline)}','${esc(r.aircraft_type)}')">✕</button>
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-airline').innerHTML = (f.airline_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.icao_code)} <span style="color:var(--dim);font-size:11px">${esc(r.entry_type)}</span></div>
        ${r.name ? `<div class="filter-secondary">${esc(r.name)}</div>` : ''}
      </div>
      <button class="del-btn" onclick="delAirline('${esc(r.icao_code)}','${esc(r.entry_type)}')">✕</button>
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';
}

async function addExclusion() {
  const rego = $('excl-rego').value.trim().toUpperCase();
  const desc = $('excl-desc').value.trim();
  if (!rego) { toast('Enter a registration'); return; }
  try {
    await api('/filters/exclusion', { method: 'POST', body: JSON.stringify({ registration: rego, description: desc }) });
    $('excl-rego').value = ''; $('excl-desc').value = '';
    toast('Added'); await loadFilters();
  } catch (e) { toast('Error: ' + e.message); }
}

async function delExclusion(rego) {
  try { await api('/filters/exclusion/' + encodeURIComponent(rego), { method: 'DELETE' }); toast('Removed'); await loadFilters(); }
  catch (e) { toast('Error: ' + e.message); }
}

async function addRego() {
  const rego = $('rego-rego').value.trim().toUpperCase();
  const desc = $('rego-desc').value.trim();
  if (!rego) { toast('Enter a registration'); return; }
  try {
    await api('/filters/rego', { method: 'POST', body: JSON.stringify({ registration: rego, description: desc }) });
    $('rego-rego').value = ''; $('rego-desc').value = '';
    toast('Added'); await loadFilters();
  } catch (e) { toast('Error: ' + e.message); }
}

async function delRego(rego) {
  try { await api('/filters/rego/' + encodeURIComponent(rego), { method: 'DELETE' }); toast('Removed'); await loadFilters(); }
  catch (e) { toast('Error: ' + e.message); }
}

async function addType() {
  const airline = $('type-airline').value.trim().toUpperCase();
  const ac = $('type-ac').value.trim().toUpperCase();
  if (!airline || !ac) { toast('Fill both fields'); return; }
  try {
    await api('/filters/type', { method: 'POST', body: JSON.stringify({ airline, aircraft_type: ac }) });
    $('type-airline').value = ''; $('type-ac').value = '';
    toast('Added'); await loadFilters();
  } catch (e) { toast('Error: ' + e.message); }
}

async function delType(airline, ac) {
  try {
    await api('/filters/type', { method: 'DELETE', body: JSON.stringify({ airline, aircraft_type: ac }) });
    toast('Removed'); await loadFilters();
  } catch (e) { toast('Error: ' + e.message); }
}

async function addAirline() {
  const icao = $('al-icao').value.trim().toUpperCase();
  const type = $('al-type').value.trim() || 'airline';
  const name = $('al-name').value.trim();
  if (!icao) { toast('Enter ICAO code'); return; }
  try {
    await api('/filters/airline', { method: 'POST', body: JSON.stringify({ icao_code: icao, entry_type: type, name }) });
    $('al-icao').value = ''; $('al-type').value = ''; $('al-name').value = '';
    toast('Added'); await loadFilters();
  } catch (e) { toast('Error: ' + e.message); }
}

async function delAirline(icao, type) {
  try {
    await api('/filters/airline/' + encodeURIComponent(icao) + '?entry_type=' + encodeURIComponent(type), { method: 'DELETE' });
    toast('Removed'); await loadFilters();
  } catch (e) { toast('Error: ' + e.message); }
}

// ── Settings ──────────────────────────────────────────────────────────────────

const SETTINGS_SCHEMA = [
  { group: 'monitoring', key: 'CHECK_INTERVAL_MINUTES', label: 'Check interval', desc: 'Arrivals feed polling interval (minutes)' },
  { group: 'monitoring', key: 'REMINDER_HOURS', label: 'Reminder hours', desc: 'Hours before arrival to send a reminder (0 = off)' },
  { group: 'livery', key: 'SPECIAL_LIVERY_RENOTIFY_HOURS', label: 'Re-notify cooldown (hrs)', desc: 'Minimum hours between livery alerts for the same registration' },
  { group: 'rare', key: 'RARE_PLANE_MIN_ABSENCE_DAYS', label: 'Min absence days', desc: 'Days without sighting before a type is considered rare' },
  { group: 'watchlist', key: 'REGO_WATCHLIST_RENOTIFY_HOURS', label: 'Rego cooldown (hrs)', desc: '' },
  { group: 'watchlist', key: 'TYPE_WATCHLIST_RENOTIFY_HOURS', label: 'Type cooldown (hrs)', desc: '' },
  { group: 'watchlist', key: 'AIRLINE_WATCHLIST_RENOTIFY_HOURS', label: 'Airline cooldown (hrs)', desc: '' },
  { group: 'military', key: 'MILITARY_RADIUS_NM', label: 'Radius (nm)', desc: 'Search radius for military traffic' },
  { group: 'military', key: 'MILITARY_MAX_ALT_FT', label: 'Max altitude (ft)', desc: '' },
  { group: 'military', key: 'MILITARY_RENOTIFY_HOURS', label: 'Re-notify cooldown (hrs)', desc: '' },
];

async function loadSettings() {
  try {
    const s = await api('/settings');
    const groups = ['monitoring', 'livery', 'rare', 'watchlist', 'military'];
    groups.forEach(g => {
      const el = $('settings-' + g);
      const items = SETTINGS_SCHEMA.filter(x => x.group === g);
      el.innerHTML = items.map(item => `
        <div class="setting-row">
          <div class="setting-label">
            <div class="setting-key">${item.label}</div>
            ${item.desc ? `<div class="setting-desc">${esc(item.desc)}</div>` : ''}
          </div>
          <input class="setting-input" data-key="${item.key}" value="${esc(s[item.key] ?? '')}" placeholder="—">
        </div>`).join('');
    });

    // Wire up save-on-blur
    document.querySelectorAll('.setting-input').forEach(inp => {
      inp.addEventListener('change', async () => {
        try {
          await api('/settings', { method: 'PUT', body: JSON.stringify({ [inp.dataset.key]: inp.value }) });
          toast('Saved');
        } catch (e) { toast('Error: ' + e.message); }
      });
    });
  } catch (e) {
    toast('Failed to load settings: ' + e.message);
  }
}

// ── Status polling ────────────────────────────────────────────────────────────

async function pollStatus() {
  try {
    const s = await api('/status');
    const badge = $('rapid-badge');
    badge.classList.toggle('visible', !!s.rapid_mode);
  } catch {}
}

// ── Service Worker + Install banner ──────────────────────────────────────────

function setupPWA() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  // iOS install prompt — show if running in browser (not standalone) on iOS
  const isIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isStandalone = window.navigator.standalone === true;
  if (isIOS && !isStandalone && !localStorage.getItem('install-dismissed')) {
    $('install-banner').classList.remove('hidden');
    $('install-banner').querySelector('.close-banner').addEventListener('click', () => {
      localStorage.setItem('install-dismissed', '1');
    });
  }
}

// ── Tab loader dispatcher ─────────────────────────────────────────────────────

function loadTab(name) {
  if (name === 'feed')     loadFeed();
  if (name === 'history')  loadHistory();
  if (name === 'stats')    loadStats();
  if (name === 'filters')  loadFilters();
  if (name === 'settings') loadSettings();
}

// ── Boot ─────────────────────────────────────────────────────────────────────

setupPWA();
loadTab('feed');
pollStatus();
setInterval(pollStatus, 30_000);
