'use strict';

// ── State ──────────────────────────────────────────────────────────────────────
const S = {
  files: [],           // {name, path, size_mb}[]
  groups: [],          // {id, files: path[], registration, metadata, keywords, organized}[]
  selected: new Set(), // paths selected in triage
  view: 'triage',
  activeGroupId: null,
  nextId: 1,
};
let _allKeywords = []; // cached from catalog

// ── Utils ──────────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

function thumbUrl(path)   { return `/api/thumbnail?path=${encodeURIComponent(path)}`; }
function previewUrl(path) { return `/api/preview?path=${encodeURIComponent(path)}`; }

function unsorted() {
  const inGroup = new Set(S.groups.flatMap(g => g.files));
  return S.files.filter(f => !inGroup.has(f.path));
}
function activeGroup()   { return S.groups.find(g => g.id === S.activeGroupId) ?? null; }
function fileByPath(p)   { return S.files.find(f => f.path === p); }

let _toastTimer;
function toast(msg, ms = 3000) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

async function api(method, url, body) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const r = await fetch(url, opts);
  return r.json().catch(() => ({ error: `HTTP ${r.status}` }));
}

async function apiWithRetry(method, url, body, { retries = 3, delayMs = 2000 } = {}) {
  for (let attempt = 1; attempt <= retries; attempt++) {
    try {
      const result = await api(method, url, body);
      if (result.success !== false) return result;
      // Retry on server-side failures too, unless it's a hard error
      if (attempt === retries) return result;
    } catch (e) {
      if (attempt === retries) return { success: false, error: String(e) };
    }
    await new Promise(r => setTimeout(r, delayMs * attempt));
  }
}

// ── Actions ────────────────────────────────────────────────────────────────────
async function scanInbox() {
  const inbox = $('inbox-path').value.trim();
  $('scan-btn').textContent = '⏳ Scanning…';
  $('scan-btn').disabled = true;
  const data = await api('GET', `/api/scan?inbox=${encodeURIComponent(inbox)}`);
  $('scan-btn').textContent = '📂 Scan Inbox';
  $('scan-btn').disabled = false;
  if (data.error) { toast(`❌ ${data.error}`); return; }
  S.files = data.files;
  S.groups = [];
  S.selected.clear();
  if (data.shoot_date) $('date-input').value = data.shoot_date;
  toast(`Found ${S.files.length} photo${S.files.length !== 1 ? 's' : ''}${data.shoot_date ? ` · date set to ${data.shoot_date}` : ''}`);
  render();
}

function toggleSelect(path) {
  S.selected.has(path) ? S.selected.delete(path) : S.selected.add(path);
  renderTriage();
}

function createGroupFromSelected(extraPaths = []) {
  const paths = [...new Set([...S.selected, ...extraPaths])];
  if (paths.length === 0) return;
  S.groups.push({ id: S.nextId++, files: paths, registration: '', metadata: null, keywords: [] });
  S.selected.clear();
  render();
}

function addToGroup(groupId, paths) {
  const g = S.groups.find(gr => gr.id === groupId);
  if (!g) return;
  for (const p of paths) if (!g.files.includes(p)) g.files.push(p);
  S.selected.clear();
  render();
}

function removeGroup(id) { S.groups = S.groups.filter(g => g.id !== id); render(); }

function editGroup(id) {
  S.activeGroupId = id;
  S.view = 'group';
  $('rego-input').value = activeGroup()?.registration ?? '';
  render();
}

function backToTriage() {
  const g = activeGroup();
  if (g) g.registration = $('rego-input').value.toUpperCase().trim();
  S.view = 'triage';
  S.activeGroupId = null;
  render();
}

// Remove photo from a group — file goes back to the unsorted pool
function returnToPool(groupId, path) {
  const g = S.groups.find(gr => gr.id === groupId);
  if (!g) return;
  g.files = g.files.filter(p => p !== path);
  render();
}

// Delete photo from disk entirely
async function deletePhoto(path) {
  // Remove from any group and from file list
  S.groups.forEach(g => { g.files = g.files.filter(p => p !== path); });
  S.files = S.files.filter(f => f.path !== path);
  render();
  await api('POST', '/api/delete', { path, output: $('output-path').value });
}

async function lookupForGroup(groupId, registration) {
  if (!registration) return;
  const g = S.groups.find(gr => gr.id === groupId);
  if (!g) return;
  const data = await api('POST', '/api/lookup', { registration });
  if (data.success) {
    g.registration = registration;
    g.metadata = data.data;
    toast(`✓ ${registration} — ${data.data.airline}`);
  } else {
    const msgs = { reg_not_found: `${registration} not found`, wrong_reg: 'Wrong reg returned — check spelling' };
    toast(`❌ ${msgs[data.data?.reason] ?? data.data?.error ?? 'Lookup failed'}`);
    g.metadata = null;
  }
  render();
}


async function lookupRegistration() {
  const registration = $('rego-input').value.toUpperCase().trim();
  const g = activeGroup();
  if (!registration || !g) return;
  const btn = $('lookup-btn');
  btn.textContent = '⏳…'; btn.disabled = true;
  const data = await api('POST', '/api/lookup', { registration });
  btn.textContent = 'Lookup →'; btn.disabled = false;
  if (data.success) {
    g.registration = registration;
    g.metadata = data.data;
  } else {
    const msgs = { reg_not_found: `${registration} not found`, wrong_reg: 'Wrong reg — check spelling' };
    toast(`❌ ${msgs[data.data?.reason] ?? data.data?.error ?? 'Lookup failed'}`);
    g.metadata = null;
  }
  renderGroupControls(g);
}

async function lookupAll() {
  const pending = S.groups.filter(g => g.registration && !g.metadata);
  if (pending.length === 0) { toast('No groups with registration to look up'); return; }
  const btn = $('lookup-all-btn');
  if (btn) { btn.textContent = '⏳…'; btn.disabled = true; }
  let ok = 0; const failures = [];
  for (const g of pending) {
    const data = await api('POST', '/api/lookup', { registration: g.registration });
    if (data.success) {
      g.metadata = data.data;
      ok++;
    } else {
      const reason = data.data?.reason ?? 'error';
      failures.push(`${g.registration} (${reason})`);
    }
  }
  if (btn) { btn.textContent = 'Lookup All'; btn.disabled = false; }
  if (failures.length) toast(`❌ Failed: ${failures.join(', ')}`, 6000);
  else toast(`✓ Lookup All: ${ok} found`);
  render();
}

async function organizeGroup() {
  const g = activeGroup();
  if (!g?.metadata) return;
  const btn = $('organize-btn');
  btn.textContent = '⏳ Moving…'; btn.disabled = true;
  const data = await api('POST', '/api/organize', {
    paths: g.files,
    registration: g.registration,
    airline: g.metadata.airline,
    aircraft_manufacturer: g.metadata.aircraft_manufacturer,
    aircraft_type: g.metadata.aircraft_type,
    aircraft_url: g.metadata.aircraft_url,
    date: $('date-input').value,
    airport: $('airport-input').value.toUpperCase(),
    output: $('output-path').value,
    catalog: $('catalog-path').value,
    session: sessionValue(),
    keywords: g.keywords || [],
  });
  btn.textContent = '✅ Move to Output Folder'; btn.disabled = false;
  if (data.success) {
    const moved = new Set(g.files);
    S.files = S.files.filter(f => !moved.has(f.path));
    S.groups = S.groups.filter(gr => gr.id !== g.id);
    const catNote = data.catalog_errors?.length
      ? ` · ⚠️ LR: ${data.catalog_errors[0]}`
      : data.catalog_updated > 0 ? ` · LR ✓ ${data.catalog_updated}` : '';
    toast(`✅ Moved ${data.moved} photo${data.moved !== 1 ? 's' : ''} → ${data.destination}${catNote}`, 6000);
    backToTriage();
  } else {
    toast(`❌ ${data.error}`);
  }
}

// ── Render: triage ─────────────────────────────────────────────────────────────
function renderTriage() {
  const uns = unsorted();
  $('unsorted-count').textContent = `(${uns.length})`;

  const hasSelected = S.selected.size > 0;
  $('new-group-zone').classList.toggle('active', hasSelected);
  $('new-group-label').textContent = hasSelected
    ? `+ New Group (${S.selected.size} selected)`
    : 'Select or drag photos here to create a group';

  // Show/hide "Clear Selection" button
  const clearSelBtn = $('clear-sel-btn');
  if (clearSelBtn) clearSelBtn.classList.toggle('hidden', S.selected.size === 0);

  // Unsorted grid
  const grid = $('unsorted-grid');
  grid.innerHTML = '';
  if (S.files.length === 0) {
    grid.innerHTML = '<p class="empty">Scan the inbox to see photos</p>';
  } else if (uns.length === 0) {
    grid.innerHTML = '<p class="empty">All photos are grouped</p>';
  } else {
    for (const f of uns) grid.appendChild(makeTriageCard(f));
  }

  // Groups sidebar
  const scroll = $('groups-scroll');
  scroll.innerHTML = '';
  if (S.groups.length === 0) {
    scroll.innerHTML = '<p class="empty" style="padding:12px">No groups yet</p>';
  } else {
    for (const g of S.groups) scroll.appendChild(makeGroupCard(g));
  }
}

function makeTriageCard(file) {
  const div = document.createElement('div');
  div.className = `photo-card${S.selected.has(file.path) ? ' selected' : ''}`;
  div.dataset.path = file.path;

  const img = document.createElement('img');
  img.src = thumbUrl(file.path);
  img.loading = 'lazy';
  img.alt = file.name;
  div.appendChild(img);

  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = `${file.name}  ·  ${file.size_mb} MB`;
  div.appendChild(label);

  if (S.selected.has(file.path)) {
    const badge = document.createElement('div');
    badge.className = 'check-badge';
    badge.textContent = '✓';
    div.appendChild(badge);
  }

  // Zoom button
  const zoom = document.createElement('button');
  zoom.className = 'card-btn zoom-btn';
  zoom.title = 'Zoom preview (⤢)';
  zoom.textContent = '⤢';
  zoom.addEventListener('click', e => { e.stopPropagation(); openLightbox(file.path, unsorted().map(f => f.path)); });
  div.appendChild(zoom);

  // Drag handle — only this element is draggable, so rubber-band works everywhere else
  const handle = document.createElement('div');
  handle.className = 'drag-handle';
  handle.title = 'Drag to group';
  handle.textContent = '⠿';
  handle.setAttribute('draggable', 'true');
  handle.addEventListener('dragstart', e => {
    e.stopPropagation();
    if (!S.selected.has(file.path)) {
      S.selected.clear();
      S.selected.add(file.path);
      renderTriage();
    }
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', 'photos');
  });
  div.appendChild(handle);

  return div;
}

function makeGroupCard(g) {
  const card = document.createElement('div');
  card.className = 'group-card';

  // Header
  const hdr = document.createElement('div');
  hdr.className = 'gc-header';
  hdr.innerHTML = `<span class="gc-name">Group ${g.id}</span><span class="gc-count">${g.files.length} photo${g.files.length !== 1 ? 's' : ''}</span>`;
  const del = document.createElement('button');
  del.className = 'gc-del'; del.title = 'Discard group'; del.textContent = '✕';
  del.onclick = e => { e.stopPropagation(); removeGroup(g.id); };
  hdr.appendChild(del);
  card.appendChild(hdr);

  // Thumbnails
  const thumbs = document.createElement('div');
  thumbs.className = 'gc-thumbs';
  g.files.slice(0, 4).forEach(path => {
    const img = document.createElement('img');
    img.src = thumbUrl(path); img.loading = 'lazy';
    img.style.cursor = 'zoom-in';
    img.addEventListener('click', e => { e.stopPropagation(); openLightbox(path, g.files, g.id); });
    thumbs.appendChild(img);
  });
  if (g.files.length > 4) {
    const more = document.createElement('div');
    more.className = 'gc-more'; more.textContent = `+${g.files.length - 4}`;
    thumbs.appendChild(more);
  }
  card.appendChild(thumbs);

  // ── Inline rego input ──────────────────────────────────────────────────────
  const regoRow = document.createElement('div');
  regoRow.className = 'gc-rego-row';

  const regoInput = document.createElement('input');
  regoInput.className = 'gc-rego-input';
  regoInput.placeholder = 'Reg';
  regoInput.maxLength = 10;
  regoInput.value = g.registration;
  regoInput.addEventListener('input', e => {
    const pos = e.target.selectionStart;
    e.target.value = e.target.value.toUpperCase();
    e.target.setSelectionRange(pos, pos);
    g.registration = e.target.value;
    g.metadata = null;
  });

  regoRow.appendChild(regoInput);
  card.appendChild(regoRow);

  // Metadata line (shown after successful lookup)
  if (g.metadata) {
    const meta = document.createElement('div');
    meta.className = 'gc-reg';
    meta.innerHTML = `<strong>${g.registration}</strong> · ${g.metadata.airline} · ${g.metadata.aircraft_type}`;
    card.appendChild(meta);
  }

  // Compact tag chips
  const tagRow = document.createElement('div');
  tagRow.className = 'gc-tags';
  (g.keywords || []).forEach(kw => {
    const chip = document.createElement('span');
    chip.className = 'gc-tag-chip';
    chip.textContent = kw;
    const del = document.createElement('button');
    del.className = 'chip-del'; del.textContent = '×';
    del.onclick = e => { e.stopPropagation(); g.keywords = g.keywords.filter(k => k !== kw); render(); };
    chip.appendChild(del);
    tagRow.appendChild(chip);
  });
  const addTagBtn = document.createElement('button');
  addTagBtn.className = 'gc-tag-add'; addTagBtn.textContent = '＋';
  addTagBtn.onclick = e => {
    e.stopPropagation();
    // Remove any open dropdowns first
    document.querySelectorAll('.gc-tag-dropdown').forEach(el => el.remove());

    const available = _allKeywords.filter(k => !(g.keywords || []).includes(k));
    if (!available.length) return;

    const drop = document.createElement('div');
    drop.className = 'tag-suggestions gc-tag-dropdown';
    drop.style.cssText = 'position:absolute;z-index:200;min-width:140px';
    available.slice(0, 20).forEach(kw => {
      const item = document.createElement('div');
      item.className = 'tag-suggestion'; item.textContent = kw;
      item.onmousedown = ev => {
        ev.preventDefault(); ev.stopPropagation();
        addTagToGroup(g, kw); drop.remove(); render();
      };
      drop.appendChild(item);
    });

    addTagBtn.style.position = 'relative';
    addTagBtn.appendChild(drop);
    const close = () => { drop.remove(); document.removeEventListener('click', close); };
    setTimeout(() => document.addEventListener('click', close), 0);
  };
  tagRow.appendChild(addTagBtn);
  card.appendChild(tagRow);

  // Edit button
  const editBtn = document.createElement('button');
  editBtn.className = 'gc-edit'; editBtn.textContent = 'Edit / Organize →';
  editBtn.onclick = () => editGroup(g.id);
  card.appendChild(editBtn);

  // Drop zone: drag photos onto an existing group
  card.addEventListener('dragover', e => { e.preventDefault(); card.classList.add('drag-over'); });
  card.addEventListener('dragleave', () => card.classList.remove('drag-over'));
  card.addEventListener('drop', e => {
    e.preventDefault(); card.classList.remove('drag-over');
    addToGroup(g.id, [...S.selected]);
  });

  return card;
}

// ── Render: group edit ─────────────────────────────────────────────────────────
function renderGroup() {
  const g = activeGroup();
  if (!g) return;
  $('gv-title').textContent = `Group ${g.id} · ${g.files.length} photo${g.files.length !== 1 ? 's' : ''}`;

  const grid = $('gv-grid');
  grid.innerHTML = '';
  const filePaths = g.files.slice();
  for (const path of filePaths) {
    const f = fileByPath(path);
    if (f) grid.appendChild(makeGroupPhoto(f, g.id, filePaths));
  }

  renderGroupControls(g);
}

function makeGroupPhoto(file, groupId, allPaths) {
  const div = document.createElement('div');
  div.className = 'photo-card';

  const img = document.createElement('img');
  img.src = thumbUrl(file.path); img.loading = 'lazy'; img.alt = file.name;
  div.appendChild(img);

  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = `${file.name}  ·  ${file.size_mb} MB`;
  div.appendChild(label);

  // Zoom — positioned right of the return+delete buttons so they don't overlap
  const zoom = document.createElement('button');
  zoom.className = 'card-btn zoom-btn'; zoom.title = 'Zoom'; zoom.textContent = '⤢';
  zoom.style.right = '55px';
  zoom.addEventListener('click', e => { e.stopPropagation(); openLightbox(file.path, allPaths, groupId); });
  div.appendChild(zoom);

  // Return to pool (← icon)
  const ret = document.createElement('button');
  ret.className = 'card-btn return-btn'; ret.title = 'Return to pool (undo grouping)'; ret.textContent = '↩';
  ret.addEventListener('click', e => { e.stopPropagation(); returnToPool(groupId, file.path); });
  div.appendChild(ret);

  // Delete from disk
  const del = document.createElement('button');
  del.className = 'card-btn del-btn'; del.title = 'Delete from disk'; del.textContent = '🗑';
  del.addEventListener('click', e => { e.stopPropagation(); deletePhoto(file.path); });
  div.appendChild(del);

  return div;
}

function renderGroupControls(g) {
  const metaDiv = $('meta-display');
  const destDiv = $('dest-preview');
  const orgBtn  = $('organize-btn');
  if (g.metadata) {
    const m = g.metadata;
    metaDiv.innerHTML = `
      <div class="meta-item"><label>Registration</label><span>${m.registration}</span></div>
      <div class="meta-item"><label>Airline</label><span>${m.airline}</span></div>
      <div class="meta-item"><label>Manufacturer</label><span>${m.aircraft_manufacturer}</span></div>
      <div class="meta-item"><label>Type</label><span>${m.aircraft_type}</span></div>`;
    metaDiv.classList.remove('hidden');
    const date = $('date-input').value.replace(/-/g, '');
    const airport = $('airport-input').value.toUpperCase();
    destDiv.innerHTML = `📁 <code>${date} - ${airport} / ${m.airline} / ${m.registration}</code>`;
    destDiv.classList.remove('hidden');
    orgBtn.classList.remove('hidden');
    orgBtn.disabled = g.files.length === 0;
  } else {
    metaDiv.classList.add('hidden');
    destDiv.classList.add('hidden');
    orgBtn.classList.add('hidden');
  }
  renderTagEditor(g);
}

// ── Move All ───────────────────────────────────────────────────────────────────
async function moveAll() {
  const btn = $('move-all-btn');

  // Auto-lookup any group that has a rego typed but hasn't been looked up yet
  const needsLookup = S.groups.filter(g => g.registration?.trim() && !g.metadata && g.files.length > 0);
  if (needsLookup.length > 0) {
    if (btn) { btn.textContent = `⏳ Looking up ${needsLookup.length}…`; btn.disabled = true; }
    for (const g of needsLookup) {
      await lookupForGroup(g.id, g.registration);
    }
    render();
    if (btn) { btn.textContent = 'Move All ✈'; btn.disabled = false; }
  }

  // Re-classify after lookups
  const ready  = S.groups.filter(g => g.metadata && g.files.length > 0);
  const noRego = S.groups.filter(g => !g.registration?.trim() && g.files.length > 0);
  const failed = S.groups.filter(g => g.registration?.trim() && !g.metadata && g.files.length > 0);

  if (ready.length === 0) {
    if (noRego.length > 0)
      toast(`${noRego.length} group${noRego.length !== 1 ? 's' : ''} still need${noRego.length === 1 ? 's' : ''} a registration — fill in REGOs first`, 5000);
    else
      toast('Lookup failed for all groups — check the registrations and try again', 5000);
    return;
  }

  const totalPhotos = ready.reduce((n, g) => n + g.files.length, 0);
  let html = `<p><strong>${ready.length} group${ready.length !== 1 ? 's' : ''}</strong> · ${totalPhotos} photo${totalPhotos !== 1 ? 's' : ''} will be moved.</p>`;
  if (noRego.length > 0)
    html += `<p style="color:var(--danger)"><strong>${noRego.length} group${noRego.length !== 1 ? 's' : ''}</strong> skipped — no registration filled.</p>`;
  if (failed.length > 0)
    html += `<p style="color:var(--danger)"><strong>${failed.length} group${failed.length !== 1 ? 's' : ''}</strong> skipped — lookup failed.</p>`;
  html += `<p>Output: <code style="font-size:11px">${$('output-path').value}</code></p>`;
  $('move-all-summary').innerHTML = html;
  $('move-all-modal').classList.remove('hidden');

  $('move-all-cancel').onclick  = () => $('move-all-modal').classList.add('hidden');
  $('move-all-confirm').onclick = () => {
    $('move-all-modal').classList.add('hidden');
    doMoveAll(ready);
  };
}

async function doMoveAll(groups) {
  const btn = $('move-all-btn');
  if (btn) { btn.textContent = '⏳…'; btn.disabled = true; }

  let movedTotal = 0;
  const failedGroups = [];

  for (const g of groups) {
    if (btn) btn.textContent = `⏳ Moving ${g.registration}…`;
    const data = await apiWithRetry('POST', '/api/organize', {
      paths: g.files,
      registration: g.registration,
      airline: g.metadata.airline,
      aircraft_manufacturer: g.metadata.aircraft_manufacturer,
      aircraft_type: g.metadata.aircraft_type,
      aircraft_url: g.metadata.aircraft_url,
      date: $('date-input').value,
      airport: $('airport-input').value.toUpperCase(),
      output: $('output-path').value,
      catalog: $('catalog-path').value,
      session: sessionValue(),
      keywords: g.keywords || [],
    }, { retries: 3, delayMs: 1500 });
    if (data.success) {
      movedTotal += data.moved;
      const movedSet = new Set(g.files);
      S.files  = S.files.filter(f => !movedSet.has(f.path));
      S.groups = S.groups.filter(gr => gr.id !== g.id);
    } else {
      failedGroups.push(g);
      toast(`❌ ${g.registration}: ${data.error}`, 5000);
    }
  }

  if (btn) { btn.textContent = 'Move All ✈'; btn.disabled = false; }

  const orphans = unsorted();
  if (orphans.length === 0) {
    toast(`✅ Done — moved ${movedTotal} photo${movedTotal !== 1 ? 's' : ''}`, 5000);
    await cleanupInbox();
    render();
    return;
  }

  // Orphans remain — ask what to do
  $('orphan-summary').innerHTML =
    `<strong>${orphans.length} photo${orphans.length !== 1 ? 's' : ''}</strong> still in the pool with no group assigned.`;
  $('orphan-modal').classList.remove('hidden');

  $('orphan-leave').onclick = () => {
    $('orphan-modal').classList.add('hidden');
    toast(`✅ Moved ${movedTotal} photos · ${orphans.length} left in pool`);
    render();
  };

  $('orphan-move').onclick = async () => {
    $('orphan-modal').classList.add('hidden');
    $('orphan-move').disabled = true;
    const data = await api('POST', '/api/move-orphans', {
      paths: orphans.map(f => f.path),
      date: $('date-input').value,
      airport: $('airport-input').value.toUpperCase(),
      output: $('output-path').value,
      session: sessionValue(),
    });
    if (data.success) {
      const movedSet = new Set(orphans.map(f => f.path));
      S.files = S.files.filter(f => !movedSet.has(f.path));
      toast(`✅ Moved ${movedTotal + data.moved} photos total · orphans → ${data.destination}`);
    } else {
      toast(`❌ ${data.error}`);
    }
    if (S.files.length === 0) await cleanupInbox();
    render();
  };

  $('orphan-delete').onclick = async () => {
    $('orphan-modal').classList.add('hidden');
    const data = await api('POST', '/api/delete-batch', { paths: orphans.map(f => f.path), output: $('output-path').value });
    const deletedSet = new Set(orphans.map(f => f.path));
    S.files = S.files.filter(f => !deletedSet.has(f.path));
    toast(`✅ Moved ${movedTotal} photos · deleted ${data.deleted} orphan${data.deleted !== 1 ? 's' : ''}`);
    if (S.files.length === 0) await cleanupInbox();
    render();
  };
}

async function cleanupInbox() {
  const data = await api('POST', '/api/cleanup-inbox', { inbox: $('inbox-path').value });
  if (data.removed?.length) {
    console.log(`[cleanup] removed ${data.removed.length} empty folder(s):`, data.removed);
  }
}

// ── Top-level render ───────────────────────────────────────────────────────────
function render() {
  if (S.view === 'triage') {
    $('triage-view').classList.remove('hidden');
    $('group-view').classList.add('hidden');
    renderTriage();
  } else {
    $('triage-view').classList.add('hidden');
    $('group-view').classList.remove('hidden');
    renderGroup();
  }
}

// ── Box selection — works anywhere in grid, not just empty space ───────────────
const boxState = { active: false, x0: 0, y0: 0, moved: false, targetCard: null };
const selRect  = $('sel-rect');

function rectsOverlap(a, b) {
  return a.left < b.right && a.right > b.left && a.top < b.bottom && a.bottom > b.top;
}

function clearSelection() {
  S.selected.clear();
  renderTriage();
}

$('unsorted-grid').addEventListener('mousedown', e => {
  // Skip if clicking a button or drag handle — let those receive their own click events
  if (e.button !== 0 || e.target.closest('.card-btn') || e.target.closest('.drag-handle')) return;
  boxState.active = true;
  boxState.x0 = e.clientX;
  boxState.y0 = e.clientY;
  boxState.moved = false;
  boxState.targetCard = e.target.closest('.photo-card');
  e.preventDefault(); // only called when NOT on a button, safe to use here
});

document.addEventListener('mousemove', e => {
  if (!boxState.active) return;
  const dx = e.clientX - boxState.x0, dy = e.clientY - boxState.y0;
  if (!boxState.moved && Math.hypot(dx, dy) > 6) {
    boxState.moved = true;
    selRect.style.display = 'block';
  }
  if (!boxState.moved) return;

  const x = Math.min(e.clientX, boxState.x0), y = Math.min(e.clientY, boxState.y0);
  const w = Math.abs(dx), h = Math.abs(dy);
  selRect.style.cssText = `display:block;left:${x}px;top:${y}px;width:${w}px;height:${h}px`;

  const rb = { left: x, right: x + w, top: y, bottom: y + h };
  $('unsorted-grid').querySelectorAll('.photo-card').forEach(card => {
    card.classList.toggle('box-hover', rectsOverlap(rb, card.getBoundingClientRect()));
  });
});

document.addEventListener('mouseup', () => {
  if (!boxState.active) return;
  boxState.active = false;
  selRect.style.display = 'none';

  if (!boxState.moved) {
    if (boxState.targetCard) {
      // Click on a card → toggle it
      toggleSelect(boxState.targetCard.dataset.path);
    } else {
      // Click on empty space → clear all selection
      clearSelection();
    }
  } else {
    // Rubber-band: add all highlighted cards to selection
    $('unsorted-grid').querySelectorAll('.photo-card.box-hover').forEach(card => {
      S.selected.add(card.dataset.path);
      card.classList.remove('box-hover');
    });
    renderTriage();
  }
});

// ── New-group drop zone ────────────────────────────────────────────────────────
$('new-group-zone').addEventListener('click', () => createGroupFromSelected());
$('new-group-zone').addEventListener('dragover', e => { e.preventDefault(); $('new-group-zone').classList.add('drag-over'); });
$('new-group-zone').addEventListener('dragleave', () => $('new-group-zone').classList.remove('drag-over'));
$('new-group-zone').addEventListener('drop', e => {
  e.preventDefault(); $('new-group-zone').classList.remove('drag-over');
  createGroupFromSelected();
});

// ── Lightbox ───────────────────────────────────────────────────────────────────
const lb = { paths: [], idx: 0, scale: 1, tx: 0, ty: 0, dragging: false, mx: 0, my: 0, groupId: null };

function lbApplyTransform() {
  $('lb-img').style.transform = `translate(${lb.tx}px,${lb.ty}px) scale(${lb.scale})`;
}
function lbReset() { lb.scale = 1; lb.tx = 0; lb.ty = 0; lbApplyTransform(); }

function lbShow(idx) {
  lb.idx = Math.max(0, Math.min(lb.paths.length - 1, idx));
  lbReset();
  const path = lb.paths[lb.idx];
  const f = fileByPath(path);
  $('lb-img').style.opacity = '0';
  $('lb-img').src = previewUrl(path);   // full-res embedded JPEG
  $('lb-img').onload = () => { $('lb-img').style.opacity = '1'; };
  $('lb-filename').textContent = f?.name ?? '';
  $('lb-counter').textContent  = `${lb.idx + 1} / ${lb.paths.length}`;
  $('lb-prev').classList.toggle('hidden', lb.paths.length <= 1);
  $('lb-next').classList.toggle('hidden', lb.paths.length <= 1);
  $('lb-return').classList.toggle('hidden', lb.groupId === null);
  const thumbs = $('lb-strip').querySelectorAll('.lb-strip-thumb');
  thumbs.forEach((t, i) => t.classList.toggle('active', i === lb.idx));
  if (thumbs[lb.idx]) thumbs[lb.idx].scrollIntoView({ inline: 'nearest', block: 'nearest' });
}

function lbBuildStrip(paths) {
  const strip = $('lb-strip');
  strip.innerHTML = '';
  if (paths.length <= 1) { strip.classList.add('hidden'); return; }
  strip.classList.remove('hidden');
  paths.forEach((p, i) => {
    const img = document.createElement('img');
    img.className = 'lb-strip-thumb';
    img.src = thumbUrl(p);
    img.loading = 'lazy';
    img.draggable = false;
    img.addEventListener('click', e => { e.stopPropagation(); lbShow(i); });
    strip.appendChild(img);
  });
}

function openLightbox(path, paths, groupId = null) {
  lb.paths   = paths.slice();
  lb.groupId = groupId;
  lb.idx     = Math.max(0, paths.indexOf(path));
  lbBuildStrip(lb.paths);
  lbShow(lb.idx);
  $('lightbox').classList.remove('hidden');
}
function closeLightbox() { $('lightbox').classList.add('hidden'); }

function lbRemoveCurrent() {
  lb.paths.splice(lb.idx, 1);
  if (lb.paths.length === 0) { closeLightbox(); return; }
  lb.idx = Math.min(lb.idx, lb.paths.length - 1);
  lbBuildStrip(lb.paths);
  lbShow(lb.idx);
}

// Scroll to zoom
$('lb-body').addEventListener('wheel', e => {
  e.preventDefault();
  lb.scale = Math.max(0.5, Math.min(8, lb.scale * (e.deltaY > 0 ? 0.88 : 1.14)));
  lbApplyTransform();
}, { passive: false });

// Drag to pan
$('lb-body').addEventListener('mousedown', e => {
  if (e.button !== 0) return;
  lb.dragging = true; lb.mx = e.clientX; lb.my = e.clientY;
  $('lb-body').classList.add('dragging');
});
document.addEventListener('mousemove', e => {
  if (!lb.dragging) return;
  lb.tx += e.clientX - lb.mx; lb.ty += e.clientY - lb.my;
  lb.mx = e.clientX; lb.my = e.clientY;
  lbApplyTransform();
});
document.addEventListener('mouseup', () => { lb.dragging = false; $('lb-body').classList.remove('dragging'); });
$('lb-body').addEventListener('dblclick', lbReset);

$('lb-prev').addEventListener('click', e => { e.stopPropagation(); lbShow(lb.idx - 1); });
$('lb-next').addEventListener('click', e => { e.stopPropagation(); lbShow(lb.idx + 1); });
$('lb-close').addEventListener('click', closeLightbox);

$('lb-delete').addEventListener('click', e => {
  e.stopPropagation();
  const path = lb.paths[lb.idx];
  lbRemoveCurrent();
  deletePhoto(path);
});

$('lb-return').addEventListener('click', e => {
  e.stopPropagation();
  const path    = lb.paths[lb.idx];
  const groupId = lb.groupId;
  lbRemoveCurrent();
  returnToPool(groupId, path);
});

document.addEventListener('keydown', e => {
  if ($('lightbox').classList.contains('hidden')) return;
  if (e.key === 'Escape')     closeLightbox();
  if (e.key === 'ArrowLeft')  lbShow(lb.idx - 1);
  if (e.key === 'ArrowRight') lbShow(lb.idx + 1);
});

// ── Session picker ─────────────────────────────────────────────────────────────
function _savedKeywords() {
  try { return JSON.parse(localStorage.getItem('ss_keywords') || '[]'); } catch { return []; }
}
function _persistKeyword(kw) {
  const saved = _savedKeywords();
  if (!saved.includes(kw)) { saved.push(kw); localStorage.setItem('ss_keywords', JSON.stringify(saved)); }
}

async function loadKeywords() {
  const catalog = $('catalog-path').value.trim();
  const fromStorage = _savedKeywords();
  let fromCatalog = [];
  if (catalog) {
    const data = await api('GET', `/api/keywords?catalog=${encodeURIComponent(catalog)}`);
    fromCatalog = data.keywords || [];
  }
  // Merge: catalog first (alphabetical), then any locally-saved extras not yet in catalog
  const merged = [...fromCatalog];
  fromStorage.forEach(k => { if (!merged.includes(k)) merged.push(k); });
  _allKeywords = merged;
}

// ── Tag editor (group view) ────────────────────────────────────────────────────
function renderTagEditor(g) {
  const editor = $('tag-editor');
  editor.classList.remove('hidden');

  const chipsEl = $('gv-tag-chips');
  chipsEl.innerHTML = '';
  (g.keywords || []).forEach(kw => {
    const chip = document.createElement('span');
    chip.className = 'tag-chip';
    chip.textContent = kw;
    const del = document.createElement('button');
    del.className = 'chip-del'; del.textContent = '×'; del.title = 'Remove';
    del.onclick = () => { g.keywords = g.keywords.filter(k => k !== kw); renderTagEditor(g); };
    chip.appendChild(del);
    chipsEl.appendChild(chip);
  });

  const wrap = $('gv-tag-add-wrap');
  const addBtn = $('gv-tag-add-btn');

  addBtn.onclick = e => {
    e.stopPropagation();
    wrap.querySelectorAll('.tag-suggestions').forEach(el => el.remove());

    const drop = document.createElement('div');
    drop.className = 'tag-suggestions';
    drop.style.cssText = 'position:absolute;bottom:calc(100% + 4px);top:auto;left:0;right:0;max-height:220px;display:flex;flex-direction:column';

    const searchInput = document.createElement('input');
    searchInput.placeholder = 'Search or add new…';
    searchInput.style.cssText = 'background:var(--surface2);border:none;border-bottom:1px solid var(--border);color:var(--text);padding:6px 10px;font-size:12px;outline:none;flex-shrink:0;border-radius:var(--r) var(--r) 0 0';

    const listDiv = document.createElement('div');
    listDiv.style.cssText = 'overflow-y:auto;flex:1';

    function populate(query) {
      listDiv.innerHTML = '';
      const q = query.toLowerCase().trim();
      const available = _allKeywords.filter(k => !(g.keywords || []).includes(k));
      const matches = q ? available.filter(k => k.toLowerCase().includes(q)) : available;
      if (q && !_allKeywords.some(k => k.toLowerCase() === q)) {
        const newItem = document.createElement('div');
        newItem.className = 'tag-suggestion';
        newItem.innerHTML = `<em>＋ Add "<strong>${query}</strong>"</em>`;
        newItem.onmousedown = ev => { ev.preventDefault(); addTagToGroup(g, query); searchInput.value = ''; populate(''); };
        listDiv.appendChild(newItem);
      }
      matches.slice(0, 15).forEach(kw => {
        const item = document.createElement('div');
        item.className = 'tag-suggestion'; item.textContent = kw;
        item.onmousedown = ev => { ev.preventDefault(); addTagToGroup(g, kw); searchInput.value = ''; populate(''); };
        listDiv.appendChild(item);
      });
    }

    searchInput.oninput = () => populate(searchInput.value);
    searchInput.onkeydown = e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const val = searchInput.value.trim();
        if (val) { addTagToGroup(g, val); searchInput.value = ''; populate(''); }
      } else if (e.key === 'Escape') {
        drop.remove();
      }
    };

    populate('');
    drop.appendChild(searchInput);
    drop.appendChild(listDiv);
    wrap.appendChild(drop);

    const closeHandler = ev => {
      if (!drop.contains(ev.target) && ev.target !== addBtn) {
        drop.remove();
        document.removeEventListener('mousedown', closeHandler);
      }
    };
    setTimeout(() => document.addEventListener('mousedown', closeHandler), 0);
    searchInput.focus();
  };
}

function addTagToGroup(g, kw) {
  const trimmed = kw.trim();
  if (!trimmed || (g.keywords || []).includes(trimmed)) return;
  if (!g.keywords) g.keywords = [];
  g.keywords.push(trimmed);
  if (!_allKeywords.includes(trimmed)) _allKeywords.push(trimmed);
  _persistKeyword(trimmed);
  renderTagEditor(g);
}

async function loadSessions() {
  const output = $('output-path').value.trim();
  const sel = $('session-select');
  if (!sel) return;
  const prev = sel.value;

  sel.innerHTML = '<option value="">— no session —</option>';

  if (output) {
    const data = await api('GET', `/api/sessions?output=${encodeURIComponent(output)}`);
    (data.sessions || []).forEach(s => {
      const opt = document.createElement('option');
      opt.value = s; opt.textContent = s;
      sel.appendChild(opt);
    });
  }

  const newOpt = document.createElement('option');
  newOpt.value = '__new__'; newOpt.textContent = '＋ New session…';
  sel.appendChild(newOpt);

  // Restore prior selection, or auto-pick most recent
  const existing = [...sel.options].map(o => o.value);
  if (prev && prev !== '__new__' && existing.includes(prev)) {
    sel.value = prev;
  } else if (sel.options.length > 2) {
    sel.value = sel.options[1].value; // first real session (index 0 is placeholder)
  }
}

function sessionValue() {
  const v = $('session-select').value;
  return (v === '__new__' || v === '') ? '' : v;
}

// ── Page navigation ────────────────────────────────────────────────────────────
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.add('hidden'));
  document.querySelectorAll('.nav-tab').forEach(t => t.classList.toggle('active', t.dataset.page === name));
  const el = $(`page-${name}`);
  if (el) el.classList.remove('hidden');
}

// ── Settings persistence ───────────────────────────────────────────────────────
const LS_KEY = 'spotting_station_settings';

function loadSettings() {
  try { return JSON.parse(localStorage.getItem(LS_KEY) || '{}'); } catch { return {}; }
}

function saveSettings(obj) {
  localStorage.setItem(LS_KEY, JSON.stringify(obj));
}

// ── Init ───────────────────────────────────────────────────────────────────────
async function init() {
  const cfg = await api('GET', '/api/config');
  const saved = loadSettings();

  // Settings page — prefer localStorage over server defaults
  $('inbox-path').value   = saved.inbox   ?? cfg.inbox   ?? '';
  $('output-path').value  = saved.output  ?? cfg.output  ?? '';
  $('catalog-path').value = saved.catalog ?? cfg.catalog ?? '';

  // Organise toolbar — session values (date/airport not persisted)
  $('date-input').value    = cfg.date    ?? '';
  $('airport-input').value = cfg.airport ?? 'SYD';
  await loadSessions();
  loadKeywords();

  // Nav tabs
  document.querySelectorAll('.nav-tab').forEach(tab => {
    tab.addEventListener('click', () => showPage(tab.dataset.page));
  });

  // Settings save
  $('settings-save-btn').addEventListener('click', async () => {
    saveSettings({
      inbox:   $('inbox-path').value,
      output:  $('output-path').value,
      catalog: $('catalog-path').value,
    });
    await loadSessions();
    loadKeywords();
    toast('Settings saved');
  });

  // Organise page controls
  $('session-select').addEventListener('change', () => {
    if ($('session-select').value !== '__new__') return;
    const name = prompt('New session name (e.g. 2026 - Oceania):');
    if (name?.trim()) {
      const sel = $('session-select');
      const opt = document.createElement('option');
      opt.value = name.trim(); opt.textContent = name.trim();
      sel.insertBefore(opt, sel.lastElementChild); // before the ＋ New option
      sel.value = name.trim();
    } else {
      // Revert to first real session or placeholder
      const sel = $('session-select');
      sel.value = sel.options.length > 2 ? sel.options[1].value : '';
    }
  });

  $('scan-btn').onclick     = scanInbox;
  $('back-btn').onclick     = backToTriage;
  $('lookup-btn').onclick   = lookupRegistration;
  $('organize-btn').onclick = organizeGroup;

  const clearSelBtn = $('clear-sel-btn');
  if (clearSelBtn) clearSelBtn.onclick = clearSelection;
  const lookupAllBtn = $('lookup-all-btn');
  if (lookupAllBtn) lookupAllBtn.onclick = lookupAll;
  const moveAllBtn = $('move-all-btn');
  if (moveAllBtn) moveAllBtn.onclick = moveAll;

  $('rego-input').addEventListener('input', e => {
    const pos = e.target.selectionStart;
    e.target.value = e.target.value.toUpperCase();
    e.target.setSelectionRange(pos, pos);
    const g = activeGroup();
    if (g) { g.metadata = null; renderGroupControls(g); }
  });

  render();
}

document.addEventListener('DOMContentLoaded', init);
