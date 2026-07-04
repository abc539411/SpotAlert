/* SpotAlert PWA — vanilla JS, no build tooling */
'use strict';

// ── Utilities ────────────────────────────────────────────────────────────────

function mfrBadge(mfr) {
  if (!mfr) return '';
  const m = mfr.toLowerCase();
  let canonical = null;
  if (m.includes('boeing'))            canonical = 'Boeing';
  else if (m.includes('airbus'))       canonical = 'Airbus';
  else if (m.includes('embraer'))      canonical = 'Embraer';
  else if (m.includes('bombardier'))   canonical = 'Bombardier';
  else if (m.includes('de havilland')) canonical = 'De Havilland';
  else if (m.includes('mcdonnell'))    canonical = 'McDonnell Douglas';
  else if (m.includes('lockheed'))     canonical = 'Lockheed Martin';
  else if (m.includes('cessna'))       canonical = 'Cessna';
  else if (m.includes('gulfstream'))   canonical = 'Gulfstream';
  else if (m.includes('dassault'))     canonical = 'Dassault';
  else if (m.includes('atr'))          canonical = 'ATR';
  else if (m.includes('saab'))         canonical = 'Saab';
  else if (m.includes('fokker'))       canonical = 'Fokker';
  else if (m.includes('comac'))        canonical = 'Comac';
  else if (m.includes('antonov'))      canonical = 'Antonov';
  else if (m.includes('sukhoi'))       canonical = 'Sukhoi';
  else if (m.includes('pilatus'))      canonical = 'Pilatus';
  else if (m.includes('sikorsky'))     canonical = 'Sikorsky';
  else if (m.includes('bell'))         canonical = 'Bell';
  else if (m.includes('leonardo'))     canonical = 'Leonardo';
  else if (m.includes('bae'))          canonical = 'BAE Systems';
  if (!canonical) return '';
  const cls = canonical.toLowerCase().replace(/\s+/g, '-');
  return `<span class="mfr mfr-${cls}">${esc(canonical)}</span>`;
}

function $(id) { return document.getElementById(id); }

function toast(msg, ms = 2000, wrap = false) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.toggle('wrap', wrap);
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
  const s = d.toLocaleString(undefined, { hour: 'numeric', minute: '2-digit', hour12: true, ...opts });
  return s.replace(' AM', 'am').replace(' PM', 'pm');
}

function fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

// ── Country flags ────────────────────────────────────────────────────────────

const _REG_PREFIXES = [
  ['VH-','AU'],['VN-','VN'],['VT-','IN'],['VQ-','GB'],
  ['HS-','TH'],['HZ-','SA'],
  ['PK-','ID'],['PH-','NL'],['P2-','PG'],
  ['A7-','QA'],['A6-','AE'],['A9C','BH'],['AP-','PK'],
  ['4R-','LK'],['4X-','IL'],
  ['9V-','SG'],['9M-','MY'],['9H-','MT'],['9G-','GH'],
  ['ZK-','NZ'],['ZS-','ZA'],
  ['CC-','CL'],
  ['OE-','AT'],['OH-','FI'],['OK-','CZ'],['OM-','SK'],
  ['OY-','DK'],['OD-','LB'],
  ['LN-','NO'],['LX-','LU'],['LY-','LT'],['LZ-','BG'],
  ['SE-','SE'],['SX-','GR'],['SU-','EG'],['SP-','PL'],['S2-','BD'],
  ['EC-','ES'],['EI-','IE'],['EP-','IR'],['ET-','ET'],
  ['ES-','EE'],['EY-','AZ'],
  ['TC-','TR'],['TS-','TN'],
  ['UR-','UA'],['UK-','UZ'],['UP-','KZ'],
  ['RA-','RU'],['RF-','RU'],
  ['RP-','PH'],
  ['DQ-','FJ'],['D-','DE'],
  ['F-','FR'],['G-','GB'],
  ['CS-','PT'],['CN-','MA'],
  ['JY-','JO'],
  ['YR-','RO'],['YL-','LV'],['YA-','AF'],
  ['5N-','NG'],['5Y-','KE'],
  ['7T-','DZ'],['XU-','KH'],
];

function _regoCountryCode(rego) {
  const r = (rego || '').toUpperCase().trim();
  if (!r) return '';
  if (r.startsWith('B-')) {
    const s = r[2] || '';
    if ('HKLM'.includes(s)) return 'HK';
    if (s === '0') return 'MO';
    return 'CN';
  }
  if (r.length > 1 && r[0] === 'N' && r[1] !== '-' && /[A-Z0-9]/.test(r[1])) return 'US';
  if (r.startsWith('JA')) return 'JP';
  if (r.startsWith('HL')) return 'KR';
  for (const [pfx, cc] of _REG_PREFIXES) {
    if (r.startsWith(pfx)) return cc;
  }
  return '';
}

// Country code → emoji flag (regional indicator pair)
function _ccEmoji(cc) {
  if (!cc || cc.length !== 2) return '';
  const a = cc.toUpperCase().charCodeAt(0) - 65;
  const b = cc.toUpperCase().charCodeAt(1) - 65;
  return String.fromCodePoint(0x1F1E6 + a) + String.fromCodePoint(0x1F1E6 + b);
}

// Returns flag image on desktop, emoji on mobile
function _flag(cc, opts = {}) {
  if (!cc || cc.length !== 2) return '';
  const h   = opts.h   || 16;
  const vab = opts.vab || -2;
  const cp  = l => (0x1F1E6 + l.toUpperCase().charCodeAt(0) - 65).toString(16);
  const code = `${cp(cc[0])}-${cp(cc[1])}`;
  return `<img src="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/${code}.svg" style="height:${h}px;width:auto;vertical-align:middle;margin:0 2px;flex-shrink:0">`;
}

const _AIRPORT_CC = {
  // Australian airports (IATA + ICAO)
  SYD:'au',MEL:'au',BNE:'au',PER:'au',ADL:'au',OOL:'au',CBR:'au',HBA:'au',CNS:'au',DRW:'au',
  YSSY:'au',YMML:'au',YBBN:'au',YPPH:'au',YPAD:'au',YBCG:'au',YSCB:'au',YHBA:'au',
  // Singapore
  SIN:'sg',WSAC:'sg',WSSS:'sg',
  // Asia-Pacific
  KUL:'my',WMKK:'my',BKK:'th',VTBS:'th',HKG:'hk',VHHH:'hk',
  NRT:'jp',HND:'jp',RJTT:'jp',RJAA:'jp',ICN:'kr',RKSI:'kr',
  PEK:'cn',PVG:'cn',CAN:'cn',ZBAA:'cn',ZSPD:'cn',ZGGG:'cn',
  DEL:'in',BOM:'in',VIDP:'in',VABB:'in',
  DXB:'ae',DOH:'qa',AUH:'ae',OMDB:'ae',OTHH:'qa',OMAA:'ae',
  // Pacific Islands
  AKL:'nz',CHC:'nz',NZAA:'nz',NZCH:'nz',
  POM:'pg',AYPY:'pg',NAN:'fj',NFFN:'fj',PPT:'pf',NTAA:'pf',
  HIR:'sb',APW:'ws',TBU:'to',RAR:'ck',NOU:'nc',
  // Europe & North America
  LHR:'gb',CDG:'fr',AMS:'nl',FRA:'de',ZRH:'ch',
  JFK:'us',LAX:'us',SFO:'us',ORD:'us',
};
function _airportCountry(iata) { return _AIRPORT_CC[iata] || ''; }

function registrationFlag(rego) {
  const cc = _regoCountryCode(rego);
  return _flag(cc, { h: 11, vab: -2 });
}

// ── Chip type normalisation ───────────────────────────────────────────────────

function _normType(t) {
  const exact = {
    'Special Livery':           'special_livery',
    'Watchlist Registration':   'rego_watchlist',
    'Watchlist Aircraft Type':  'type_watchlist',
    'Watchlist Airline':        'airline_watchlist',
    'Watchlist Operator':       'operator_watchlist',
    'Rare Plane/Airline':       'rare_plane',
    'Route Equipment Change':   'route_type',
    'Military':                 'military',
  };
  return exact[t] || (t || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
}

function chipClass(type) {
  const map = {
    special_livery:    'chip-livery',
    rare_plane:        'chip-rare',
    rego_watchlist:    'chip-rego',
    type_watchlist:    'chip-type',
    airline_watchlist: 'chip-airline',
    operator_watchlist:'chip-airline',
    military:          'chip-military',
    route_type:        'chip-route',
  };
  return map[_normType(type)] || 'chip-unknown';
}

function chipLabel(type) {
  const map = {
    special_livery:    'Livery',
    rare_plane:        'Rare',
    rego_watchlist:    'Rego',
    type_watchlist:    'Type',
    airline_watchlist: 'Airline',
    operator_watchlist:'Operator',
    military:          'Military',
    route_type:        'Route',
  };
  return map[_normType(type)] || type || '?';
}

function _parseDetail(detail) {
  const m = detail.match(/^(.*?)\s*\(([^)]+)\)\s*$/);
  return m ? { airline: m[1].trim(), acType: m[2].trim() } : { airline: detail, acType: '' };
}

function sqCard(r) {
  const type    = r.notif_type || '';
  const photo   = r.photo_url || '';
  const isDep   = r._cardType === 'departure';
  const eventTs = r._eventTs || r.arrival_ts || r.notified_ts;
  const ts      = fmtTs(eventTs, { hour: '2-digit', minute: '2-digit' });
  const { airline, acType } = _parseDetail(r.detail || '');
  const encoded = esc(JSON.stringify(r));

  const airlineLogo = _airlineLogoImg(airline, 28);
  return `<div class="sq" onclick="openDetail(this)" data-r="${encoded}">
    ${photo ? `<div class="sq-bg" style="background-image:url('${esc(photo)}')"></div>` : ''}
    <div class="sq-top">
      <span class="sq-rego">${esc(r.registration)}</span>
    </div>
    <div class="sq-bottom">
      <div class="sq-row2">
        <span class="chip ${chipClass(type)}">${chipLabel(type)}</span>
        ${acType ? `<span class="fc-actype">${esc(acType)}</span>` : ''}
      </div>
      ${airline ? `<div class="sq-airline">${esc(airline)}</div>` : ''}
    </div>
    ${airlineLogo ? `<div style="position:absolute;bottom:4px;right:8px;z-index:3">${airlineLogo}</div>` : ''}
  </div>`;
}

let _gridDetailEl = null;
let _gridExpandedCard = null;

function _detailVars(r) {
  const photo = r.photo_url || '';
  return {
    type:      r.notif_type || '',
    photo,
    fullPhoto: photo.replace('/640/', '/full/').replace('/400/', '/full/'),
    ts:        fmtTs(r.notified_ts || r.arrival_ts, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }),
    ...         _parseDetail(r.detail || ''),
    extra:     r.extra_info || '',
  };
}

function _fmtLastSeen(ts) {
  if (!ts) return null;
  const tz = _feedTimezone || undefined;
  const opts = tz ? { timeZone: tz } : {};
  const d = new Date(ts * 1000);
  const label = d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric', ...opts });
  const dateStr  = ts  => new Date(ts * 1000).toLocaleDateString('en-CA', opts); // YYYY-MM-DD
  const seenDay  = dateStr(ts);
  const todayDay = dateStr(Math.floor(Date.now() / 1000));
  const msPerDay = 86400000;
  const daysAgo  = Math.round((new Date(todayDay) - new Date(seenDay)) / msPerDay);
  if (daysAgo === 0) return `${label} (today)`;
  if (daysAgo === 1) return `${label} (yesterday)`;
  return `${label} (${daysAgo} days ago)`;
}

// True only when the notification_record row is tracking the same flight as this notification_log row.
// Also requires the live arrival to be within 12h of the notification arrival to avoid matching
// the next-day rotation of a daily recurring route.
function _isSameFlight(r) {
  if (!(r.live_flight_number && r.flight_number && r.live_flight_number === r.flight_number)) return false;
  if (r.live_arrival_ts && r.arrival_ts && Math.abs(r.live_arrival_ts - r.arrival_ts) > 43200) return false;
  return true;
}

function _flightStatus(r) {
  if (!r.live_arrival_ts) return null;
  if (r.live_flight_number && r.flight_number && r.live_flight_number !== r.flight_number) return 'Departed';
  if (_isSameFlight(r) && r.live_status) return r.live_status;
  return null;
}

// ── Feed: rego-grouped cards ──────────────────────────────────────────────────

// Map status strings to our 5 canonical states (handles both FR24 raw and canonical values)
function _normStatus(raw) {
  if (!raw) return null;
  const s = raw.toLowerCase();
  if (s === 'arriving' || s === 'in flight')   return 'Arriving';
  if (s === 'arrived' || s === 'on ground' || s === 'landed') return 'Arrived';
  if (s === 'scheduled')                        return 'Scheduled';
  if (s === 'departed')                         return 'Departed';
  return null;
}

// Status for a single flight bar
function _barStatus(f, nowTs) {
  if (f.current_status) {
    const norm = _normStatus(f.current_status);
    if (norm) {
      // Stale "Arriving": if estimated arrival has already passed, drop through to timestamp logic
      // so the live fallback can resolve the actual status
      if (norm === 'Arriving' && f.arrival_ts && f.arrival_ts < nowTs) { /* fall through */ }
      else {
        if ((norm === 'Arrived' || norm === 'Scheduled') && f.dep_ts && f.dep_ts <= nowTs) return 'Departed';
        if (norm === 'Arrived' && !f.dep_ts && f.arrival_ts && (nowTs - f.arrival_ts) > 86400) return 'Departed';
        return norm;
      }
    }
  }
  // Timestamp fallback
  if (f.arrival_ts && f.arrival_ts > nowTs) return 'Scheduled';
  if (f.dep_ts && f.dep_ts > nowTs)         return 'Arrived';
  if (f.dep_ts && f.dep_ts <= nowTs)        return 'Departed';
  // No dep info — recent past arrival: assume Arrived (live fallback will confirm or mark Departed)
  if (f.arrival_ts && f.arrival_ts < nowTs && (nowTs - f.arrival_ts) < 172800) return 'Arrived';
  if (f.arrival_ts && (nowTs - f.arrival_ts) >= 172800) return 'Departed';
  return 'N/A';
}

// Card-level status = highest-priority state across all bars
const _STATUS_PRIORITY = ['Arriving', 'Arrived', 'Scheduled', 'Departed', 'N/A'];
function _cardStatus(card, nowTs) {
  const statuses = (card.flights || []).map(f => _barStatus(f, nowTs));
  for (const s of _STATUS_PRIORITY) {
    if (s === 'Departed' && statuses.some(x => x !== 'Departed' && x !== 'N/A')) continue;
    if (statuses.includes(s)) return s;
  }
  return 'N/A';
}

const _STATUS_STYLE = {
  Scheduled: ['rgba(120,120,120,0.15)', '#999'],
  Arriving:  ['rgba(245,158,11,0.18)',  '#f59e0b'],
  Arrived:   ['rgba(34,197,94,0.18)',   '#22c55e'],
  Departed:  ['rgba(120,120,120,0.10)', 'var(--dim)'],
  'N/A':     ['rgba(120,120,120,0.08)', 'var(--dim)'],
};

function _statusPillInline(status) {
  if (!status) return '';
  const [bg, fg] = _STATUS_STYLE[status] || _STATUS_STYLE['N/A'];
  return `<span class="sq-card-status" style="color:${fg};background:${bg}">${esc(status)}</span>`;
}

// Render a rego card (same .sq thumbnail style, enhanced for multi-flight)
function regoCard(group) {
  const nowTs  = Math.floor(Date.now() / 1000);
  const photo  = group.photo_url || '';
  const status = _cardStatus(group, nowTs);
  const { airline: _parsedAirline, acType } = _parseDetail(group.detail || '');
  const isMilitary = (group.notif_types || []).includes('Military');
  const airline = isMilitary
    ? (group.extra_info || '').split(' · ')[0]
    : _parsedAirline;
  const count  = (group.flights || []).length;
  const encoded = esc(JSON.stringify(group));

  const chips = (group.notif_types || []).map(t =>
    `<span class="chip ${chipClass(t)}">${chipLabel(t)}</span>`
  ).join('');

  const airlineLogo = isMilitary
    ? _airforceRoundelImg(airline, 23)
    : _airlineLogoByIcao(group.airline_icao || '', 23, _parsedAirline);
  return `<div class="sq" onclick="openDetail(this)" data-r="${encoded}">
    ${photo ? `<div class="sq-bg" style="background-image:url('${esc(photo)}')"></div>` : ''}
    <div class="sq-top">
      <span class="sq-rego">${esc(group.registration)}</span>
    </div>
    <div class="sq-bottom">
      <div class="sq-row2">
        ${chips}
        ${acType ? `<span class="fc-actype">${esc(acType)}</span>` : ''}
        ${count > 1 ? `<span class="sq-count">${count}×</span>` : ''}
      </div>
      ${airline ? `<div class="sq-airline">${esc(airline)}</div>` : ''}
    </div>
    ${airlineLogo ? `<div class="sq-tail-logo" style="position:absolute;bottom:4px;right:8px;z-index:3">${airlineLogo}</div>` : ''}
  </div>`;
}

async function loadFeed() {
  const el = $('history-list');
  try {
    const data = await api('/feed?days=30');
    if (!data.days || !data.days.length) {
      el.innerHTML = '<div class="empty">No activity yet.</div>';
      return;
    }
    _feedAirportIata = data.airport_iata || '';
    _feedAirportName = data.airport_name || '';
    _feedTimezone    = data.timezone     || '';
    el.innerHTML = data.days.map(day => `
      <div class="section-heading">${esc(day.label)}</div>
      <div class="fc-grid">${(day.cards || []).map(g => regoCard(g)).join('')}</div>
    `).join('');
  } catch (e) {
    el.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
}

function _dayKey(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}

function _expandRow(r) {
  // dep_ts from flight_departure_pattern is a stale historical timestamp — not reliable for
  // day-key comparison without projecting it onto today's date via turnaround_secs.
  // Always return a single arrival card for now.
  return [{ ...r, _eventTs: r.arrival_ts, _cardType: 'arrival' }];
}

function _detailInner(r, closeCmd, showPhoto = true) {
  const fr24 = `https://www.flightradar24.com/data/aircraft/${(r.registration || '').toLowerCase()}`;
  const lazyId = r.flights
    ? `detail-lazy-rego_${r.registration}`
    : `detail-lazy-${r.id || (r.registration + '_' + (r.arrival_ts || 0))}`;
  const spotId = `${lazyId}-spotted`;

  function card(label, value) {
    if (!value) return '';
    return `<div class="dc"><span class="lbl">${label}</span><span class="val">${value}</span></div>`;
  }

  // ── New format: rego group with flights[] ──────────────────────────────
  if (r.flights && r.flights.length > 0) {
    const { airline, acType } = _parseDetail(r.detail || '');
    const photo     = r.photo_url || '';
    const fullPhoto = photo.replace('/640/', '/full/').replace('/400/', '/full/');
    const chips = (r.notif_types || []).map(t =>
      `<span class="chip ${chipClass(t)}">${chipLabel(t)}</span>`
    ).join('');
    const nowTs = Math.floor(Date.now() / 1000);
    const airportIata = _feedAirportIata || '';
    const airportName = _feedAirportName || '';
    const lastSeen = _fmtLastSeen(r.airport_last_seen_ts);
    const cardSt = _cardStatus(r, nowTs);
    const [cStBg, cStFg] = _STATUS_STYLE[cardSt] || _STATUS_STYLE['N/A'];
    const statusPillId = `${lazyId}-status`;
    const _statusPillHtml = st => {
      if (!st || st === 'N/A') return '';
      const [bg, fg] = _STATUS_STYLE[st] || _STATUS_STYLE['N/A'];
      return `<span style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:20px;background:${bg};color:${fg}">${esc(st)}</span>`;
    };
    const cardStatusPill = `<span id="${statusPillId}">${_statusPillHtml(cardSt)}</span>`;

    // Render each flight as a route bar (same design as single-card detail)
    const flightBars = r.flights.map(f => {
      const depLabel = !f.dep_ts ? null : f.dep_ts > nowTs ? (f.dep_label || 'Scheduled') : 'Departed';

      // Use the computed canonical status so the route bar label matches the status pill
      const computedStatus = _barStatus(f, nowTs);
      const routeLiveStatus = computedStatus === 'Arriving'  ? 'In Flight'
                            : computedStatus === 'Arrived'   ? 'On Ground'
                            : computedStatus === 'Scheduled' ? 'Scheduled'
                            : computedStatus === 'Departed'  ? 'Departed'
                            : null;

      const fData = {
        airport_iata:        airportIata,
        airport_name:        airportName,
        next_dep_flight:     f.dep_flight || null,
        next_dep_dest_iata:  f.dep_dest_iata || null,
        next_dep_dest_name:  f.dep_dest_name || null,
        next_dep_dest_city:  f.dep_dest_city || null,
        next_dep_ts:         f.dep_ts || null,
        next_dep_label:      depLabel,
        next_dep_confidence: f.dep_confidence || null,
        origin_iata: null,
        origin_name: null,
      };
      const fR = {
        flight_number:      f.flight_number,
        arrival_ts:         f.arrival_ts,
        live_arrival_ts:    f.arrival_ts,
        live_flight_number: f.flight_number,
        live_status:        routeLiveStatus,
        origin_iata:        f.origin_iata,
        origin_name:        f.origin_name,
        origin_city:        f.origin_city,
        airport_iata:       airportIata,
        airport_name:       airportName,
      };
      return _renderRouteBar(fData, fR);
    }).join('');
    const isMilitary = (r.notif_types || []).includes('Military');
    // Newest visit first — r.flights is chronological ascending, the carousel should open on the latest.
    const mapFlights = isMilitary ? [...r.flights].reverse() : [];
    const mapPages = isMilitary ? mapFlights.map((f, i) => {
      const mapId = `${lazyId}-map-${i}`;
      const mapLabel = fmtTs(f.arrival_ts, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
      return `<div class="mil-map-page">
        <div class="mil-map-label">Detected ${esc(mapLabel)}</div>
        <div class="mil-map" id="${mapId}" data-track='${esc(JSON.stringify(f.track || []))}'></div>
      </div>`;
    }).join('') : '';
    const mapDots = isMilitary && r.flights.length > 1
      ? `<div class="mil-map-dots">${r.flights.map((_, i) => `<span class="mil-map-dot${i === 0 ? ' active' : ''}"></span>`).join('')}</div>`
      : '';
    const mapSections = isMilitary
      ? `<div class="mil-map-carousel" id="${lazyId}-map-carousel">${mapPages}</div>${mapDots}`
      : '';

    return `
      ${showPhoto && photo ? `<img class="detail-photo" src="${esc(fullPhoto)}" alt="${esc(r.registration)}" onerror="this.src='${esc(photo)}'">` : ''}
      <div class="detail-header">
        <div style="display:flex;align-items:center;gap:8px">
          <a class="rego" style="font-size:20px;font-weight:700;color:var(--text);text-decoration:none;letter-spacing:.01em" href="${esc(fr24)}" target="_blank">${esc(r.registration)}</a>
          ${_flag(_regoCountryCode(r.registration), { h: 26, vab: -13 })}
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          ${cardStatusPill}
        </div>
      </div>
      <div style="margin-top:9px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
        ${chips}
        ${mfrBadge(r.manufacturer)}
        ${acType ? `<span class="fc-actype">${esc(acType)}</span>` : ''}
      </div>
      ${airline ? `<div style="margin-top:7px;font-size:12px;color:var(--dim)">${esc(airline)}</div>` : ''}
      ${r.extra_info && !isMilitary ? `<div style="margin-top:6px;font-size:12px;color:var(--dim);font-style:italic;line-height:1.4">${esc(r.extra_info)}</div>` : ''}
      ${isMilitary ? mapSections : `<div${r.flights.length > 2 ? ' class="flight-bars-scroll" style="max-height:290px"' : ''}>${flightBars}</div>`}
      <div class="detail-cards">
        ${isMilitary ? '' : `<div class="dc"><span class="lbl">Last Visit</span><span class="val" id="${lazyId}-lastseen" style="color:var(--dim)">—</span></div>`}
        <div class="dc"><span class="lbl">Spotted</span><div id="${spotId}" style="margin-top:4px;color:var(--dim);font-size:12px">Never</div></div>
      </div>`;
  }

  // ── Legacy format: single notification row ─────────────────────────────
  const { type, photo, fullPhoto, ts, airline, acType, extra } = _detailVars(r);
  const effArrTs = (_isSameFlight(r) ? r.live_arrival_ts : null) || r.arrival_ts;
  const statusStr = _flightStatus(r);
  const statusPillStyle = statusStr === 'On Ground'
    ? 'background:rgba(34,197,94,0.12);color:var(--success)'
    : (statusStr === 'In Flight' || statusStr === 'Departed')
    ? 'background:rgba(245,158,11,0.12);color:var(--warn)'
    : 'background:rgba(120,120,120,0.12);color:var(--dim)';
  const lastSeen = _fmtLastSeen(r.airport_last_seen_ts);

  return `
    ${showPhoto && photo ? `<img class="detail-photo" src="${esc(fullPhoto)}" alt="${esc(r.registration)}" onerror="this.src='${esc(photo)}'">` : ''}
    <div class="detail-header">
      <div style="display:flex;align-items:center;gap:8px">
        <a class="rego" style="font-size:20px;font-weight:700;color:var(--text);text-decoration:none;letter-spacing:.01em" href="${esc(fr24)}" target="_blank">${esc(r.registration)}</a>
        ${_flag(_regoCountryCode(r.registration), { h: 26, vab: -13 })}
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        ${statusStr ? `<span style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:20px;${statusPillStyle}">${esc(statusStr)}</span>` : ''}
      </div>
    </div>
    <div style="margin-top:9px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <span class="chip ${chipClass(type)}">${chipLabel(type)}</span>
      ${mfrBadge(r.manufacturer)}
      ${acType ? `<span class="fc-actype">${esc(acType)}</span>` : ''}
    </div>
    ${airline ? `<div style="margin-top:7px;font-size:12px;color:var(--dim)">${esc(airline)}</div>` : ''}
    ${extra ? `<div style="margin-top:6px;font-size:12px;color:var(--dim);font-style:italic;line-height:1.4">${esc(extra)}</div>` : ''}
    <div id="${lazyId}-route"></div>
    <div class="detail-cards" style="margin-top:10px">
      ${card('Last Seen', lastSeen || '<span style="color:var(--dim)">Never</span>')}
      <div class="dc"><span class="lbl">Last Spotted</span><span class="val" id="${spotId}" style="color:var(--dim)">Never</span></div>
    </div>
    <div id="${lazyId}" class="detail-cards" style="margin-top:6px"></div>`;
}

async function openDetail(el) {
  const r = JSON.parse(el.dataset.r);

  if (window.innerWidth < 768) {
    // Mobile: bottom sheet modal
    $('detail-modal').querySelector('.detail-sheet-scroll').innerHTML = _detailInner(r, 'closeDetail()');
    $('detail-modal').classList.remove('hidden');
  } else {
    // Desktop: expand in grid — toggle off if same card clicked again
    if (_gridExpandedCard === el) { collapseGridDetail(); return; }
    collapseGridDetail();

    _gridExpandedCard = el;
    el.classList.add('sq--expanded');

    // Find the last card in the same visual row so the panel sits below the full row
    const grid = el.closest('.fc-grid');
    const elTop = el.getBoundingClientRect().top;
    let anchor = el;
    for (const card of grid.querySelectorAll('.sq')) {
      if (Math.abs(card.getBoundingClientRect().top - elTop) < 5) anchor = card;
    }

    const panel = document.createElement('div');
    panel.className = 'grid-detail';
    panel.innerHTML = `<div class="gd-inner">${_detailInner(r, 'collapseGridDetail()', false)}</div>`;

    const gridRect = grid.getBoundingClientRect();
    const cardLeft = Math.round(el.getBoundingClientRect().left - gridRect.left);
    const clampedLeft = Math.max(0, Math.min(cardLeft, gridRect.width - 510));
    panel.style.setProperty('--card-left', clampedLeft + 'px');

    anchor.after(panel);
    _gridDetailEl = panel;
    // Double rAF ensures the element is painted before the transition starts
    requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.add('open')));
  }

  // Fire and forget: lazy-load Last Spotted (and route bar for legacy cards)
  const lazyUid = r.flights
    ? ('rego_' + r.registration)
    : (r.id || (r.registration + '_' + (r.arrival_ts || 0)));
  _loadAircraftDetail(r.registration, lazyUid, r);

  requestAnimationFrame(_initMilMaps);
}

function _initMilMaps() {
  document.querySelectorAll('.mil-map').forEach(el => {
    if (el.dataset.mapInit) return;
    el.dataset.mapInit = '1';
    const track = JSON.parse(el.dataset.track || '[]');
    if (!track.length) { el.closest('.mil-map-page')?.remove(); return; }
    // These are small visit-preview maps inside a swipeable carousel — dragging/pinch
    // would fight the carousel's own swipe gesture, so panning is disabled; +/- still zooms.
    const map = L.map(el, {
      attributionControl: false,
      dragging: false, touchZoom: false, scrollWheelZoom: false,
      doubleClickZoom: false, boxZoom: false, keyboard: false,
    });
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18 }).addTo(map);
    const pts = track.map(p => [p.lat, p.lon]);
    if (pts.length === 1) {
      L.marker(pts[0]).addTo(map);
      map.setView(pts[0], 11);
    } else {
      const line = L.polyline(pts, { color: '#3b82f6', weight: 3 }).addTo(map);
      map.fitBounds(line.getBounds(), { padding: [16, 16] });
    }
  });

  // Sync the dot indicator to whichever map page is currently snapped into view, and
  // enable click-and-drag paging on desktop (no touch swipe there, native overflow-x
  // drag-to-scroll isn't a thing, and the map's own dragging is disabled above).
  document.querySelectorAll('.mil-map-carousel').forEach(carousel => {
    if (carousel.dataset.dotsInit) return;
    carousel.dataset.dotsInit = '1';
    _initDragScroll(carousel, () => {
      const idx = Math.round(carousel.scrollLeft / carousel.clientWidth);
      carousel.scrollTo({ left: idx * carousel.clientWidth, behavior: 'smooth' });
    });
    const dotsEl = carousel.nextElementSibling;
    if (!dotsEl || !dotsEl.classList.contains('mil-map-dots')) return;
    const dots = dotsEl.querySelectorAll('.mil-map-dot');
    carousel.addEventListener('scroll', () => {
      const idx = Math.round(carousel.scrollLeft / carousel.clientWidth);
      dots.forEach((d, i) => d.classList.toggle('active', i === idx));
    }, { passive: true });
  });
}

function collapseGridDetail() {
  if (_gridDetailEl) {
    const p = _gridDetailEl;
    p.classList.remove('open');
    p.addEventListener('transitionend', () => p.remove(), { once: true });
    _gridDetailEl = null;
  }
  if (_gridExpandedCard) { _gridExpandedCard.classList.remove('sq--expanded'); _gridExpandedCard = null; }
}

function closeDetail() {
  $('detail-modal').classList.add('hidden');
  _openRecCard = null;
}

let _openRecCard  = null;
let _openRecPanel = null;
let _openRecScrollEl = null;

function openRecDetail(el) {
  if (window.innerWidth < 768) {
    // Mobile: reuse the feed's bottom-sheet modal
    if (_openRecCard === el) { closeDetail(); _openRecCard = null; return; }
    _openRecCard = el;
    const sheet = $('detail-modal').querySelector('.detail-sheet-scroll');
    const _rf = JSON.parse(el.dataset.f);
    const liveryRow = _rf.extra_info
      ? `<div class="rfc-panel-body">
           <div class="rfc-remarks-label">LIVERY</div>
           <div style="font-size:12px;color:var(--text);margin-top:4px">${esc(_rf.extra_info)}</div>
         </div>` : '';
    sheet.innerHTML = `
      <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:10px">${esc(_rf.registration || '')}</div>
      ${_buildRecDetail(el)}${liveryRow}`;
    $('detail-modal').classList.remove('hidden');
    return;
  }

  if (_openRecCard === el) { _closeRecPanel(); return; }
  _closeRecPanel();

  _openRecCard = el;
  el.classList.add('rfc-open');

  const rect = el.getBoundingClientRect();
  const panel = document.createElement('div');
  panel.className = 'rfc-panel';
  panel.style.left  = rect.left  + 'px';
  panel.style.width = rect.width + 'px';
  panel.innerHTML = _buildRecDetail(el);
  document.body.appendChild(panel);
  _openRecPanel = panel;

  const panelH = Math.min(320, panel.scrollHeight || 320);
  if (rect.bottom + panelH > window.innerHeight - 8) {
    panel.style.top    = '';
    panel.style.bottom = (window.innerHeight - rect.top) + 'px';
    panel.classList.add('rfc-panel-above');
    el.classList.add('rfc-above');
  } else {
    panel.style.top = rect.bottom + 'px';
  }

  let scrollEl = el.parentElement;
  while (scrollEl && scrollEl !== document.body) {
    const ov = getComputedStyle(scrollEl).overflowY;
    if (ov === 'auto' || ov === 'scroll') break;
    scrollEl = scrollEl.parentElement;
  }
  if (scrollEl && scrollEl !== document.body) {
    _openRecScrollEl = scrollEl;
    scrollEl.addEventListener('scroll', _closeRecPanel, { once: true });
  }

  requestAnimationFrame(() => requestAnimationFrame(() => panel.classList.add('rfc-panel-open')));
}

function _closeRecPanel() {
  if (_openRecPanel) { _openRecPanel.remove(); _openRecPanel = null; }
  if (_openRecCard)  { _openRecCard.classList.remove('rfc-open', 'rfc-above'); _openRecCard = null; }
  if (_openRecScrollEl) { _openRecScrollEl.removeEventListener('scroll', _closeRecPanel); _openRecScrollEl = null; }
}

document.addEventListener('click', e => {
  if (_openRecCard && !_openRecCard.contains(e.target) && !(_openRecPanel && _openRecPanel.contains(e.target))) {
    _closeRecPanel();
  }
});

function _buildRecDetail(el) {
  const f     = JSON.parse(el.dataset.f);
  const isArr = (f.side || el.dataset.side) === 'arrival' || el.dataset.side === 'arr';
  const { airline } = _parseDetail(f.detail || '');
  const flightNum   = isArr ? (f.flight_number || '—') : (f.dep_flight || '—');
  // New flat-event format: f.light, f.qualifying, f.ts
  // Legacy fallback for old cached data
  const light       = f.light ?? (isArr ? f.arr_light : f.dep_light);
  const qualifying  = f.qualifying ?? (isArr ? (f.arr_qualifying ?? true) : (f.dep_qualifying ?? true));

  const ts  = f.ts ?? (isArr ? f.arrival_ts : f.dep_ts);
  const sr  = parseInt(el.dataset.sr || '0', 10);
  const ss  = parseInt(el.dataset.ss || '0', 10);

  const reasons = [];
  if (light === 'bad_light') {
    reasons.push({ text: 'Harsh Light', dq: false });
  } else if (light === 'low_light' && ts && sr && ss) {
    const minsAfterSr = Math.round((ts - sr) / 60);
    const minsBeforeSs = Math.round((ss - ts) / 60);
    const label = minsAfterSr >= 0 && minsAfterSr < minsBeforeSs
      ? `Low Light (${minsAfterSr} min)` : `Low Light (${minsBeforeSs} min)`;
    reasons.push({ text: label, dq: false });
  }
  if (!qualifying && !light && ts && sr && ss) {
    if (ts < sr) reasons.push({ text: 'Before Sunrise', dq: true });
    else if (ts > ss) reasons.push({ text: 'After Sunset', dq: true });
  }
  if (f.reason && f.reason.startsWith('spotted_')) {
    const n = f.reason.split('_')[1];
    reasons.push({ text: `Spotted ${n}×`, dq: true });
  }
  const sortedReasons = [...reasons.filter(r => r.dq), ...reasons.filter(r => !r.dq)];
  const reasonsHtml = sortedReasons.length ? `
    <div class="rfc-panel-body">
      <div class="rfc-remarks-label">REMARKS</div>
      <div class="rfc-remarks-pills">${sortedReasons.map(r => `<span class="rfc-remark-pill${r.dq ? ' rfc-remark-dq' : ''}">${esc(r.text)}</span>`).join('')}</div>
    </div>` : '';

  const photoHtml = f.photo_url ? `
    <div class="rfc-panel-photo-wrap">
      <img class="rfc-panel-photo" src="${esc(f.photo_url)}" loading="lazy" alt="">
      <div class="rfc-panel-photo-overlay">
        ${airline ? `<div class="rfc-panel-airline">${esc(airline)}</div>` : ''}
        <div class="rfc-panel-flight">${esc(flightNum)}</div>
      </div>
    </div>` : '';

  const bodyHtml = reasonsHtml;

  return `${photoHtml}${bodyHtml}`;
}

async function _loadAircraftDetail(registration, uid, r) {
  const placeholderId = `detail-lazy-${uid}`;
  try {
    const data = await api(`/aircraft/${registration}`);
    // For rego-group cards (new feed format), update Prev Visit + Spotted pills + live status fallback
    if (r.flights) {
      const lsEl = document.getElementById(placeholderId + '-lastseen');
      if (lsEl) {
        if (data.prev_seen_ts) {
          lsEl.textContent = _fmtLastSeen(data.prev_seen_ts) || '—';
          lsEl.style.color = '';
        } else {
          lsEl.textContent = 'First visit';
        }
      }
      const spotEl = document.getElementById(placeholderId + '-spotted');
      if (spotEl) {
        const sessions = data.sessions || [];
        if (sessions.length > 0) {
          const isLivery = (r.notif_types || []).includes('Special Livery');
          const curLivery = (r.extra_info || '').trim().toLowerCase();
const pills = sessions.map(s => {
            const d   = new Date(s.ts * 1000);
            const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][d.getMonth()];
            const yr  = String(d.getFullYear()).slice(2);
            const day = String(d.getDate()).padStart(2,'0');
            const apt = s.airport || '';
            const cc  = _airportCountry(apt);
            const flag = _flag(cc, { h: 11, vab: -1 });
            const codePart = flag
              ? `<span style="display:inline-flex;align-items:center;gap:3px">${flag}${esc(apt)}</span>`
              : esc(apt);
            const sesNotes = (s.notes || '').trim().toLowerCase();
            const hl = isLivery && curLivery && sesNotes && sesNotes === curLivery;
            return `<span class="col-ex-pill${hl ? ' col-ex-pill-hl' : ''}">` +
              `<span class="col-ex-pill-code">${codePart}</span>` +
              `<span class="col-ex-pill-sep"></span>` +
              `<span class="col-ex-pill-count" style="color:var(--text)">${day} ${mon} '${yr}</span>` +
              `<span class="col-ex-pill-sep"></span>` +
              `<span class="col-ex-pill-count">${s.count}</span>` +
              `</span>`;
          }).join('');
          spotEl.innerHTML = `<div class="col-ex-pills" style="padding:0">${pills}</div>`;
        } else if (data.last_spotted_ts) {
          const d = new Date(data.last_spotted_ts * 1000);
          const lbl = d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
          const apt = data.last_spotted_airport ? ` at ${esc(data.last_spotted_airport)}` : '';
          const cnt = data.spotted_count > 1 ? ` (${data.spotted_count}×)` : '';
          spotEl.innerHTML = esc(lbl + apt + cnt);
        }
      }
      // Live FR24 status fallback — for recent flights without a confirmed current_status
      const nowTs = Math.floor(Date.now() / 1000);
      const mostRecentArr = Math.max(...(r.flights || []).map(f => f.arrival_ts || 0));
      const isRecent = mostRecentArr && (nowTs - mostRecentArr) < 86400;
      const hasConfirmedStatus = (r.flights || []).some(f => f.current_status);
      // Also trigger if any flight shows stale "Arriving" (arrival_ts already passed)
      const hasStaleArriving = (r.flights || []).some(f =>
        f.current_status && _normStatus(f.current_status) === 'Arriving' && f.arrival_ts && f.arrival_ts < nowTs
      );
      if (isRecent && (!hasConfirmedStatus || hasStaleArriving)) {
        try {
          const live = await api(`/live-status/${encodeURIComponent(registration)}`);
          if (live.status) {
            const norm = live.status === 'Departed' ? 'Departed' : _normStatus(live.status);
            if (norm && norm !== 'N/A') {
              const statusEl = document.getElementById(placeholderId + '-status');
              if (statusEl) {
                const [bg, fg] = _STATUS_STYLE[norm] || _STATUS_STYLE['N/A'];
                statusEl.innerHTML = `<span style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:20px;background:${bg};color:${fg}">${esc(norm)}</span>`;
              }
            }
          }
        } catch (_) {}
      }
      return;
    }
    // Legacy single-row cards
    const el = document.getElementById(placeholderId);
    if (el) el.innerHTML = _renderLazyRows(data, r);
    const routeEl = document.getElementById(placeholderId + '-route');
    if (routeEl) routeEl.innerHTML = _renderRouteBar(data, r);
    if (data.last_spotted_ts) {
      const spotEl = document.getElementById(placeholderId + '-spotted');
      if (spotEl) {
        const d = new Date(data.last_spotted_ts * 1000);
        const lbl = d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
        const apt = data.last_spotted_airport ? ` at ${esc(data.last_spotted_airport)}` : '';
        const cnt = data.spotted_count > 1 ? ` (${data.spotted_count}×)` : '';
        spotEl.innerHTML = esc(lbl + apt + cnt);
      }
    }
  } catch (_) { /* silently ignore if catalog/db unavailable */ }
}

function _renderLazyRows(data, r) {
  let html = '';
  return html;
}

function _cityName(airportName) {
  if (!airportName) return '';
  return airportName
    .replace(/\s+(international airport|international|intl\.?|airport|aeropuerto|aéroport|airfield|regional|domestic|executive|municipal)\s*$/i, '')
    .trim();
}

function _renderRouteBar(data, r) {
  const originIata = r.origin_iata  || data.origin_iata  || '';
  const originName = r.origin_name  || data.origin_name  || '';
  const originCity = r.origin_city  || data.origin_city  || '';
  const centerIata = data.airport_iata || r.airport_iata || '';
  const centerName = data.airport_name || r.airport_name || '';
  const nextDest   = data.next_dep_dest_iata || '';
  const nextName   = data.next_dep_dest_name || '';
  const nextCity   = data.next_dep_dest_city || '';
  const isMobile   = window.innerWidth < 768;
  const originDisp = isMobile ? (originCity || _cityName(originName)) : _cityName(originName);
  const nextDisp   = isMobile ? (nextCity   || _cityName(nextName))   : _cityName(nextName);
  const nextFlight = data.next_dep_flight || '';
  const nextConf   = data.next_dep_confidence || 0;
  const nextLabel  = data.next_dep_label || '';
  const effArrTs   = (_isSameFlight(r) ? r.live_arrival_ts : null) || r.arrival_ts;
  const arrTime    = effArrTs ? fmtTs(effArrTs, { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const depTime    = data.next_dep_ts ? fmtTs(data.next_dep_ts, { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '';

  // Arrival label: prefer stored arr_label (tracks which FR24 timestamp was used),
  // fall back to deriving from live status.
  const liveStatus = r.live_status || '';
  const arrLabel = data.arr_label ||
    ((liveStatus === 'On Ground' || liveStatus === 'Departed') ? 'Arrived'
    : liveStatus === 'In Flight' ? 'Estimated'
    : liveStatus === 'Scheduled' ? 'Scheduled'
    : 'Arrived');

  // Status pill — for Predicted, fill represents confidence; others use flat color
  const _pill = (label, conf) => {
    const COLORS = {
      'Arrived':   ['rgba(34,197,94,0.18)',   '#22c55e'],
      'Estimated': ['rgba(59,130,246,0.18)',  '#93c5fd'],
      'Scheduled': ['rgba(120,120,120,0.15)', '#999'],
      'Departed':  ['rgba(245,158,11,0.18)',  '#f59e0b'],
    };
    const base = 'font-size:9px;font-weight:700;padding:2px 0;border-radius:20px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;flex-shrink:0;min-width:76px;text-align:center;box-sizing:border-box';
    if (label === 'Predicted') {
      const pct = conf || 0;
      const bg = `linear-gradient(to right,rgba(245,158,11,0.85) ${pct}%,rgba(245,158,11,0.12) ${pct}%)`;
      return `<span style="${base};background:${bg};color:#92400e">${esc(label)}</span>`;
    }
    const [bg, color] = COLORS[label] || COLORS['Scheduled'];
    return `<span style="${base};background:${bg};color:${color}">${esc(label)}</span>`;
  };

  const _timeLine = (label, time, conf) => !time ? '' :
    `<span style="display:flex;align-items:center;justify-content:center;gap:6px;margin-top:4px">
      ${_pill(label, conf)}
      <span class="rb-sub" style="margin:0;min-width:72px;text-align:left">${esc(time)}</span>
    </span>`;

  const arrTimeHtml = _timeLine(arrLabel, arrTime, null);
  const depTimeHtml = _timeLine(nextLabel, depTime, nextLabel === 'Predicted' ? nextConf : null);

  if (!originIata && !centerIata) return '';

  const fr24Airport = iata => `https://www.flightradar24.com/airport/${iata.toLowerCase()}`;
  const fr24Flight  = fn   => `https://www.flightradar24.com/data/flights/${fn.toLowerCase().replace(/\s/g,'')}`;

  const rightNode = nextDest ? `
    <div class="rb-arrow">✈</div>
    <div class="rb-node">
      <span class="rb-lbl">Next Dep.</span>
      <a class="rb-iata rb-link" href="${fr24Airport(nextDest)}" target="_blank">${esc(nextDest)}</a>
      ${nextDisp   ? `<span class="rb-sub">${esc(nextDisp)}</span>` : ''}
      ${nextFlight ? `<a class="rb-sub rb-link" href="${fr24Flight(nextFlight)}" target="_blank">${esc(nextFlight)}</a>` : ''}
    </div>` : '';

  return `<div class="rb">
    <div class="rb-node">
      <span class="rb-lbl">Arr. From</span>
      <a class="rb-iata rb-link" href="${fr24Airport(originIata)}" target="_blank">${esc(originIata)}</a>
      ${originDisp      ? `<span class="rb-sub">${esc(originDisp)}</span>` : ''}
      ${r.flight_number ? `<a class="rb-sub rb-link" href="${fr24Flight(r.flight_number)}" target="_blank">${esc(r.flight_number)}</a>` : ''}
    </div>
    <div class="rb-arrow">✈</div>
    <div class="rb-node rb-here">
      <span class="rb-lbl">At</span>
      <span class="rb-iata">${esc(centerIata)}</span>
      ${arrTimeHtml}
      ${depTimeHtml}
    </div>
    ${rightNode}
  </div>`;
}

function esc(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Tab navigation ────────────────────────────────────────────────────────────

let _feedAirportIata = '';
let _feedAirportName = '';
let _feedTimezone   = '';

const TABS = ['recommendation', 'history', 'collection', 'search', 'settings'];
let activeTab = 'history';

function switchTab(name) {
  if (!TABS.includes(name)) return;
  activeTab = name;
  TABS.forEach(t => {
    $('tab-' + t).classList.toggle('hidden', t !== name);
  });
  document.querySelectorAll('.nav-tab').forEach(b => {
    b.classList.toggle('active', b.dataset.tab === name);
  });
  // Swap header button between Manual Check, Refresh Collection, and Restart Server
  const btn = $('btn-refresh'), lbl = $('btn-refresh-label');
  if (btn && lbl) {
    if (typeof _resetRestartArm === 'function') _resetRestartArm();
    btn.classList.remove('btn-danger', 'btn-danger-armed');
    if (name === 'collection') {
      btn.onclick = () => loadCollection(true);
      lbl.textContent = 'Refresh Collection';
    } else if (name === 'recommendation') {
      btn.onclick = () => { _recLoaded = false; toast('Plotting the windows…'); loadRecommendation(true); };
      lbl.textContent = 'Refresh Spotting';
    } else if (name === 'settings') {
      btn.onclick = () => armRestartBackend();
      btn.classList.add('btn-danger');
      lbl.textContent = 'Restart Server';
    } else {
      btn.onclick = () => forceCheck();
      lbl.textContent = 'Refresh Feed';
    }
  }
  loadTab(name);
  if ((name === 'recommendation' || name === 'collection') && typeof _syncRecScrollHeight === 'function') {
    requestAnimationFrame(_syncRecScrollHeight);
  }
}

$('nav-tabs').addEventListener('click', e => {
  const btn = e.target.closest('.nav-tab');
  if (btn) switchTab(btn.dataset.tab);
});

// ── History ───────────────────────────────────────────────────────────────────

async function loadHistory() {
  const el = $('history-list');
  try {
    const rows = await api('/history?days=7');
    if (!rows.length) { el.innerHTML = '<div class="empty">No notifications yet.</div>'; return; }

    // Expand each log row into 1 or 2 virtual entries (arrival / departure cards)
    const expanded = rows.flatMap(_expandRow);

    // Deduplicate: same registration + flight_number + day + cardType → keep highest notified_ts
    // Handles re-notifications (e.g. 12h special livery cooldown) for the same flight
    const _dedup = new Map();
    for (const r of expanded) {
      const key = `${r.registration}|${r.flight_number || ''}|${_dayKey(r._eventTs)}|${r._cardType}`;
      const prev = _dedup.get(key);
      if (!prev || (r.notified_ts || 0) > (prev.notified_ts || 0)) _dedup.set(key, r);
    }
    const entries = [..._dedup.values()];

    // Group by local event date (arrival_ts or dep_ts, NOT notified_ts)
    const groups = {};
    const order  = [];
    entries.forEach(r => {
      const key = _dayKey(r._eventTs);
      if (!groups[key]) { groups[key] = []; order.push(key); }
      groups[key].push(r);
    });

    // Sort entries within each group by event time descending; sort groups newest-first
    for (const key of order) groups[key].sort((a, b) => (b._eventTs || 0) - (a._eventTs || 0));
    order.sort((a, b) => b.localeCompare(a));

    el.innerHTML = order.map(key => `
      <div class="section-heading">${esc(fmtDate(groups[key][0]._eventTs))}</div>
      <div class="fc-grid">${groups[key].map(sqCard).join('')}</div>`).join('');
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

  $('fl-exclusion').innerHTML = (f.filter_exclusions || f.exclusion_list || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.registration)}</div>
        ${r.description ? `<div class="filter-secondary">${esc(r.description)}</div>` : ''}
      </div>
      <button class="del-btn" title="Remove" onclick="delExclusion('${esc(r.registration)}')">✕</button>
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-rego').innerHTML = (f.filter_regos || f.rego_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.registration)}</div>
        ${r.description ? `<div class="filter-secondary">${esc(r.description)}</div>` : ''}
      </div>
      <button class="del-btn" onclick="delRego('${esc(r.registration)}')">✕</button>
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-type').innerHTML = (f.filter_types || f.type_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.aircraft_type)}</div>
        <div class="filter-secondary">${esc(r.airline)}</div>
      </div>
      <button class="del-btn" onclick="delType('${esc(r.airline)}','${esc(r.aircraft_type)}')">✕</button>
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-airline').innerHTML = (f.filter_airlines || f.airline_watchlist || []).map(r => `
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
  const type = $('al-type').value || 'airline';
  const name = $('al-name').value.trim();
  if (!icao) { toast('Enter ICAO code'); return; }
  try {
    await api('/filters/airline', { method: 'POST', body: JSON.stringify({ icao_code: icao, entry_type: type, name }) });
    $('al-icao').value = ''; $('al-type').value = 'airline'; $('al-name').value = '';
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
  // Monitoring — Polling
  { group: 'mon-polling',   key: 'CHECK_INTERVAL_MINUTES',      label: 'Check Frequency',          desc: 'How often to poll FR24 for new arrivals. Lower = more responsive, higher = more API load.',                      type: 'number', min: 1,  max: 120,  unit: 'minutes', restart: true },
  { group: 'mon-polling',   key: 'FETCH_PAGES',                 label: 'Pages to Fetch',           desc: 'Each page covers around 100 recent arrivals. Increase if busy airports miss flights at the end of the list.',  type: 'number', min: 1,  max: 10,   unit: 'pages', restart: true },
  // Monitoring — Departure
  { group: 'mon-departure', key: 'DEPARTURE_PATTERN_THRESHOLD', label: 'Departure Confidence',     desc: 'Minimum historical confidence required before showing a predicted departure time. 80% means the pattern must hold 4 out of 5 times.', type: 'number', min: 0, max: 100, step: 5, unit: '%', restart: true },
  // Special Livery
  { group: 'livery', key: 'SPECIAL_LIVERY_KEYWORDS',           label: 'Keywords',             desc: 'A flight matches if its airline name contains any of these words (case-insensitive). e.g. "retro", "special".',  type: 'tags', restart: true },
  { group: 'livery', key: 'SPECIAL_LIVERY_EXCLUDE_KEYWORDS',   label: 'Exclude Keywords',     desc: 'If the airline name contains any of these words the match is suppressed — use to block standard liveries.',      type: 'tags', restart: true },
  // Rare Plane
  { group: 'rare', key: 'RARE_PLANE_MIN_ABSENCE_DAYS',         label: 'Minimum Days Absent',  desc: 'An aircraft type is only considered "rare" if it hasn\'t been seen at this airport for at least this many days.', type: 'number', min: 1, max: 365, unit: 'days', restart: true },
  // Military — Scanning
  { group: 'mil-scan',  key: 'MILITARY_CHECK_INTERVAL_MINUTES', label: 'Check Frequency',        desc: 'How often to query adsb.fi for military traffic near the airport.',                                              type: 'number', min: 1,  max: 60,   unit: 'minutes', restart: true },
  { group: 'mil-scan',  key: 'MILITARY_RADIUS_NM',              label: 'Detection Radius',       desc: 'Only consider military aircraft within this radius of the airport. Smaller = fewer false positives.',             type: 'number', min: 10, max: 500,  unit: 'nm', restart: true },
  { group: 'mil-scan',  key: 'MILITARY_MAX_ALT_FT',             label: 'Maximum Altitude',       desc: 'Ignore high-altitude transits — only alert on low-level traffic that\'s likely photo-worthy.',                   type: 'number', min: 0,  max: 50000, step: 500, unit: 'feet', restart: true },
  { group: 'mil-scan',  key: 'MILITARY_RENOTIFY_HOURS',          label: 'Repeat Alert Cooldown', desc: 'Once a military registration has been alerted, suppress further alerts for this many hours.',                    type: 'number', min: 0,  max: 168,  unit: 'hours', restart: true },
  // Spotting settings
  { group: 'spotrec', key: 'SPOT_MAX_GAP_HOURS',     label: 'Spotting Window Gap',     desc: 'A gap longer than this between flights starts a new spotting window instead of joining the current one.', type: 'number', min: 1,  max: 12,  unit: 'hours' },
  { group: 'spotrec', key: 'SPOT_LULL_MINS',          label: 'Quiet Period Length',     desc: 'A quiet stretch within a spotting window longer than this is called out so you know when to take a break.', type: 'number', min: 15, max: 240, unit: 'minutes' },
  { group: 'spotrec', key: 'SPOT_MAX_LULLS',          label: 'Quiet Periods to Show',   desc: 'Maximum number of quiet periods listed per spotting window, to keep recommendations easy to read.',          type: 'number', min: 0,  max: 10 },
  { group: 'spotrec', key: 'SPOT_LIGHTING_GATE',      label: 'Avoid Poor Lighting',     desc: 'When on, spotting windows that overlap sunrise, sunset, or the midday glare window are skipped.',          type: 'toggle' },
  { group: 'spotrec', key: 'SPOT_MAX_SPOTTED',        label: 'Already-Photographed Limit', desc: 'Stop recommending an aircraft once you have photographed it this many times at this airport. 0 = always include.', type: 'number', min: 0,  max: 50,  unit: 'times' },
  { group: 'spotrec', key: 'SPOT_LIGHT_BUFFER_MINS',  label: 'Sunrise/Sunset Buffer',   desc: 'Minutes before and after sunrise/sunset that are treated as poor light — aircraft are front-lit but at a harsh angle.', type: 'number', min: 0,  max: 120, unit: 'minutes' },
  { group: 'spotrec', key: 'SPOT_BAD_LIGHT_START',    label: 'Midday Glare Window Start', desc: 'Start of the harsh midday light window. Aircraft look flat and washed out between these times.',        type: 'time' },
  { group: 'spotrec', key: 'SPOT_BAD_LIGHT_END',      label: 'Midday Glare Window End',   desc: 'End of the harsh midday light window. Leave blank to turn off the midday glare check entirely.',          type: 'time' },
  // Route Type filter
  { group: 'routetype', key: 'ROUTE_TYPE_MIN_DAYS',            label: 'Minimum History Required', desc: 'Require at least this many days of recorded operations before declaring an "established" type. Prevents false positives on new routes.', type: 'number', min: 1, max: 90,  unit: 'days', restart: true },
  { group: 'routetype', key: 'ROUTE_TYPE_DOMINANCE_X',         label: 'Dominance Ratio',          desc: 'The most common aircraft type must appear at least this many times more often than the second-most common to be considered established.', type: 'number', min: 1, max: 10, unit: '×', restart: true },
  { group: 'routetype', key: 'ROUTE_TYPE_LOOKBACK_DAYS',       label: 'Lookback Period',          desc: 'How far back to look when calculating which aircraft type dominates a route. Longer = more stable, shorter = more reactive.', type: 'number', min: 7, max: 365, unit: 'days', restart: true },
];

// ── Settings helpers ──────────────────────────────────────────────────────────

const _RESTART_REQUIRED_KEYS = new Set(SETTINGS_SCHEMA.filter(x => x.restart).map(x => x.key));

async function _saveSetting(key, value) {
  try {
    await api('/settings', { method: 'PUT', body: JSON.stringify({ [key]: value }) });
    const needsRestart = _RESTART_REQUIRED_KEYS.has(key);
    toast(needsRestart ? 'Saved — restart the server for this to take effect' : 'Saved',
          needsRestart ? 5000 : 2000, needsRestart);
  } catch (e) { toast('Error: ' + e.message); }
}

const _DAYS_ORDER = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const _DAYS_LABELS = ['M','T','W','T','F','S','S'];

function _settingControl(item, value) {
  const k = item.key;
  const v = value ?? '';
  switch (item.type) {
    case 'number': {
      const inp = `<input type="number" class="setting-input" data-key="${k}" value="${esc(String(v))}"
        ${item.min != null ? `min="${item.min}"` : ''}
        ${item.max != null ? `max="${item.max}"` : ''}
        ${item.step     ? `step="${item.step}"` : ''}>`;
      return item.unit
        ? `<div class="num-with-unit">${inp}<span class="num-unit">${esc(item.unit)}</span></div>`
        : inp;
    }
    case 'time':
      return `<input type="time" class="setting-input" data-key="${k}" value="${esc(String(v))}">`;
    case 'window': {
      const cur = String(v).toLowerCase();
      return `<div class="seg-ctrl" data-key="${k}">
        ${[['','Always'],['Daylight','Daylight'],['Off','Off']].map(([val,lbl]) =>
          `<button class="seg-btn${cur === val.toLowerCase() ? ' active' : ''}" data-val="${val}">${lbl}</button>`
        ).join('')}</div>`;
    }
    case 'days': {
      const active = new Set(String(v).split(',').map(d => d.trim()).filter(Boolean));
      return `<div class="day-toggles" data-key="${k}">
        ${_DAYS_ORDER.map((day, i) =>
          `<button class="day-btn${active.has(day) ? ' active' : ''}" data-day="${day}">${_DAYS_LABELS[i]}</button>`
        ).join('')}</div>`;
    }
    case 'toggle': {
      const checked = v === 'true' || v === true;
      return `<label class="tog-switch">
        <input type="checkbox" class="tog-input" data-key="${k}"${checked ? ' checked' : ''}>
        <span class="tog-track"><span class="tog-thumb"></span></span>
      </label>`;
    }
    case 'select':
      return `<select class="setting-select" data-key="${k}">
        ${(item.options || []).map(([val, lbl]) =>
          `<option value="${esc(val)}"${String(v) === val ? ' selected' : ''}>${esc(lbl)}</option>`
        ).join('')}</select>`;
    default:
      return `<input class="setting-input" data-key="${k}" value="${esc(String(v))}" placeholder="—">`;
  }
}

function _settingRow(item, value) {
  const uCls = item.unused ? ' setting-unused' : '';
  const uTag = item.unused ? ` <span class="setting-unused-tag">not active</span>` : '';
  if (item.type === 'tags') {
    const tags = String(value || '').split(',').map(t => t.trim()).filter(Boolean);
    return `<div class="setting-row-full${uCls}" data-key="${item.key}">
      <div class="setting-key">${item.label}${uTag}</div>
      ${item.desc ? `<div class="setting-desc">${esc(item.desc)}</div>` : ''}
      <div class="tags-list">${tags.map(t =>
        `<span class="tag-chip">${esc(t)}<button class="tag-del" data-tag="${esc(t)}">×</button></span>`
      ).join('')}</div>
      <div class="tags-add-row">
        <input class="tags-input" placeholder="Add keyword…">
        <button class="add-btn tags-add-btn">Add</button>
      </div>
    </div>`;
  }
  return `<div class="setting-row${uCls}">
    <div class="setting-label">
      <div class="setting-key">${item.label}${uTag}</div>
      ${item.desc ? `<div class="setting-desc">${esc(item.desc)}</div>` : ''}
    </div>
    <div class="setting-control">${_settingControl(item, value)}</div>
  </div>`;
}

function _wireSettings() {
  // Standard inputs (number, time, text)
  document.querySelectorAll('.setting-input').forEach(inp => {
    inp.addEventListener('change', () => _saveSetting(inp.dataset.key, inp.value));
  });
  // Select
  document.querySelectorAll('.setting-select').forEach(sel => {
    sel.addEventListener('change', () => _saveSetting(sel.dataset.key, sel.value));
  });
  // Toggle
  document.querySelectorAll('.tog-input').forEach(inp => {
    inp.addEventListener('change', () => _saveSetting(inp.dataset.key, inp.checked ? 'true' : 'false'));
  });
  // Segmented window
  document.querySelectorAll('.seg-ctrl .seg-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const ctrl = btn.closest('.seg-ctrl');
      ctrl.querySelectorAll('.seg-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      _saveSetting(ctrl.dataset.key, btn.dataset.val);
    });
  });
  // Day toggles
  document.querySelectorAll('.day-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      btn.classList.toggle('active');
      const wrap = btn.closest('.day-toggles');
      const active = _DAYS_ORDER.filter(d =>
        wrap.querySelector(`.day-btn[data-day="${d}"]`)?.classList.contains('active')
      );
      _saveSetting(wrap.dataset.key, active.join(','));
    });
  });
  // Tags — add button + Enter key
  document.querySelectorAll('.setting-row-full').forEach(row => {
    const key = row.dataset.key;
    const inp = row.querySelector('.tags-input');
    const addFn = () => {
      const val = inp.value.trim();
      if (!val) return;
      _addSettingTag(row, key, val);
      inp.value = '';
    };
    row.querySelector('.tags-add-btn').addEventListener('click', addFn);
    inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addFn(); } });
    // Tags — delete chips
    row.querySelectorAll('.tag-del').forEach(btn => {
      btn.addEventListener('click', () => _removeSettingTag(row, key, btn.dataset.tag));
    });
  });
}

function _addSettingTag(row, key, tag) {
  const list = row.querySelector('.tags-list');
  const current = [...list.querySelectorAll('.tag-del')].map(b => b.dataset.tag);
  if (current.includes(tag)) return;
  _saveSetting(key, [...current, tag].join(','));
  const chip = document.createElement('span');
  chip.className = 'tag-chip';
  chip.innerHTML = `${esc(tag)}<button class="tag-del" data-tag="${esc(tag)}">×</button>`;
  chip.querySelector('.tag-del').addEventListener('click', () => _removeSettingTag(row, key, tag));
  list.appendChild(chip);
}

function _removeSettingTag(row, key, tag) {
  const list = row.querySelector('.tags-list');
  const current = [...list.querySelectorAll('.tag-del')].map(b => b.dataset.tag);
  _saveSetting(key, current.filter(t => t !== tag).join(','));
  const btn = [...list.querySelectorAll('.tag-del')].find(b => b.dataset.tag === tag);
  if (btn) btn.closest('.tag-chip').remove();
}

async function loadSettings() {
  try {
    const s = await api('/settings');
    const groups = [...new Set(SETTINGS_SCHEMA.map(x => x.group))];
    groups.forEach(g => {
      const el = $('settings-' + g);
      if (!el) return;
      el.innerHTML = SETTINGS_SCHEMA.filter(x => x.group === g)
        .map(item => _settingRow(item, s[item.key] ?? '')).join('');
    });
    // Static inputs not in SETTINGS_SCHEMA — populate manually
    const lsEl = $('info-logostream-key');
    if (lsEl && !lsEl.dataset.userEdited) lsEl.value = s.LOGOSTREAM_API_KEY || '';
    _wireSettings();
  } catch (e) {
    toast('Failed to load settings: ' + e.message);
  }
}

// ── Force check ───────────────────────────────────────────────────────────────

let _restartArmed = false;
let _restartArmTimer = null;

function _resetRestartArm() {
  clearTimeout(_restartArmTimer);
  _restartArmed = false;
  const btn = $('btn-refresh'), lbl = $('btn-refresh-label');
  if (btn) btn.classList.remove('btn-danger-armed');
  if (lbl && activeTab === 'settings') lbl.textContent = 'Restart Server';
}

function armRestartBackend() {
  const btn = $('btn-refresh'), lbl = $('btn-refresh-label');
  if (!btn || !lbl) return;
  if (_restartArmed) {
    _resetRestartArm();
    restartBackend();
    return;
  }
  _restartArmed = true;
  btn.classList.add('btn-danger-armed');
  lbl.textContent = 'Confirm Restart?';
  _restartArmTimer = setTimeout(_resetRestartArm, 4000);
}

async function restartBackend() {
  try {
    await api('/restart', { method: 'POST' });
    toast('Backend restarting…', 4000);
  } catch (_) {
    toast('Restart triggered (connection lost is expected)', 4000);
  }
}

async function forceCheck() {
  const btn = $('btn-refresh');
  btn.classList.add('spinning');
  btn.disabled = true;
  try {
    toast('Binoculars out…');
    await api('/force-check', { method: 'POST' });
    setTimeout(() => { loadFeed(); _recLoaded = false; toast('Nothing missed. Probably.'); }, 8000);
  } catch (e) {
    toast('Check failed: ' + e.message);
  } finally {
    btn.classList.remove('spinning');
    btn.disabled = false;
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
  if (name === 'recommendation') loadRecommendation(false);
  if (name === 'history')        loadFeed();
  if (name === 'collection')     loadCollection();
  if (name === 'search')         {
    if (!_srchTabInited) {
      _srchTabInited = true;
      _srchDDCreate('srch-dd-mfr',        'All Manufacturer', [], () => _srchFlFilter());
      _srchDDCreate('srch-dd-airline',    'All Airline',      [], () => _srchFlFilter());
      _srchDDCreate('srch-dd-type',       'All Type',         [], () => _srchFlFilter());
      _srchDDCreate('srch-dd-rt-origin',  'All Origins',      [], () => { _srchRtMirror('origin'); _srchRtRun(true); });
      _srchDDCreate('srch-dd-rt-dest',    'All Destinations', [], () => { _srchRtMirror('dest');   _srchRtRun(true); });
      _srchDDCreate('srch-dd-rt-airline', 'All Airlines',     [], () => _srchRtRun(true));
      _srchDDCreate('srch-dd-cat-mfr',    'All Manufacturers',[], () => _srchRun(true));
      _srchDDCreate('srch-dd-cat-type',   'All Types',        [], () => _srchRun(true));
      _srchDDCreate('srch-dd-cat-airline','All Airlines',     [], () => _srchRun(true));
      _srchDDCreate('srch-dd-cat-airport','All Airports',     [], () => _srchRun(true));
      _srchDDCreate('srch-dd-cat-keyword','All Keywords',     [], () => _srchRun(true));
      _srchFiltersTs = Date.now();
      _srchFlLoadFilters();
      _srchRtLoadFilters();
    } else {
      _srchMaybeRefreshFilters();
    }
    $('srch-fl-status').textContent = 'Enter a registration or select a filter.';
    $('srch-rt-status').textContent = 'Enter a flight number or select a filter.';
    _srchSetBtn(_srchActiveSub);
  }
  if (name === 'settings')       { loadInfo(); loadFilters(); loadSettings(); }
}

// ── Collection tab ────────────────────────────────────────────────────────────
let _colLoaded = false;
let _colInited = false;
const _colSpCache = {}, _colApCache = {}, _colArppCache = {}, _colTyCache = {};
let _colSpPinned = false;

function _colHideAllPopovers() {
  ['col-airline-popover','col-airport-popover','col-type-popover','col-session-popover'].forEach(id => {
    const el = $(id);
    if (el) { el.classList.add('hidden'); el.classList.remove('pinned'); }
  });
  _colSpPinned = false;
}

async function loadCollection(force) {
  if (_colLoaded && !force) return;
  _colLoaded = true;
  if (force) { _colKwLiveryCache = null; _srchCatStale = true; }
  // Pre-load filter tag setting so session expand respects it immediately
  api('/settings').then(s => {
    const sel = new Set((s.collection_session_tags || '').split(',').map(t => t.trim()).filter(Boolean));
    _sessionFilterTags = sel.size ? sel : null;
  }).catch(() => {});
  const btn = $('btn-refresh');
  const lbl = $('btn-refresh-label');
  if (btn) btn.disabled = true;
  if (lbl) lbl.textContent = 'Loading…';
  if (force) toast('Dusting off the catalog…');
  try {
    const d = await api(force ? '/catalog-stats?force=true' : '/catalog-stats');
    if (d.error) { toast('Collection: ' + d.error); return; }
    if (force) {
      toast('All negatives accounted for.');
      api('/fleet-cards/refresh-photos', { method: 'POST' }).catch(() => {});
      if (_fleetCards.length) setTimeout(_fleetInit, 1500);
    }
    _colRenderStats(d);
    if (!_colInited) {
      _colInited = true;
      _colInitSessionPopover();
      _colInitAirlinePopover();
      _colInitAirportPopover();
      _colInitTypePopover();
      _colInitRegoPopover();
    }
    if (window.twemoji) twemoji.parse($('tab-collection'), {folder: 'svg', ext: '.svg'});
  } catch(e) { toast('Collection load failed'); _colLoaded = false; } finally {
    if (btn) btn.disabled = false;
    if (lbl) lbl.textContent = 'Refresh Collection';
  }
}

// ── Collection subtabs ────────────────────────────────────────────────────
let _colActiveSub = 'summary';

function _colSubtab(name) {
  _colActiveSub = name;
  document.querySelectorAll('[data-col-subtab]').forEach(b => {
    b.classList.toggle('active', b.dataset.colSubtab === name);
  });
  document.querySelectorAll('.col-subtab-page').forEach(p => {
    p.classList.toggle('hidden', p.id !== 'col-subtab-' + name);
  });
  if (name === 'fleet') _fleetInit();
  if (typeof _syncRecScrollHeight === 'function') requestAnimationFrame(_syncRecScrollHeight);
}

function _fleetToggleType(key) {
  _fleetExpanded[key] = !_fleetExpanded[key];
  const rows = document.getElementById('flt-g-' + key);
  const arrow = document.getElementById('flt-a-' + key);
  if (rows) rows.style.display = _fleetExpanded[key] ? 'flex' : 'none';
  if (arrow) arrow.textContent = _fleetExpanded[key] ? '▾' : '▸';
}

function _fltShortDate(dateStr) {
  if (!dateStr) return '';
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' });
}

function _fmtFleetDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'});
}

// ── Fleet coverage cards ──────────────────────────────────────────────────
let _fleetCards = [];   // [{airline, iata, icao, aircraft:[]}]
let _fleetAdding = false;
let _fleetExpanded = {};  // 'ICAO-TYPE_CODE' → bool
const _regPfxCC = {};    // prefix → {cc, name}  (persists for session)
let _fleetWatched = new Set();  // registrations already on rego watchlist

function _regoPrefix(rego) {
  if (!rego) return '';
  if (rego.includes('-')) return rego.split('-')[0].toUpperCase();
  // No dash (e.g. US N-numbers: N784UA → 'N')
  const m = rego.match(/^([A-Z]+)/i);
  return m ? m[1].toUpperCase() : rego[0].toUpperCase();
}

async function _fleetPrefetchPrefixes(aircraft) {
  // Collect unknown prefixes and a sample rego for each
  const samples = {};
  for (const a of aircraft) {
    const pfx = _regoPrefix(a.registration);
    if (pfx && !_regPfxCC[pfx] && !samples[pfx]) samples[pfx] = a.registration;
  }
  await Promise.all(Object.entries(samples).map(async ([pfx, rego]) => {
    try {
      const d = await api(`/reg-prefix-cc?prefix=${encodeURIComponent(pfx)}&sample=${encodeURIComponent(rego)}`);
      if (d.cc) _regPfxCC[pfx] = d;
    } catch {}
  }));
}

let _fleetDragInited = false;

async function _fleetInit() {
  _fleetAdding = false;
  _fleetRender();
  const [cards, filters] = await Promise.all([
    api('/fleet-cards').catch(() => []),
    api('/filters').catch(() => ({})),
  ]);
  _fleetCards = cards;
  _fleetWatched = new Set((filters.filter_regos || filters.rego_watchlist || []).map(r => r.registration));
  const allAircraft = _fleetCards.flatMap(c => c.aircraft);
  if (allAircraft.length) await _fleetPrefetchPrefixes(allAircraft);
  _fleetRender();
  const wrap = $('flt-wrap');
  if (!_fleetDragInited && wrap) {
    _initDragScroll(wrap, null);
    _fleetDragInited = true;
  }
  // Centre first card: add side-padding so there's room to scroll, then set scrollLeft=0
  setTimeout(() => {
    const w = $('flt-wrap');
    const first = w && w.querySelector('.flt-card');
    if (w && first) {
      const side = Math.max(12, Math.round((w.clientWidth - first.offsetWidth) / 2));
      w.style.paddingLeft  = side + 'px';
      w.style.paddingRight = side + 'px';
      w.scrollLeft = 0;
    }
  }, 80);
}

function _fleetRender() {
  const wrap = $('flt-wrap');
  if (!wrap) return;
  wrap.style.paddingLeft = wrap.style.paddingRight = '';

  if (_fleetCards.length === 0 && !_fleetAdding) {
    wrap.innerHTML = `<div class="flt-empty">
      <button class="flt-empty-add-btn" onclick="_fleetAddCard()">+</button>
      <div style="color:var(--dim);font-size:12px;margin-top:4px">Track a Fleet</div>
    </div>`;
    return;
  }

  let html = _fleetCards.map((c, i) => _fleetCardHtml(c, i)).join('');

  if (_fleetAdding) {
    html += `<div class="flt-card flt-card--input" id="flt-input-card">
      <div class="flt-input-inner">
        <div class="flt-input-label">Enter IATA or ICAO airline code</div>
        <input class="flt-code-input" id="flt-code-inp" type="text" placeholder="e.g. QF, QFA" maxlength="4" autocomplete="off" spellcheck="false">
        <div class="flt-input-btns">
          <button class="flt-btn-go" onclick="_fleetConfirm()">Search</button>
          <button class="flt-btn-cancel" onclick="_fleetCancelAdd()">Cancel</button>
        </div>
        <div class="flt-input-err" id="flt-inp-err"></div>
      </div>
    </div>`;
  }

  html += `<div class="flt-add-col">
    <button class="flt-add-col-btn" onclick="_fleetAddCard()" title="Add airline">+</button>
    <div class="flt-add-col-label">Add Airline</div>
  </div>`;

  wrap.innerHTML = html;

  if (_fleetAdding) {
    const inp = $('flt-code-inp');
    if (inp) {
      inp.oninput = () => { inp.value = inp.value.toUpperCase(); };
      inp.onkeydown = e => { if (e.key === 'Enter') _fleetConfirm(); };
      inp.focus();
    }
  }
}

function _fleetCardHtml(card, idx) {
  const have = card.aircraft.filter(a => a.photos > 0).length;
  const total = card.aircraft.length;
  const pct = total ? Math.round(have / total * 100) : 0;
  const logoSrc = `/api/airline-logo/${encodeURIComponent(card.icao)}?v=${_LOGO_V}`;

  // Group by type_code, sorted alphabetically by type_code
  const groups = [];
  const seen = {};
  [...card.aircraft].sort((a, b) => (a.type_code || '').localeCompare(b.type_code || '')).forEach(a => {
    if (!seen[a.type_code]) {
      seen[a.type_code] = true;
      groups.push({ type_code: a.type_code, type_full: a.type_full, manufacturer: a.manufacturer, aircraft: [] });
    }
    groups[groups.length - 1].aircraft.push(a);
  });

  const rows = groups.map(g => {
    const key = card.icao + '-' + g.type_code;
    const open = !!_fleetExpanded[key];
    const mfrCls = (g.manufacturer || '').toLowerCase().replace(/\s+/g, '-');
    const badge = g.manufacturer ? `<span class="mfr mfr-${mfrCls}">${esc(g.manufacturer)}</span>` : '';
    const typeName = g.type_full.replace(/^(airbus|boeing|embraer|bombardier|atr|mcdonnell douglas|lockheed)\s+/i, '').trim() || g.type_code;
    const grpHave = g.aircraft.filter(a => a.photos > 0).length;
    const header = `<div class="flt-type-hd" onclick="_fleetToggleType('${key}')">
      <span id="flt-a-${key}" class="flt-type-arrow">${open ? '▾' : '▸'}</span>
      ${badge}
      <span class="flt-type-hd-name">${esc(typeName)}</span>
      <span class="flt-type-count">${grpHave}/${g.aircraft.length}</span>
    </div>`;
    const acRows = [...g.aircraft].sort((a, b) => {
      const aHave = a.photos > 0, bHave = b.photos > 0;
      if (bHave !== aHave) return bHave ? 1 : -1;
      return a.registration.localeCompare(b.registration);
    }).map(a => {
      const pfx = _regoPrefix(a.registration);
      const cc  = (_regPfxCC[pfx] || {}).cc || '';
      const flag = cc ? `<span class="flt-pill-flag">${_flagEmoji(cc, 12)}</span>` : '';
      const isWatched = a.photos === 0 && _fleetWatched.has(a.registration);
      const cls = a.photos > 0 ? 'flt-pill--have' : isWatched ? 'flt-pill--watched' : 'flt-pill--miss';
      const clickAttr = a.photos === 0 && !isWatched ? `onclick="_fleetPillClick(this,'${esc(a.registration)}')"` : '';
      let right = '';
      if (a.photos > 0 && a.last_date) {
        const apFlag = a.last_ap_cc ? `<span class="flt-ap-flag">${_flagEmoji(a.last_ap_cc, 10)}</span>` : '';
        const apCode = a.last_ap_iata || '';
        const date = _fltShortDate(a.last_date);
        const sep = date && apCode ? '&nbsp;&nbsp;·&nbsp;&nbsp;' : '';
        right = `<span class="flt-pill-ct">${date}${sep}${apFlag}${apCode ? ' ' + esc(apCode) : ''}</span>`;
      }
      return `<span class="flt-rego-pill ${cls}" ${clickAttr}>${flag}${esc(a.registration)}${right}</span>`;
    }).join('');
    return header + `<div id="flt-g-${key}" class="flt-pill-wrap" style="display:${open ? 'flex' : 'none'}">${acRows}</div>`;
  }).join('');

  return `<div class="flt-card">
    <div class="flt-card-hd">
      <img class="flt-hd-logo" src="${logoSrc}" onerror="this.style.display='none'" alt="">
      <div class="flt-hd-info">
        <div class="flt-hd-name">${esc(card.airline)} <span style="color:var(--dim);font-weight:400;font-size:12px;margin-left:3px">${esc(card.iata)}/${esc(card.icao)}</span></div>
        <div class="flt-hd-cov">${have} / ${total} · ${pct}% <span style="margin-left:8px;color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.04em">Updated ${_fmtFleetDate(card.updated_at)}</span></div>
      </div>
      <button class="flt-hd-close" onclick="_fleetRemoveCard(${idx},this)" title="Remove">✕</button>
    </div>
    <div class="flt-progress"><div class="flt-progress-fill" style="width:${pct}%"></div></div>
    <div class="flt-ac-list">${rows}</div>
  </div>`;
}

function _fleetAddCard() {
  if (_fleetAdding) { const inp = $('flt-code-inp'); if (inp) inp.focus(); return; }
  _fleetAdding = true;
  _fleetRender();
}

function _fleetCancelAdd() {
  _fleetAdding = false;
  _fleetRender();
}

async function _fleetConfirm() {
  const inp = $('flt-code-inp');
  if (!inp) return;
  const code = inp.value.trim().toUpperCase();
  if (!code) { inp.focus(); return; }

  const card = $('flt-input-card');
  if (card) card.innerHTML = `<div class="flt-loading">Fetching ${esc(code)}…</div>`;

  try {
    const d = await api(`/fleet-coverage?code=${encodeURIComponent(code)}`);
    if (d.error) throw new Error(d.error);
    const dup = _fleetCards.some(c => (d.icao && c.icao === d.icao) || (d.iata && c.iata === d.iata));
    if (dup) throw new Error(`${d.airline || code} is already added.`);
    await api('/fleet-cards', { method: 'POST', body: JSON.stringify({ icao: d.icao, iata: d.iata, airline: d.airline, aircraft: d.aircraft }) });
    _fleetCards.push({ airline: d.airline, iata: d.iata, icao: d.icao, aircraft: d.aircraft });
    await _fleetPrefetchPrefixes(d.aircraft);
    _fleetAdding = false;
    _fleetRender();
  } catch(e) {
    _fleetRender();
    const err = $('flt-inp-err');
    const inp2 = $('flt-code-inp');
    if (err) err.textContent = String(e.message || e);
    if (inp2) { inp2.value = code; inp2.focus(); }
  }
}

async function _fleetPillClick(el, rego) {
  if (el.dataset.confirm) {
    // Second click — add to watchlist
    el.style.cssText = '';
    el.textContent = '✓ Added';
    el.onclick = null;
    try {
      await api('/filters/rego', { method: 'POST', body: JSON.stringify({ registration: rego, airline: '', description: 'Added from Fleet tracker' }) });
      _fleetWatched.add(rego);
    } catch(e) {
      el.textContent = rego;
    }
    setTimeout(() => { delete el.dataset.confirm; }, 3000);
    return;
  }
  // First click — prompt
  el.dataset.confirm = '1';
  el.style.cssText = 'background:rgba(245,158,11,0.2);border-color:var(--warn);color:var(--warn);cursor:pointer;justify-content:center;';
  el.innerHTML = `<span style="font-size:11px;font-weight:600">Add ${rego} to Rego Watchlist?</span>`;
  setTimeout(() => {
    if (el.dataset.confirm) {
      delete el.dataset.confirm;
      el.style.cssText = '';
      // Restore original content by re-rendering
      _fleetRender();
    }
  }, 4000);
}

async function _fleetRemoveCard(idx, btn) {
  if (!btn) return;
  if (!btn.dataset.confirm) {
    btn.dataset.confirm = '1';
    btn.textContent = 'CONFIRM';
    btn.style.cssText = 'background:var(--danger);color:#fff;border:none;border-radius:var(--r);padding:5px 12px;font-size:12px;font-weight:700;cursor:pointer;letter-spacing:.04em';
    setTimeout(() => { if (btn.dataset.confirm) { delete btn.dataset.confirm; btn.textContent = '✕'; btn.style.cssText = ''; } }, 3000);
    return;
  }
  const card = _fleetCards[idx];
  if (!card) return;
  _fleetCards.splice(idx, 1);
  _fleetRender();
  try { await api(`/fleet-cards/${encodeURIComponent(card.icao)}`, { method: 'DELETE' }); } catch {}
}

// ── ICAO airline code → 2-letter country code ─────────────────────────────
const _ICAO_CC = {
  QFA:'au',VOZ:'au',JST:'au',RXA:'au',QQW:'au',QLK:'au',
  ANZ:'nz',LNZ:'nz',
  CPA:'hk',HDA:'hk',HKE:'hk',GBA:'hk',
  SIA:'sg',TGW:'sg',
  JAL:'jp',ANA:'jp',JJP:'jp',SFJ:'jp',
  AAR:'kr',KAL:'kr',JJA:'kr',
  CCA:'cn',CES:'cn',CSN:'cn',CHH:'cn',CSC:'cn',CXA:'cn',
  CAL:'tw',EVA:'tw',SJX:'tw',
  MAS:'my',AXM:'my',BMA:'my',
  THA:'th',BKP:'th',
  GIA:'id',LNI:'id',BTK:'id',CTV:'id',
  PAL:'ph',CEB:'ph',
  HVN:'vn',VJC:'vn',BAV:'vn',
  AIC:'in',IGO:'in',VTI:'in',
  TGT:'lk',RNA:'np',RBA:'bn',
  FJI:'fj',AGO:'pg',TOK:'pg',ACI:'nc',SOL:'sb',
  NRU:'nr',AVN:'vu',
  UAE:'ae',ETD:'ae',FDB:'ae',ABY:'ae',
  QTR:'qa',GFA:'bh',OMA:'om',
  THY:'tr',
  BAW:'gb',VIR:'gb',EZY:'gb',EXS:'gb',
  DLH:'de',CLH:'de',EWG:'de',CFG:'de',
  AFR:'fr',CDG:'fr',TVF:'fr',
  KLM:'nl',KLC:'nl',
  SWR:'ch',EDW:'ch',
  AUA:'at',IBE:'es',VLG:'es',AEA:'es',
  AZA:'it',NAX:'no',FIN:'fi',LOT:'pl',WZZ:'hu',
  RYR:'ie',EIN:'ie',TAP:'pt',ICE:'is',
  UAL:'us',AAL:'us',DAL:'us',ASA:'us',JBU:'us',SWA:'us',HAL:'us',
  FDX:'us',UPS:'us',GTI:'us',
  ACA:'ca',WJA:'ca',TSC:'ca',POE:'ca',
  LAN:'cl',AZU:'br',GLO:'br',AVA:'co',AMX:'mx',CMP:'pa',
  SAA:'za',ETH:'et',MSR:'eg',KQA:'ke',MRU:'mu',
  UAF:'au',
};

function _flagEmoji(cc, h = 16) {
  if (!cc || cc.length !== 2) return '';
  const cp = l => (0x1F1E6 + l.toUpperCase().charCodeAt(0) - 65).toString(16);
  const code = `${cp(cc[0])}-${cp(cc[1])}`;
  return `<img src="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/svg/${code}.svg" style="height:${h}px;width:auto;vertical-align:middle;flex-shrink:0">`;
}

const _LOGO_V = 2;  // bump to bust SW logo cache when server-side logic changes
function _airlineLogoByIcao(icao, size = 28, fallbackName = '') {
  if (!icao && !fallbackName) return '';
  const src = icao
    ? `/api/airline-logo/${encodeURIComponent(icao)}?v=${_LOGO_V}`
    : `/api/airline-logo-name/${encodeURIComponent(fallbackName.replace(/\s*\(.*?\)/g,'').trim())}?v=${_LOGO_V}`;
  return `<img src="${src}" onerror="this.style.display='none'" loading="lazy" alt="" style="height:${size}px;max-width:${size * 2}px;object-fit:contain;flex-shrink:0">`;
}

function _airforceRoundelImg(country, size = 28) {
  if (!country) return '';
  const src = `/api/airforce-roundel/${encodeURIComponent(country)}?v=${_LOGO_V}`;
  return `<img src="${src}" onerror="this.style.display='none'" loading="lazy" alt="" style="height:${size}px;max-width:${size * 2}px;object-fit:contain;flex-shrink:0">`;
}

function _airlineLogoImg(airlineName, size = 28) {
  if (!airlineName) return '';
  const m = airlineName.match(/\(([A-Z]{2,4})\)\s*$/);
  const src = m
    ? `/api/airline-logo/${encodeURIComponent(m[1])}?v=${_LOGO_V}`
    : `/api/airline-logo-name/${encodeURIComponent(airlineName.replace(/\s*\(.*?\)/g,'').trim())}?v=${_LOGO_V}`;
  return `<img src="${src}" onerror="this.style.display='none'" loading="lazy" alt="" style="height:${size}px;max-width:${Math.round(size*2)}px;object-fit:contain;flex-shrink:0">`;
}

function _colAirlineLogo(rawName) {
  const m = rawName && rawName.match(/\(([A-Z]{2,4})\)\s*$/);
  const icao = m ? m[1] : '';
  const cleanName = (rawName || '').replace(/\s*\(.*?\)/g, '').trim();
  const src = icao
    ? `/api/airline-logo/${encodeURIComponent(icao)}?v=${_LOGO_V}`
    : cleanName ? `/api/airline-logo-name/${encodeURIComponent(cleanName)}?v=${_LOGO_V}` : '';
  if (!src) return '<span class="col-logo-slot"></span>';
  return `<span class="col-logo-slot"><img class="col-airline-logo" src="${src}" onerror="this.style.display='none'" loading="lazy" alt=""></span>`;
}

function _colAlignCounts(panelId) {
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const els = panel.querySelectorAll('.col-stats-row-count');
  if (!els.length) return;
  els.forEach(el => el.style.width = '');
  const maxW = Math.max(...Array.from(els).map(el => el.scrollWidth));
  els.forEach(el => el.style.width = (maxW + 4) + 'px');
}

function _colMfrBadge(t) {
  if (!t || !t.manufacturer) return '';
  const cls = t.manufacturer.toLowerCase().replace(/\s+/g, '-');
  return `<span class="mfr mfr-${cls}">${t.manufacturer}</span>`;
}

function _shortAirportName(name) {
  return (name || '').replace(/\s*\bInternational\b/gi, '').replace(/\s*\bAirports?\b/gi, '').replace(/\s+/g, ' ').trim();
}

function _colRenderSessionRows(sessions) {
  if (!sessions || !sessions.length) return '<div class="empty">No data</div>';
  const max = Math.max(...sessions.map(s => s.aircraft), 1);
  return sessions.map(s => `
    <div class="col-stats-row col-session-row" data-date="${s.date||''}" data-airport="${s.airport||''}">
      <div class="col-stats-row-bar" style="width:${Math.round(s.aircraft/max*100)}%"></div>
      <div class="col-stats-row-content">
        <span style="width:24px;flex-shrink:0;display:flex;align-items:center;justify-content:center;margin-left:-4px;font-size:16px">${s.flag||''}</span>
        <span class="col-stats-row-name">${esc(window.innerWidth < 768 ? (s.airport || _shortAirportName(s.airport_name || '')) : _shortAirportName(s.airport_name || s.airport || ''))}</span>
        <span class="col-stats-row-sub">${esc(s.date_label||'')}</span>
        <span class="col-stats-row-count" style="align-self:center">${s.aircraft} aircraft · ${s.photos.toLocaleString()} photos</span>
      </div>
    </div>`).join('');
}

function _colRenderRows(items, nameKey, countKey, subFn, prefixFn, rowClass, dataAttr, nameFn) {
  if (!items || !items.length) return '<div class="empty">No data</div>';
  const max = Math.max(...items.map(i => i[countKey]||0), 1);
  return items.map(item => `
    <div class="col-stats-row ${rowClass||''}" ${dataAttr ? dataAttr(item) : ''}>
      <div class="col-stats-row-bar" style="width:${Math.round((item[countKey]||0)/max*100)}%"></div>
      <div class="col-stats-row-content">
        ${prefixFn ? prefixFn(item) : ''}
        <span class="col-stats-row-name${nameKey==='iata'?' col-stats-iata':''}">${nameFn ? nameFn(item) : item[nameKey]}</span>
        ${subFn ? `<span class="col-stats-row-sub">${subFn(item)}</span>` : ''}
        <span class="col-stats-row-count">${(item[countKey]||0).toLocaleString()}</span>
      </div>
    </div>`).join('');
}

function _colRenderRegoRows(items, metricKey, metricLabel) {
  if (!items || !items.length) return '<div class="empty">No data</div>';
  const max = Math.max(...items.map(i => i[metricKey]||0), 1);
  return items.map(item => {
    const badge = item.manufacturer
      ? `<span class="mfr mfr-${item.manufacturer.toLowerCase().replace(/\s+/g,'-')}">${item.manufacturer}</span>`
      : '';
    const typeName = item.aircraft_type_name || item.aircraft_type || '';
    const airlineName = (item.airline || '').replace(/\s*\([^)]+\)\s*$/, '').trim();
    const greyParts = [typeName, airlineName].filter(Boolean);
    const sub = greyParts.length ? `<span style="font-size:10px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(greyParts.join(' · '))}</span>` : '';
    return `<div class="col-stats-row col-rego-row" data-reg="${esc(item.reg)}" style="cursor:pointer">
      <div class="col-stats-row-bar" style="width:${Math.round((item[metricKey]||0)/max*100)}%"></div>
      <div class="col-stats-row-content" style="padding:5px 9px">
        <div style="flex:1;min-width:0;display:flex;flex-direction:column;gap:3px">
          <span style="font-size:12px;font-weight:700">${esc(item.reg)}</span>
          <div style="display:flex;align-items:center;gap:5px;overflow:hidden">${badge}${sub}</div>
        </div>
        <span class="col-stats-row-count" style="align-self:center">${(item[metricKey]||0).toLocaleString()}</span>
      </div>
    </div>`;
  }).join('');
}

function _colRenderHopperRows(items) {
  if (!items || !items.length) return '<div class="empty">No data</div>';
  const max = Math.max(...items.map(h => h.airport_count || 0), 1);
  return items.map(h => {
    const chips = h.airports.map(a => `<span class="col-hopper-chip">${a.flag?a.flag+' ':''}${a.iata}</span>`).join('');
    const badge = h.manufacturer ? `<span class="mfr mfr-${h.manufacturer.toLowerCase().replace(/\s+/g,'-')}">${h.manufacturer}</span>` : '';
    const typeName = h.aircraft_type_name || h.aircraft_type || '';
    const airlineName = (h.airline || '').replace(/\s*\([^)]+\)\s*$/, '').trim();
    const greyParts = [typeName, airlineName].filter(Boolean);
    const sub = greyParts.length ? `<span style="font-size:10px;color:var(--dim)">${esc(greyParts.join(' · '))}</span>` : '';
    return `<div class="col-hopper-row col-rego-row" data-reg="${esc(h.reg)}" style="position:relative;overflow:hidden;display:flex;align-items:center;gap:0;cursor:pointer">
      <div class="col-stats-row-bar" style="position:absolute;inset:0;right:auto;width:${Math.round((h.airport_count||0)/max*100)}%;pointer-events:none"></div>
      <div style="position:relative;flex:1;min-width:0">
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">${esc(h.reg) ? `<span class="col-hopper-reg">${esc(h.reg)}</span>` : ''}${chips}</div>
        <div style="display:flex;align-items:center;gap:5px;margin-top:4px">${badge}${sub}</div>
      </div>
      <span class="col-stats-row-count" style="align-self:center">${h.airport_count} airports${window.innerWidth < 768 ? '' : ` · ${h.photos} photos`}</span>
    </div>`;
  }).join('');
}

function _colRenderStats(d) {
  $('col-sh-photos').textContent   = d.total_photos.toLocaleString();
  $('col-sh-aircraft').textContent = d.total_aircraft.toLocaleString();
  $('col-sh-airlines').textContent = d.total_airlines.toLocaleString();
  $('col-sh-airports').textContent = d.total_airports.toLocaleString();
  $('col-sh-sessions').textContent = d.sessions.length.toLocaleString();

  const ls = d.last_session;
  if (ls) {
    const daysText = ls.days_ago === 0 ? 'Today' : ls.days_ago === 1 ? 'Yesterday' : `${ls.days_ago} days ago`;
    const pillStyle =
      ls.days_ago < 7  ? 'background:var(--surface2);border:1px solid var(--border);color:var(--dim)' :
      ls.days_ago < 30 ? 'background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.4);color:#eab308' :
                         'background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);color:#ef4444';
    if (window.innerWidth < 768) {
      $('col-last-session-bar').innerHTML = `
        <div class="lsb-m-row1">Last Session</div>
        <div class="lsb-m-row2">${ls.flag?ls.flag+' ':''}${esc(_shortAirportName(ls.airport_name))} <span class="lsb-airport-code">${esc(ls.airport)}</span></div>
        <div class="lsb-m-row3">
          <span class="lsb-date">${esc(ls.date_label)}</span>
          <span class="lsb-days-ago" style="${pillStyle};border-radius:10px;padding:2px 10px;font-size:11px;font-weight:600">${daysText}</span>
        </div>`;
    } else {
      $('col-last-session-bar').innerHTML = `
        <span class="lsb-label">Last session</span>
        <span style="display:inline-flex;align-items:baseline;gap:0">
          <span class="lsb-airport-name">${ls.flag?ls.flag+' ':''}${esc(_shortAirportName(ls.airport_name))}</span>
          <span class="lsb-airport-code">${esc(ls.airport)}</span>
        </span>
        <span class="lsb-divider"></span>
        <span class="lsb-date">${esc(ls.date_label)}</span>
        <span class="lsb-spacer"></span>
        <span class="lsb-days-ago" style="${pillStyle};border-radius:10px;padding:2px 10px;font-size:11px;font-weight:600">${daysText}</span>`;
    }
  }

  // Keyword stat boxes
  (d.kw_stats || []).forEach((kw, i) => {
    const numEl = $(`col-kw-num-${i}`), lblEl = $(`col-kw-label-${i}`), box = $(`col-kw-box-${i}`);
    if (!numEl) return;
    if (kw.keyword) {
      numEl.textContent = (kw.count || 0).toLocaleString();
      lblEl.textContent = kw.keyword;
      if (box) {
        box.dataset.keyword = kw.keyword;
        const isLivery = kw.keyword === 'Special Livery';
        box.style.cursor = isLivery ? 'pointer' : 'default';
        box.style.pointerEvents = isLivery ? '' : 'none';
      }
    } else {
      numEl.textContent = '—';
      lblEl.textContent = 'Not set';
      if (box) { box.dataset.keyword = ''; box.style.cursor = 'default'; box.style.pointerEvents = 'none'; }
    }
  });

  $('col-sessions').innerHTML    = _colRenderSessionRows(d.sessions);
  $('col-airlines').innerHTML    = _colRenderRows(d.top_airlines, 'name', 'photos',
    i => { const m = (i.raw_name||'').match(/^(.*?)\s*\(([A-Z]{2,4})\)\s*$/); return m ? m[2] : ''; },
    i => _colAirlineLogo(i.raw_name||''), 'col-airline-row',
    i => `data-airline="${(i.raw_name||'').replace(/"/g,'&quot;')}"`,
    i => { const m = (i.raw_name||'').match(/^(.*?)\s*\(([A-Z]{2,4})\)\s*$/); return m ? esc(m[1]) : esc(i.name||''); });
  $('col-airports').innerHTML    = _colRenderRows(d.top_airports, 'full_name', 'photos',
    i => i.iata || '',
    i => `<span style="width:24px;flex-shrink:0;display:flex;align-items:center;justify-content:center;margin-left:-4px;font-size:16px">${i.flag||''}</span>`,
    'col-airport-row',
    i => `data-iata="${(i.iata||'').replace(/"/g,'&quot;')}"`,
    i => esc(_shortAirportName(i.full_name || i.iata)));
  $('col-types').innerHTML       = _colRenderRows(d.top_types, 'full_name', 'photos',
    i => i.name || '',
    i => _colMfrBadge(i), 'col-type-row',
    i => `data-family="${(i.name||'').replace(/"/g,'&quot;')}"`,
    i => esc(i.full_name || i.name));
  $('col-hoppers').innerHTML     = _colRenderHopperRows(d.airport_hoppers);
  $('col-most-photos').innerHTML   = _colRenderRegoRows(d.most_photos_rego,   'photos',   'photos');
  $('col-most-sessions').innerHTML = _colRenderRegoRows(d.most_sessions_rego, 'sessions', 'sessions');
  // Align separator lines: measure widest count per panel, set uniform width
  ['col-airlines','col-airports','col-types','col-sessions','col-most-photos','col-most-sessions'].forEach(_colAlignCounts);
}

function _colTagClass(tag) {
  if (tag === 'Special Livery') return 'col-sp-tag-special-livery';
  if (tag === 'Military') return 'col-sp-tag-military';
  return 'col-sp-tag-default';
}

// ── Shared click-to-expand helper ─────────────────────────────────────────────
function _colToggleExpand(row, buildFn) {
  // Check if this row's expand is already open before collapsing
  const nextEl = row.nextElementSibling;
  const alreadyOpen = nextEl && nextEl.classList.contains('col-row-expand');

  // Collapse all expands in the panel and remove from DOM
  const panel = row.closest('.col-stats-panel');
  if (panel) {
    panel.querySelectorAll('.col-row-expand').forEach(e => {
      if (e.previousElementSibling) e.previousElementSibling.classList.remove('col-row-active');
      e.remove();
    });
  }

  // Toggle: if this row was already open, just collapsed it above — done
  if (alreadyOpen) return;

  // Create and insert expand div
  const expand = document.createElement('div');
  expand.className = 'col-row-expand col-expand-open';
  expand.style.cssText = 'display:block;flex-shrink:0;max-height:0;overflow:hidden;background:var(--surface2);border-radius:var(--r);transition:max-height 0.25s ease;width:100%;box-sizing:border-box;scrollbar-width:thin;scrollbar-color:var(--border) transparent;';
  requestAnimationFrame(() => { expand.style.maxHeight = '320px'; expand.style.overflowY = 'auto'; expand.style.overflowX = 'hidden'; });
  expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">Loading…</div></div>';
  row.after(expand);
  row.classList.add('col-row-active');
  console.log('expand inserted, offsetHeight=', expand.offsetHeight, 'parent=', expand.parentElement?.id);
  buildFn(expand);
  setTimeout(() => expand.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 50);
}

// ── Keyword stat box expand panel ─────────────────────────────────────────────
let _colKwOpenIdx = null;
let _colKwLiveryCache = null;

function _colKwClose() {
  if (_colKwOpenIdx !== null) {
    const box = $(`col-kw-box-${_colKwOpenIdx}`);
    if (box) {
      box.classList.remove('active');
      const panel = box.querySelector('.col-kw-panel');
      if (panel) panel.remove();
    }
    _colKwOpenIdx = null;
  }
}

function _colKwToggle(i) {
  const box = $(`col-kw-box-${i}`);
  if (!box) return;
  const keyword = box.dataset.keyword;
  if (keyword !== 'Special Livery') return;

  // Close if already open
  if (_colKwOpenIdx === i) { _colKwClose(); return; }
  _colKwClose();

  _colKwOpenIdx = i;
  box.classList.add('active');

  const panel = document.createElement('div');
  panel.className = 'col-kw-panel';
  panel.innerHTML = '<div class="col-kw-panel-body"><div class="col-sp-empty">Loading…</div></div>';
  box.appendChild(panel);

  _colKwLoad(keyword, panel);
}

async function _colKwLoad(keyword, panel) {
  try {
    if (!_colKwLiveryCache) _colKwLiveryCache = await api('/collection/livery-stats');
    const d = _colKwLiveryCache;
    const alliances = d.alliances || [];
    if (!alliances.length) {
      panel.innerHTML = '<div class="col-kw-panel-body"><div class="col-sp-empty">No data</div></div>';
      return;
    }
    const _allianceLogo = { 'Oneworld Livery': '/static/alliance/oneworld.png', 'Star Alliance Livery': '/static/alliance/star-alliance.png', 'SkyTeam Livery': '/static/alliance/skyteam.png' };
    const _allianceLogoH = {};
    const rows = alliances.map(a => {
      const h = _allianceLogoH[a.livery] || '18px';
      const logo = _allianceLogo[a.livery] ? `<img src="${_allianceLogo[a.livery]}" style="height:${h};width:auto;object-fit:contain;flex-shrink:0">` : '';
      return `<div style="display:flex;align-items:center;gap:8px;justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--border)">
        <span style="display:flex;align-items:center;gap:8px;min-width:0">${logo}<span style="font-size:12px;color:var(--text)">${esc(a.livery)}</span></span>
        <span style="font-size:13px;font-weight:700;color:var(--text);min-width:32px;text-align:right">${a.count}</span>
      </div>`;
    }).join('');
    panel.innerHTML = `<div class="col-kw-panel-body">${rows}</div>`;
  } catch (e) {
    panel.innerHTML = '<div class="col-kw-panel-body"><div class="col-sp-empty">Failed to load</div></div>';
  }
}

// Close kw panel when clicking outside
document.addEventListener('click', e => {
  if (_colKwOpenIdx !== null && !e.target.closest('.col-kw-stat')) _colKwClose();
}, true);

function _colSpClose() {
  _colSpPinned = false;
  const pop = $('col-session-popover');
  if (pop) { pop.classList.remove('pinned'); pop.classList.add('hidden'); }
}

function _colShowSessionPopover(row, pin) {
  const date = row.dataset.date, airport = row.dataset.airport;
  if (!date || !airport) return;
  const key = `${date}|${airport}`;
  const pop = $('col-session-popover'), content = $('col-sp-content');
  const renderPop = (aircraft) => {
    const header = `<div class="col-sp-header-row">
      <span class="col-sp-title">Special Aircraft${pin?' — '+row.querySelector('.col-stats-row-name').textContent.trim():''}</span>
      <button class="col-sp-close-btn" onclick="_colSpClose()">✕</button>
    </div>`;
    if (!aircraft.length) {
      content.innerHTML = header + '<div class="col-sp-empty">No tagged aircraft this session</div>';
    } else {
      const rows = aircraft.map(a => {
        const badge = a.manufacturer ? `<span class="mfr mfr-${a.manufacturer.toLowerCase().replace(/\s+/g,'-')}">${a.manufacturer}</span>` : '';
        const tagHtml = a.tags.map(t => `<span class="col-sp-tag ${_colTagClass(t)}">${t}</span>`).join('');
        if (window.innerWidth < 768) {
          return `<div class="col-sp-row col-sp-row-m">
            <div class="col-sp-m-row1"><span class="col-sp-reg">${esc(a.reg)}</span>${badge}</div>
            <div class="col-sp-m-row2">${[a.aircraft_type, a.airline].filter(Boolean).join(' · ')}</div>
            <div class="col-sp-tags">${tagHtml}</div>
          </div>`;
        }
        return `<div class="col-sp-row">
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
            <span class="col-sp-reg">${esc(a.reg)}</span>
            <span class="col-sp-meta">${[badge+a.aircraft_type, a.airline].filter(p=>p.replace(/<[^>]+>/g,'').trim()).join('<span style="color:var(--border);margin:0 2px">·</span>')}</span>
          </div>
          <div class="col-sp-tags">${tagHtml}</div>
        </div>`;
      }).join('');
      content.innerHTML = header + `<div class="col-sp-aircraft">${rows}</div>`;
    }
    const btn = pop.querySelector('.col-sp-close-btn');
    if (btn) btn.addEventListener('click', _colSpClose);
    if (window.twemoji) twemoji.parse(pop, {folder:'svg',ext:'.svg'});
  };
  const panelRect = row.closest('.col-stats-panel').getBoundingClientRect();
  const rowRect   = row.getBoundingClientRect();
  pop.style.top  = `${Math.max(8, Math.min(rowRect.top, window.innerHeight-420))}px`;
  pop.style.left = `${panelRect.right+8}px`;
  _colHideAllPopovers();
  if (pin) { _colSpPinned = true; pop.classList.add('pinned'); } else { pop.classList.remove('pinned'); }
  pop.classList.remove('hidden');
  if (_colSpCache[key]) { renderPop(_colSpCache[key]); return; }
  content.innerHTML = '<div class="col-sp-empty">Loading…</div>';
  api(`/catalog-stats/session?date=${date}&airport=${encodeURIComponent(airport)}`)
    .then(d => { _colSpCache[key] = d.aircraft||[]; renderPop(_colSpCache[key]); })
    .catch(() => { content.innerHTML = '<div class="col-sp-empty">Failed to load</div>'; });
}

function _colInitSessionPopover() {
  const panel = $('col-panel-sessions');
  if (!panel) return;
  panel.addEventListener('click', e => {
    const row = e.target.closest('.col-session-row');
    if (!row) return;
    const date = row.dataset.date, airport = row.dataset.airport;
    if (!date || !airport) return;
    const key = `${date}|${airport}`;
    _colToggleExpand(row, expand => {
      const render = aircraft => {
        if (!aircraft.length) {
          expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">No tagged aircraft this session</div></div>';
        } else {
          const rows = aircraft.map(a => {
            const badge = a.manufacturer ? `<span class="mfr mfr-${a.manufacturer.toLowerCase().replace(/\s+/g,'-')}">${a.manufacturer}</span>` : '';
            const airline = (a.airline || '').replace(/\s*\(.*?\)/g, '').trim();
            const visibleTags = _sessionFilterTags ? a.tags.filter(t => _sessionFilterTags.has(t)) : a.tags;
            const tagHtml = visibleTags.map(t => `<span class="col-sp-tag ${_colTagClass(t)}">${t}</span>`).join('');
            const notesHtml = (a.notes && a.tags.includes('Special Livery'))
              ? `<span style="font-size:11px;color:var(--dim);margin-right:2px">${esc(a.notes)}</span>` : '';
            const parts = [a.aircraft_type, airline].filter(Boolean).map(esc).join('<span class="col-sp-dot">·</span>');
            if (window.innerWidth < 768) {
              return `<div class="col-sp-row col-sp-row-m">
                <div class="col-sp-m-row1"><span class="col-sp-reg">${esc(a.reg)}</span>${badge}</div>
                <div class="col-sp-m-row2">${parts}</div>
                <div class="col-sp-tag-group">${tagHtml}${notesHtml}</div>
              </div>`;
            }
            return `<div class="col-sp-row">
              <div class="col-sp-main"><span class="col-sp-reg">${esc(a.reg)}</span>${badge}<span class="col-sp-meta">${parts}</span></div>
              <div class="col-sp-tag-group">${notesHtml}${tagHtml}</div>
            </div>`;
          }).join('');
          expand.innerHTML = `<div class="col-expand-body"><div class="col-sp-aircraft">${rows}</div></div>`;
        }
        if (window.twemoji) twemoji.parse(expand, {folder:'svg',ext:'.svg'});
      };
      if (_colSpCache[key]) { render(_colSpCache[key]); return; }
      const ftParam = _sessionFilterTags ? `&filter_tags=${encodeURIComponent([..._sessionFilterTags].join(','))}` : '';
      api(`/catalog-stats/session?date=${date}&airport=${encodeURIComponent(airport)}${ftParam}`)
        .then(d => { _colSpCache[key]=d.aircraft||[]; render(_colSpCache[key]); })
        .catch(() => { expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">Failed to load</div></div>'; });
    });
  });
}

// Extract short code from "Qantas (QFA)" → "QFA", or use first word as fallback
function _colExAirlineCode(name) {
  const m = (name || '').match(/\(([A-Z0-9]{2,4})\)\s*$/);
  return m ? m[1] : (name || '').split(' ')[0];
}

const _MFR_BG = {
  'airbus':'#0062a3','boeing':'#1d4289','embraer':'#007a3d','bombardier':'#c8002c',
  'de-havilland':'#b85c00','atr':'#5c3e9f','mcdonnell-douglas':'#555',
  'lockheed-martin':'#1b5e20','saab':'#007070','bae-systems':'#8b0000',
  'british-aerospace':'#8b0000','british-aircraft-corporation':'#8b0000',
  'dassault':'#4a1560','fokker':'#bf4800','comac':'#cc0000','antonov':'#424242',
  'sukhoi':'#4a148c','cessna':'#7a5200','gulfstream':'#00695c','sikorsky':'#2e7d32',
  'bell':'#bf360c','pilatus':'#b71c1c','northrop-grumman':'#4a148c','leonardo':'#006064',
  'beechcraft':'#5d4037','piper':'#00796b','douglas':'#484848','daher':'#bf4800',
  'airbus-helicopters':'#005b6e','north-american':'#37474f',
};

function _colExPill(code, count) {
  return `<span class="col-ex-pill"><span class="col-ex-pill-code">${esc(code)}</span><span class="col-ex-pill-sep"></span><span class="col-ex-pill-count">${count.toLocaleString()}</span></span>`;
}

function _colExTypePill(code, count, manufacturer) {
  const key = (manufacturer || '').toLowerCase().replace(/\s+/g, '-');
  const bg = _MFR_BG[key] || '#444';
  return `<span class="col-ex-pill" style="--pill-fill:${bg};--pill-sep:rgba(255,255,255,0.2);--pill-code-col:#fff;--pill-count-col:rgba(255,255,255,0.65)"><span class="col-ex-pill-code">${esc(code)}</span><span class="col-ex-pill-sep"></span><span class="col-ex-pill-count">${count.toLocaleString()}</span></span>`;
}

function _colExSection(label, pills) {
  return `<div class="col-ex-section"><div class="col-ap-label">${label}</div><div class="col-ex-pills">${pills||'<span class="col-sp-empty">No data</span>'}</div></div>`;
}

function _colInitAirlinePopover() {
  const panel = $('col-panel-airlines');
  if (!panel) return;
  panel.addEventListener('click', e => {
    const row = e.target.closest('.col-airline-row');
    if (!row) return;
    const airline = row.dataset.airline;
    if (!airline) return;
    _colToggleExpand(row, expand => {
      const render = d => {
        const apPills = (d.airports||[]).map(a => _colExPill(a.iata, a.photos)).join('');
        const tyPills = (d.types||[]).map(t => _colExTypePill(t.name, t.photos, t.manufacturer)).join('');
        expand.innerHTML = `<div class="col-expand-body">${_colExSection('Top Airports', apPills)}<div class="col-ap-divider"></div>${_colExSection('Top Aircraft Types', tyPills)}</div>`;
      };
      if (_colApCache[airline]) { render(_colApCache[airline]); return; }
      api(`/catalog-stats/airline?airline=${encodeURIComponent(airline)}`)
        .then(d => { _colApCache[airline]=d; render(d); })
        .catch(() => { expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">Failed</div></div>'; });
    });
  });
}

function _colInitAirportPopover() {
  const panel = $('col-panel-airports');
  if (!panel) return;
  panel.addEventListener('click', e => {
    const row = e.target.closest('.col-airport-row');
    if (!row) return;
    const iata = row.dataset.iata;
    if (!iata) return;
    _colToggleExpand(row, expand => {
      const render = d => {
        const alPills = (d.airlines||[]).map(a => _colExPill(_colExAirlineCode(a.name), a.photos)).join('');
        const tyPills = (d.types||[]).map(t => _colExTypePill(t.name, t.photos, t.manufacturer)).join('');
        expand.innerHTML = `<div class="col-expand-body">${_colExSection('Top Airlines', alPills)}<div class="col-ap-divider"></div>${_colExSection('Top Aircraft Types', tyPills)}</div>`;
      };
      if (_colArppCache[iata]) { render(_colArppCache[iata]); return; }
      api(`/catalog-stats/airport?airport=${encodeURIComponent(iata)}`)
        .then(d => { _colArppCache[iata]=d; render(d); })
        .catch(() => { expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">Failed</div></div>'; });
    });
  });
}

function _colInitTypePopover() {
  const panel = $('col-panel-types');
  if (!panel) return;
  panel.addEventListener('click', e => {
    const row = e.target.closest('.col-type-row');
    if (!row) return;
    const family = row.dataset.family;
    if (!family) return;
    _colToggleExpand(row, expand => {
      const render = d => {
        const alPills = (d.airlines||[]).map(a => _colExPill(_colExAirlineCode(a.name), a.photos)).join('');
        const apPills = (d.airports||[]).map(a => _colExPill(a.iata, a.photos)).join('');
        expand.innerHTML = `<div class="col-expand-body">${_colExSection('Top Airlines', alPills)}<div class="col-ap-divider"></div>${_colExSection('Top Airports', apPills)}</div>`;
      };
      if (_colTyCache[family]) { render(_colTyCache[family]); return; }
      api(`/catalog-stats/type?family=${encodeURIComponent(family)}`)
        .then(d => { _colTyCache[family]=d; render(d); })
        .catch(() => { expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">Failed</div></div>'; });
    });
  });
}

const _colRegoCache = {};

function _colShortDate(dateStr) {
  // "2019-07-07" → "07 Jul '19"
  const [y, m, d] = (dateStr || '').split('-');
  if (!y) return dateStr;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${d} ${months[+m-1]} '${y.slice(2)}`;
}

function _colInitRegoPopover() {
  ['col-panel-most-photos','col-panel-most-sessions','col-panel-hoppers'].forEach(panelId => {
    const panel = $(panelId);
    if (!panel) return;
    panel.addEventListener('click', e => {
      const row = e.target.closest('.col-rego-row');
      if (!row) return;
      const reg = row.dataset.reg;
      if (!reg) return;
      _colToggleExpand(row, expand => {
        const render = d => {
          if (!d.sessions || !d.sessions.length) {
            expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">No sessions found</div></div>';
            return;
          }
          const pills = d.sessions.map(s => {
            const codePart = s.flag
              ? `<span style="display:inline-flex;align-items:center;gap:3px">${s.flag}${esc(s.iata)}</span>`
              : esc(s.iata);
            const tags = s.tags || [];
            const matched = tags.length && (_sessionFilterTags ? tags.some(t => _sessionFilterTags.has(t)) : tags.length > 0);
            const hlClass = matched ? ' col-ex-pill-hl' : '';
            return `<span class="col-ex-pill${hlClass}">` +
              `<span class="col-ex-pill-code">${codePart}</span>` +
              `<span class="col-ex-pill-sep"></span>` +
              `<span class="col-ex-pill-count">${_colShortDate(s.date)}</span>` +
              `<span class="col-ex-pill-sep"></span>` +
              `<span class="col-ex-pill-count">${s.photos.toLocaleString()}</span>` +
              `</span>`;
          }).join('');
          expand.innerHTML = `<div class="col-expand-body">${_colExSection('Sessions', pills)}</div>`;
          if (window.twemoji) twemoji.parse(expand, {folder:'svg',ext:'.svg'});
        };
        if (_colRegoCache[reg]) { render(_colRegoCache[reg]); return; }
        api(`/catalog-stats/rego?rego=${encodeURIComponent(reg)}`)
          .then(d => { _colRegoCache[reg]=d; render(d); })
          .catch(() => { expand.innerHTML = '<div class="col-expand-body"><div class="col-sp-empty">Failed to load</div></div>'; });
      });
    });
  });
}

const REC_START = 5 * 60;   // 05:00 in minutes
const REC_END   = 23 * 60;  // 23:00 in minutes

function _recPct(localMin) {
  const clamped = Math.max(REC_START, Math.min(REC_END, localMin));
  return ((clamped - REC_START) / (REC_END - REC_START) * 100).toFixed(2);
}

function _minToHHMM(min) {
  const h = Math.floor(min / 60), m = min % 60;
  const ap = h >= 12 ? 'pm' : 'am';
  const h12 = h % 12 || 12;
  return `${h12}:${m < 10 ? '0' + m : m}${ap}`;
}

// Weather code → description + icon
const _WX_CODES = {
  0:'Clear',1:'Mainly clear',2:'Partly cloudy',3:'Overcast',
  45:'Fog',48:'Icy fog',51:'Light drizzle',53:'Drizzle',55:'Heavy drizzle',
  61:'Light rain',63:'Rain',65:'Heavy rain',
  71:'Light snow',73:'Snow',75:'Heavy snow',
  80:'Light showers',81:'Showers',82:'Heavy showers',
  85:'Snow showers',86:'Heavy snow showers',
  95:'Thunderstorm',96:'Thunderstorm+hail',99:'Heavy thunderstorm+hail',
};
const _WX_ICONS = {
  0:'☀️',1:'🌤',2:'⛅',3:'☁️',45:'🌫',48:'🌫',
  51:'🌦',53:'🌧',55:'🌧',61:'🌦',63:'🌧',65:'🌧',
  71:'🌨',73:'❄️',75:'❄️',80:'🌦',81:'🌧',82:'⛈',
  85:'🌨',86:'🌨',95:'⛈',96:'⛈',99:'⛈',
};
const _WX_SEVERE = new Set([75,82,86,95,96,99]);

function _initDragScroll(el, snapFn) {
  let down = false, moved = false, axis = null;
  let startX = 0, startY = 0, scrollLeft = 0;
  let velX = 0, lastX = 0, lastT = 0, rafId = null;

  el.style.cursor = 'grab';

  el.addEventListener('mousedown', e => {
    cancelAnimationFrame(rafId);
    down = true; moved = false; axis = null; velX = 0;
    startX = e.pageX; startY = e.pageY;
    scrollLeft = el.scrollLeft;
    lastX = e.pageX; lastT = Date.now();
    el.style.cursor = 'grabbing';
    el.style.userSelect = 'none';
  });

  window.addEventListener('mousemove', e => {
    if (!down) return;
    const dx = e.pageX - startX;
    const dy = e.pageY - startY;
    if (!axis && (Math.abs(dx) > 4 || Math.abs(dy) > 4))
      axis = Math.abs(dx) >= Math.abs(dy) ? 'x' : 'y';
    if (axis !== 'x') return;
    moved = true;
    const now = Date.now();
    velX = (e.pageX - lastX) / Math.max(1, now - lastT);
    lastX = e.pageX; lastT = now;
    el.scrollLeft = scrollLeft - dx;
  });

  window.addEventListener('mouseup', () => {
    if (!down) return;
    down = false;
    el.style.cursor = 'grab';
    el.style.userSelect = '';
    let v = -velX * 12;
    const glide = () => {
      if (Math.abs(v) < 0.5) {
        if (snapFn && moved) snapFn();
        return;
      }
      el.scrollLeft += v;
      v *= 0.88;
      rafId = requestAnimationFrame(glide);
    };
    if (moved) glide();
  });

  el.addEventListener('click', e => { if (moved) e.stopPropagation(); }, true);
}

function _initDragScrollY(el) {
  let down = false, moved = false, axis = null;
  let startX = 0, startY = 0, scrollTop = 0;
  let velY = 0, lastY = 0, lastT = 0, rafId = null;

  // Scroll thumb
  const thumb = document.createElement('div');
  thumb.className = 'rec-scroll-thumb';
  el.appendChild(thumb);
  let fadeTimer = null;

  function _showThumb() {
    const HEADER_H = 94; // sticky header (72px) + col-labels (~22px)
    const ratio = el.clientHeight / el.scrollHeight;
    if (ratio >= 1) return;
    const trackH = el.clientHeight - HEADER_H;
    const thumbH = Math.min(80, Math.max(28, trackH * ratio));
    const maxScroll = el.scrollHeight - el.clientHeight;
    const scrollRatio = maxScroll > 0 ? el.scrollTop / maxScroll : 0;
    const thumbTop = el.scrollTop + HEADER_H + scrollRatio * (trackH - thumbH);
    thumb.style.height = thumbH + 'px';
    thumb.style.top = thumbTop + 'px';
    thumb.style.transition = 'none';
    thumb.style.opacity = '1';
    clearTimeout(fadeTimer);
    fadeTimer = setTimeout(() => {
      thumb.style.transition = 'opacity 0.8s ease';
      thumb.style.opacity = '0';
    }, 800);
  }

  el.addEventListener('scroll', _showThumb);

  el.addEventListener('mousedown', e => {
    cancelAnimationFrame(rafId);
    down = true; moved = false; axis = null; velY = 0;
    startX = e.pageX; startY = e.pageY; scrollTop = el.scrollTop;
    lastY = e.pageY; lastT = Date.now();
    el.style.userSelect = 'none';
    e.preventDefault();
  });

  window.addEventListener('mousemove', e => {
    if (!down) return;
    const dx = e.pageX - startX;
    const dy = e.pageY - startY;
    if (!axis && (Math.abs(dx) > 4 || Math.abs(dy) > 4))
      axis = Math.abs(dy) >= Math.abs(dx) ? 'y' : 'x';
    if (axis !== 'y') return;
    moved = true;
    const now = Date.now();
    velY = (e.pageY - lastY) / Math.max(1, now - lastT);
    lastY = e.pageY; lastT = now;
    el.scrollTop = scrollTop - dy;
    _showThumb();
  });

  window.addEventListener('mouseup', () => {
    if (!down) return;
    down = false; el.style.userSelect = '';
    let v = -velY * 12;
    const glide = () => {
      if (Math.abs(v) < 0.5) return;
      el.scrollTop += v; v *= 0.92;
      _showThumb();
      rafId = requestAnimationFrame(glide);
    };
    if (moved) glide();
  });

  el.addEventListener('click', e => { if (moved) e.stopPropagation(); }, true);
}

let _recLoaded = false;
let _recData   = null;

async function loadRecommendation(force) {
  if (_recLoaded && !force && _recData) {
    // Already rendered — nothing to do, tab switch is instant
    return;
  }
  const el = $('recommendation-content');
  if (!el) return;
  if (!_recData) el.innerHTML = '<div style="padding:24px;text-align:center;color:var(--dim);font-size:13px">Loading…</div>';
  try {
    const data = await api('/recommendation');
    _recData   = data;
    _recLoaded = true;
    if (force) toast("Forecast's in. Pick your spot.");
    el.innerHTML = _renderRecommendation(data);
    const scroll = el.querySelector('.rec-scroll');
    if (scroll) _initDragScroll(scroll, () => {
      const cards = [...scroll.querySelectorAll('.rec-day')];
      const scrollMid = scroll.getBoundingClientRect().left + scroll.clientWidth / 2;
      let closest = null, minDist = Infinity;
      for (const card of cards) {
        const dist = Math.abs(card.getBoundingClientRect().left + card.offsetWidth / 2 - scrollMid);
        if (dist < minDist) { minDist = dist; closest = card; }
      }
      if (closest) {
        const offset = closest.getBoundingClientRect().left - scroll.getBoundingClientRect().left;
        scroll.scrollTo({ left: scroll.scrollLeft + offset - (scroll.clientWidth - closest.offsetWidth) / 2, behavior: 'smooth' });
      }
    });
    el.querySelectorAll('.rec-day').forEach(d => {
      _initDragScrollY(d);
      d.style.overflowY = d.scrollHeight > d.clientHeight ? 'auto' : 'hidden';
    });

    // Initial position: today's card centered, scrolled to current time
    if (scroll) requestAnimationFrame(() => {
      const today = scroll.querySelector('.rec-today');
      if (!today) return;

      const isMobile = window.matchMedia('(max-width: 767px)').matches;
      if (isMobile) {
        // One full-width card per page (scroll-snap) — just jump straight to it.
        scroll.scrollLeft = today.offsetLeft;
      } else {
        const halfView = scroll.clientWidth / 2;
        const halfCard = 350;
        const origLeft = today.offsetLeft;

        // Left spacer so today (and earlier cards) can be centered
        const needed = Math.max(0, halfView - halfCard - origLeft);
        if (needed > 0) {
          const spacer = document.createElement('div');
          spacer.style.cssText = `flex:0 0 ${needed}px;pointer-events:none`;
          scroll.insertBefore(spacer, scroll.firstChild);
        }

        // Center today horizontally (instant, no animation on load)
        scroll.scrollLeft = Math.max(0, origLeft + needed - halfView + halfCard);
      }

      // Scroll today vertically to current time
      const HEADER_H = 94;
      const timeLine = today.querySelector('.rec-current-time');
      if (timeLine) {
        const timeTopPx = parseFloat(timeLine.style.top) || 0;
        const visibleH = today.clientHeight - HEADER_H;
        today.scrollTop = Math.max(0, timeTopPx - HEADER_H - visibleH / 2);
      }
    });
  } catch (e) {
    el.innerHTML = `<div style="padding:24px;text-align:center;color:var(--dim);font-size:13px">${esc(e.message)}</div>`;
  }
}

function _renderRecommendation(data) {
  if (!data || !data.days || !data.days.length)
    return '<div style="padding:24px;text-align:center;color:var(--dim)">No data yet.</div>';
  const cards = data.days.filter(d => d.clusters && d.clusters.length > 0 || d.is_today);
  if (!cards.length) return '<div class="rec-scroll">' + data.days.slice(0,3).map(_renderDayCard).join('') + '</div>';
  return `<div class="rec-scroll">${data.days.map(_renderDayCard).join('')}</div>`;
}

function _recFlightCard(f, nowTs, adjPy, sr, ss) {
  // f.side = 'arrival' | 'departure' (flat event model)
  const isArr   = (f.side === 'arrival');
  const ts      = f.ts;
  const localMin= f.local_min;
  const py      = adjPy ?? 0;
  const time    = _minToHHMM(localMin);
  const light   = f.light;

  let tierClass = '';
  if (!f.qualifying) tierClass = 'rfc-nonq';
  else if (light === 'low_light' || light === 'bad_light') tierClass = 'rfc-badlight';

  const icon = '';

  const { airline, acType } = _parseDetail(f.detail || '');
  const chips = (f.notif_types || []).map(t =>
    `<span class="chip ${chipClass(t)}" style="font-size:9px;height:16px;padding:0 4px">${chipLabel(t)}</span>`
  ).join('');
  const _flagRaw = _flag(_regoCountryCode(f.registration), { h: 10, vab: -1 });
  const flag = _flagRaw ? `<span style="margin-left:3px">${_flagRaw}</span>` : '';

  let st, stBg, stFg;
  if (isArr) {
    st = _barStatus(f, nowTs);
    if (st === 'N/A') st = null;
  } else {
    st = ts > nowTs ? (f.dep_label || 'Scheduled') : 'Departed';
  }
  [stBg, stFg] = _STATUS_STYLE[st] || _STATUS_STYLE['Scheduled'];
  const stPill = st ? `<span style="font-size:9px;font-weight:700;padding:1px 5px;border-radius:10px;background:${stBg};color:${stFg};text-transform:uppercase;letter-spacing:.04em">${esc(st)}</span>` : '';

  const sideClass = isArr ? 'rfc-arr' : 'rfc-dep';
  const livery = f.extra_info ? `<span class="rfc-livery-txt">${esc(f.extra_info)}${icon}</span>` : (icon ? `<span>${icon}</span>` : '');
  const fJson = esc(JSON.stringify(f));
  const srAttr = sr ? ` data-sr="${sr}"` : '';
  const ssAttr = ss ? ` data-ss="${ss}"` : '';

  const logoIcao = f.airline_icao || '';
  const logoSrc = logoIcao
    ? `/api/airline-logo/${encodeURIComponent(logoIcao)}?v=${_LOGO_V}`
    : airline ? `/api/airline-logo-name/${encodeURIComponent(airline.replace(/\s*\(.*?\)/g,'').trim())}?v=${_LOGO_V}` : '';
  const logoImg = logoSrc
    ? `<img src="${logoSrc}" onerror="this.style.display='none'" alt="" style="height:100%;max-height:18px;width:auto;object-fit:contain">`
    : '';
  const logoSlot     = `<div class="rfc-logo-div"></div><div class="rfc-logo-slot">${logoImg}</div>`;
  const logoSlotLeft = `<div class="rfc-logo-slot">${logoImg}</div><div class="rfc-logo-div"></div>`;

  const content = `<div class="rfc-content">
    <div class="rfc-top">${isArr
      ? `<span style="display:flex;align-items:center;gap:3px;flex-shrink:0">${chips}${acType ? `<span class="fc-actype" style="font-size:9px;height:16px;padding:0 4px">${esc(acType)}</span>` : ''}</span><span class="rfc-rego">${esc(f.registration)}${flag}</span>`
      : `<span class="rfc-rego">${esc(f.registration)}${flag}</span><span style="display:flex;align-items:center;gap:3px;flex-shrink:0">${chips}${acType ? `<span class="fc-actype" style="font-size:9px;height:16px;padding:0 4px">${esc(acType)}</span>` : ''}</span>`
    }</div>
    <div class="rfc-time">${isArr
      ? `${livery ? `<span style="margin-right:auto">${livery}</span>` : ''}<span style="display:flex;align-items:center;gap:4px"><span style="font-size:10px;color:var(--dim)">${time}</span>${stPill}</span>`
      : `<span style="display:flex;align-items:center;gap:4px">${stPill}<span style="font-size:10px;color:var(--dim)">${time}</span></span>${livery ? `<span style="margin-left:auto">${livery}</span>` : ''}`
    }</div>
  </div>`;

  // Desktop: logo spans the full card height, beside a 2-row content block (unchanged).
  const desktopBlock = `<div class="rfc-desktop">${isArr ? `${logoSlotLeft}${content}` : `${content}${logoSlot}`}</div>`;

  // Mobile: 3 stacked rows — rego+flag / chips+type / status+time. Logo spans
  // only the first two rows. No livery name shown.
  const mobileBlock = `<div class="rfc-mobile">
    <div class="rfc-m-upper">
      <div class="rfc-m-logo">${logoImg}</div>
      <div class="rfc-m-text">
        <div class="rfc-m-row1"><span class="rfc-rego">${esc(f.registration)}${flag}</span></div>
        <div class="rfc-m-row2">${isArr ? `${acType ? `<span class="fc-actype" style="font-size:9px;height:16px;padding:0 4px">${esc(acType)}</span>` : ''}${chips}` : `${chips}${acType ? `<span class="fc-actype" style="font-size:9px;height:16px;padding:0 4px">${esc(acType)}</span>` : ''}`}</div>
      </div>
    </div>
    <div class="rfc-m-row3">${isArr ? `<span style="font-size:10px;color:var(--dim)">${time}</span>${stPill}` : `${stPill}<span style="font-size:10px;color:var(--dim)">${time}</span>`}</div>
  </div>`;

  return `<div class="rec-flight-card ${sideClass} ${tierClass}" style="top:${py.toFixed(1)}px" title="${esc(f.registration)} ${isArr ? 'arr' : 'dep'} ${time}" onclick="openRecDetail(this)" data-side="${isArr ? 'arr' : 'dep'}" data-f="${fJson}"${srAttr}${ssAttr}>${desktopBlock}${mobileBlock}</div>`;
}

const COMPRESS_GAP_MINS = 60;  // gaps longer than this are compressed (1h)
const COMPRESS_GAP_PX   = 44;  // visual height of a skip segment
const TIMELINE_SCALE_PX = 4;   // px per minute in active segments
// Mobile's 3-row mini card (rego / chips+type / status+time) is taller than
// desktop's 2-row card, so overlap-avoidance needs more vertical room per card.
const CARD_H_PX = window.matchMedia('(max-width: 767px)').matches ? 76 : 56;

function _buildLayout(eventMins) {
  const PAD = 15;
  const pts = new Set([REC_START, REC_END]);
  for (const m of eventMins) {
    pts.add(Math.max(REC_START, m - PAD));
    pts.add(Math.max(REC_START, m));
    pts.add(Math.min(REC_END, m + PAD));
  }
  const sorted = [...pts].sort((a, b) => a - b);
  const segs = [];
  let curPx = 0;
  for (let i = 0; i < sorted.length - 1; i++) {
    const sMin = sorted[i], eMin = sorted[i + 1];
    const span = eMin - sMin;
    if (span <= 0) continue;
    if (span > COMPRESS_GAP_MINS - 2 * PAD) {
      segs.push({ type: 'gap', startMin: sMin, endMin: eMin, startPx: curPx });
      curPx += COMPRESS_GAP_PX;
    } else {
      const h = span * TIMELINE_SCALE_PX;
      segs.push({ type: 'active', startMin: sMin, endMin: eMin, startPx: curPx, height: h });
      curPx += h;
    }
  }
  function toY(min) {
    const c = Math.max(REC_START, Math.min(REC_END, min));
    for (let i = segs.length - 1; i >= 0; i--) {
      if (c >= segs[i].startMin) {
        const s = segs[i];
        if (s.type === 'gap') return s.startPx + COMPRESS_GAP_PX / 2;
        const frac = Math.min(1, (c - s.startMin) / (s.endMin - s.startMin));
        return s.startPx + frac * s.height;
      }
    }
    return 0;
  }
  return { segs, totalPx: curPx, toY };
}

function _renderDayCard(day) {
  const todayCls = day.is_today ? ' rec-today' : '';
  const clusters = day.clusters || [];
  const nowTs    = Math.floor(Date.now() / 1000);
  const sr = day.sunrise_ts || 0;
  const ss = day.sunset_ts  || 0;

  // Weather
  const wc     = day.weather_code || 0;
  const wxIcon = _WX_ICONS[wc] || '🌡';
  const wxDesc = _WX_CODES[wc] || '';
  const severe = day.weather_severe;
  const wxStyle= severe ? 'color:var(--danger);font-weight:600' : 'color:var(--dim)';
  const tempRange = day.temp_min != null && day.temp_max != null
    ? `<span class="rdc-weather rdc-temp" style="color:var(--dim)">${day.temp_min}° – ${day.temp_max}°</span>` : '';
  const wxHtml = wxDesc ? `<span class="rdc-weather" style="${wxStyle}">${wxIcon} ${esc(wxDesc)}</span>${tempRange}` : '';

  // Window times with am/pm
  function _toAmPm(min) {
    const h = Math.floor(min / 60), m = min % 60;
    const ap = h >= 12 ? 'pm' : 'am';
    const h12 = h % 12 || 12;
    return `${h12}:${m < 10 ? '0'+m : m}${ap}`;
  }

  const primary = clusters[0];
  const winHtml = primary && primary.show_window
    ? `<span class="rdc-window">Window: ${_toAmPm(primary.recommended_start_local_min)} – ${_toAmPm(primary.end_local_min)}</span>`
    : '';

  const primaryDur = primary ? (primary.end_local_min - primary.recommended_start_local_min) : 0;
  const shorterAlts = (primary && primary.show_window && primary.alternative_windows || []).filter(w => {
    const dur = w.end_local_min - w.start_local_min;
    return dur < primaryDur;
  });
  const altHtml = shorterAlts.length
    ? `<span class="rdc-alt">Alt: ${shorterAlts.map(w => {
        const dur = w.end_local_min - w.start_local_min;
        const earlierMins = primary.recommended_start_local_min - w.start_local_min;
        const shorterMins = primaryDur - dur;
        const parts = [];
        if (earlierMins > 0) parts.push(`${earlierMins}m earlier`);
        if (shorterMins > 0) parts.push(`${shorterMins}m shorter`);
        return `${_toAmPm(w.start_local_min)}–${_toAmPm(w.end_local_min)}${parts.length ? ' (' + parts.join(', ') + ')' : ''}`;
      }).join(' · ')}</span>`
    : '';

  const totalRegs = day.total_regs || 0;

  const hdr = `<div class="rec-day-hdr">
    <div>
      <div class="rec-d-label">${esc(day.label)}</div>
      ${winHtml || '<span class="rdc-window" style="color:var(--dim);font-weight:400">No window</span>'}
      ${altHtml}
    </div>
    <div style="text-align:right">
      <div class="rec-d-count">${totalRegs > 0 ? totalRegs + (totalRegs > 1 ? ' flights' : ' flight') : ''}</div>
      ${wxHtml}
    </div>
  </div>`;

  if (!clusters.length) {
    return `<div class="rec-day${todayCls}">${hdr}<div class="rec-empty">No activity</div></div>`;
  }

  // Build compressed layout from all event times
  const eventMins = [];
  for (const cluster of clusters) {
    for (const f of (cluster.flights || [])) {
      if (f.local_min != null) eventMins.push(f.local_min);
    }
    if (cluster.recommended_start_local_min != null) eventMins.push(cluster.recommended_start_local_min);
    if (cluster.end_local_min != null) eventMins.push(cluster.end_local_min);
  }
  const layout = _buildLayout(eventMins);

  // Hour labels — only for hours in active segments
  const hourLabels = [6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22].filter(h => {
    const min = h * 60;
    const seg = [...layout.segs].reverse().find(s => min >= s.startMin);
    return seg && seg.type === 'active' && min <= seg.endMin;
  }).map(h => {
    const py = layout.toY(h * 60);
    const ap = h === 0 ? '12am' : h < 12 ? `${h}am` : h === 12 ? '12pm' : `${h-12}pm`;
    return `<span class="rec-axis-label" style="top:${py.toFixed(1)}px">${ap}</span>`;
  }).join('');

  // Gap segment labels
  const gapHtml = layout.segs.filter(s => s.type === 'gap').map(s => {
    const mins = s.endMin - s.startMin;
    const h = Math.floor(mins / 60), m = mins % 60;
    const label = h > 0 ? (m > 0 ? `${h}h ${m}m` : `${h}h`) : `${m}m`;
    return `<div class="rec-gap" style="top:${s.startPx}px;height:${COMPRESS_GAP_PX}px"></div>`;
  }).join('');

  // Sunrise/sunset axis markers
  let srLine = '', ssLine = '';
  if (day.sunrise_ts) {
    const py = layout.toY(_tsToLocalMin(day.sunrise_ts, day));
    srLine = `<span class="rec-sun-line" style="top:${py.toFixed(1)}px">Sunrise</span>`;
  }
  if (day.sunset_ts) {
    const py = layout.toY(_tsToLocalMin(day.sunset_ts, day));
    ssLine = `<span class="rec-sun-line rec-sun-set" style="top:${py.toFixed(1)}px">Sunset</span>`;
  }

  // Collect all card events; separate arrivals (left) and departures (right)
  const arrEvts = [], depEvts = [];
  for (const cluster of clusters) {
    for (const f of (cluster.flights || [])) {
      if (f.local_min == null) continue;
      const evObj = { f, py: layout.toY(f.local_min) };
      if (f.side === 'arrival') arrEvts.push(evObj);
      else depEvts.push(evObj);
    }
  }
  function _adjustPos(evts) {
    evts.sort((a, b) => a.py - b.py);
    let floor = -Infinity;
    for (const ev of evts) {
      if (ev.py < floor) ev.py = floor;
      floor = ev.py + CARD_H_PX;
    }
  }
  _adjustPos(arrEvts);
  _adjustPos(depEvts);
  const _pyMap = {};
  for (const ev of arrEvts) _pyMap[ev.f.registration + '_arr_' + (ev.f.ts || 0)] = ev.py;
  for (const ev of depEvts) _pyMap[ev.f.registration + '_dep_' + (ev.f.ts || 0)] = ev.py;

  // Current time line (today only) — built after card layout so overlaps can be checked
  let currentTimeLine = '';
  if (day.is_today) {
    const dt = new Date();
    const nowPy = layout.toY(dt.getHours() * 60 + dt.getMinutes());
    const nowH = dt.getHours(), nowM = dt.getMinutes();
    const nowStr = `${nowH < 10 ? '0'+nowH : nowH}:${nowM < 10 ? '0'+nowM : nowM}`;
    const HALF = CARD_H_PX / 2;
    const arrOvlp = arrEvts.some(ev => nowPy >= ev.py - HALF && nowPy <= ev.py + HALF);
    const depOvlp = depEvts.some(ev => nowPy >= ev.py - HALF && nowPy <= ev.py + HALF);
    const leftSeg  = arrOvlp ? '' : `<div class="rct-seg rct-left"><span class="rec-current-label">Now ${nowStr}</span></div>`;
    const rightSeg = depOvlp ? '' : `<div class="rct-seg rct-right"></div>`;
    currentTimeLine = `<div class="rec-current-time" style="top:${nowPy.toFixed(1)}px">
      ${leftSeg}
      <div class="rct-seg rct-center"></div>
      ${rightSeg}
    </div>`;
  }

  // Clusters: render boxes + lulls first, then all cards on top
  // so no cluster box border can ever paint over a flight card.
  let boxesHtml = '', cardsHtml = '';
  for (const cluster of clusters) {
    const ws = cluster.recommended_start_local_min;
    const we = cluster.end_local_min;
    if (!cluster.show_window) {
      for (const f of (cluster.flights || [])) {
        if (f.local_min == null) continue;
        const key = f.registration + '_' + (f.side === 'arrival' ? 'arr' : 'dep') + '_' + (f.ts || 0);
        if (_pyMap[key] != null)
          cardsHtml += _recFlightCard(f, nowTs, _pyMap[key], sr, ss);
      }
      continue;
    }

    // Global box extent: top = first qualifying card on either side, bot = last.
    let globalTop = Infinity, globalBot = -Infinity;
    for (const f of (cluster.flights || [])) {
      if (!f.qualifying || f.local_min == null) continue;
      if (f.local_min < ws || f.local_min > we) continue;
      const key = f.registration + '_' + (f.side === 'arrival' ? 'arr' : 'dep') + '_' + (f.ts || 0);
      const py = _pyMap[key];
      if (py == null) continue;
      globalTop = Math.min(globalTop, py - CARD_H_PX / 2);
      globalBot = Math.max(globalBot, py + CARD_H_PX / 2);
    }
    if (globalTop === Infinity) {
      globalTop = layout.toY(ws) - CARD_H_PX / 2;
      globalBot = layout.toY(we) + CARD_H_PX / 2;
    }

    // Per-side adjustment: if an out-of-window card straddles the global top/bottom border on one
    // side, push that side's border past the card so the line avoids it. Other side stays clean.
    // "Out of window" = local_min outside [ws, we], regardless of f.qualifying.
    let adjLTop = globalTop, adjRTop = globalTop;
    let adjLBot = globalBot, adjRBot = globalBot;
    for (const f of (cluster.flights || [])) {
      if (f.local_min == null) continue;
      const outsideWindow = f.local_min < ws || f.local_min > we;
      if (!outsideWindow) continue;
      const key = f.registration + '_' + (f.side === 'arrival' ? 'arr' : 'dep') + '_' + (f.ts || 0);
      const py = _pyMap[key];
      if (py == null) continue;
      const isLeft = f.side === 'arrival';
      // Top: card straddles globalTop on this side
      if (py < globalTop + CARD_H_PX / 2 && py + CARD_H_PX / 2 > globalTop) {
        if (isLeft) adjLTop = Math.max(adjLTop, py + CARD_H_PX / 2);
        else adjRTop = Math.max(adjRTop, py + CARD_H_PX / 2);
      }
      // Bottom: card straddles globalBot on this side
      if (py - CARD_H_PX / 2 < globalBot && py > globalBot - CARD_H_PX / 2) {
        if (isLeft) adjLBot = Math.min(adjLBot, py - CARD_H_PX / 2);
        else adjRBot = Math.min(adjRBot, py - CARD_H_PX / 2);
      }
    }

    // SVG spans globalTop→globalBot; per-side adjustments (adjLTop/adjRTop etc.) drive the step.
    const boxTop = globalTop;
    const boxBot = globalBot;
    const boxH   = boxBot - boxTop;
    const H   = boxH.toFixed(1);
    const VW  = 1000;
    const CLR = '#f59e0b', sw = '2', sda = '6 4';
    const lineAttr = `stroke="${CLR}" stroke-width="${sw}" stroke-dasharray="${sda}" fill="none" vector-effect="non-scaling-stroke"`;
    const RX = 14, RY = 7;
    const x0 = 1, x1 = VW - 1, xMid = 500;

    // Per-side local Y coords relative to combined boxTop
    const lY0 = adjLTop - boxTop, lY1 = adjLBot - boxTop;
    const rY0 = adjRTop - boxTop, rY1 = adjRBot - boxTop;

    let svgLines = '';
    // Background fill
    if (lY1 > lY0) svgLines += `<rect x="0" y="${lY0}" width="${xMid}" height="${lY1-lY0}" fill="rgba(245,158,11,0.04)" stroke="none" rx="${RX}" ry="${RY}"/>`;
    if (rY1 > rY0) svgLines += `<rect x="${xMid}" y="${rY0}" width="${VW-xMid}" height="${rY1-rY0}" fill="rgba(245,158,11,0.04)" stroke="none" rx="${RX}" ry="${RY}"/>`;

    const needTopStep = Math.abs(lY0 - rY0) > 1;
    const needBotStep = Math.abs(lY1 - rY1) > 1;

    // Left side: outer corners + verticals + horizontals (shortened when step corner follows)
    if (lY1-RY > lY0+RY) svgLines += `<line ${lineAttr} x1="${x0}" y1="${lY0+RY}" x2="${x0}" y2="${lY1-RY}"/>`;
    svgLines += `<path ${lineAttr} d="M ${x0} ${lY0+RY} A ${RX} ${RY} 0 0 1 ${x0+RX} ${lY0}"/>`;
    svgLines += `<path ${lineAttr} d="M ${x0+RX} ${lY0} H ${needTopStep ? xMid-RX : xMid}"/>`;
    svgLines += `<path ${lineAttr} d="M ${x0} ${lY1-RY} A ${RX} ${RY} 0 0 0 ${x0+RX} ${lY1}"/>`;
    svgLines += `<path ${lineAttr} d="M ${x0+RX} ${lY1} H ${needBotStep ? xMid-RX : xMid}"/>`;

    // Right side: outer corners + verticals + horizontals (shortened when step corner follows)
    if (rY1-RY > rY0+RY) svgLines += `<line ${lineAttr} x1="${x1}" y1="${rY0+RY}" x2="${x1}" y2="${rY1-RY}"/>`;
    svgLines += `<path ${lineAttr} d="M ${x1-RX} ${rY0} A ${RX} ${RY} 0 0 1 ${x1} ${rY0+RY}"/>`;
    svgLines += `<path ${lineAttr} d="M ${needTopStep ? xMid+RX : xMid} ${rY0} H ${x1-RX}"/>`;
    svgLines += `<path ${lineAttr} d="M ${x1-RX} ${rY1} A ${RX} ${RY} 0 0 0 ${x1} ${rY1-RY}"/>`;
    svgLines += `<path ${lineAttr} d="M ${needBotStep ? xMid+RX : xMid} ${rY1} H ${x1-RX}"/>`;

    // Center step: top corner is convex (outward), bottom corner is concave (inward).
    // Sweep directions are computed from which side is higher to avoid hardcoding per-case.
    const _drawStep = (lY, rY) => {
      const highY = Math.min(lY, rY), lowY = Math.max(lY, rY);
      const rightIsHigher = rY < lY;
      // topSweep: convex = arc curves away from the notch
      const topSweep = rightIsHigher ? 0 : 1;
      // botSweep: concave = arc curves into the notch (opposite of top)
      const botSweep = 1 - topSweep;
      const topX = rightIsHigher ? xMid + RX : xMid - RX;
      const botX = rightIsHigher ? xMid - RX : xMid + RX;
      svgLines += `<path ${lineAttr} d="M ${topX} ${highY} A ${RX} ${RY} 0 0 ${topSweep} ${xMid} ${highY+RY}"/>`;
      if (lowY - highY > 2*RY) svgLines += `<line ${lineAttr} x1="${xMid}" y1="${highY+RY}" x2="${xMid}" y2="${lowY-RY}"/>`;
      svgLines += `<path ${lineAttr} d="M ${xMid} ${lowY-RY} A ${RX} ${RY} 0 0 ${botSweep} ${botX} ${lowY}"/>`;
    };
    if (needTopStep) _drawStep(lY0, rY0);
    if (needBotStep) _drawStep(lY1, rY1);

    boxesHtml += `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${VW} ${H}" preserveAspectRatio="none" overflow="visible" style="position:absolute;top:${boxTop.toFixed(1)}px;left:2px;right:2px;height:${H}px;width:calc(100% - 4px);display:block;pointer-events:none;z-index:2">${svgLines}</svg>`;

    for (const lull of (cluster.lulls || [])) {
      const midMin = (lull.start_local_min + lull.end_local_min) / 2;
      const midPx  = layout.toY(midMin);
      // Use the fixed (non-mobile-inflated) card height here — this is just a
      // "don't draw the label on top of a card" buffer, not the spacing pass.
      const overlaps = [...arrEvts, ...depEvts].some(
        ev => midPx > ev.py - 16 && midPx < ev.py + 56 + 16
      );
      if (overlaps) continue;
      const dur    = Math.round(lull.end_local_min - lull.start_local_min);
      const durH   = Math.floor(dur / 60), durM = dur % 60;
      const durStr = durH > 0 ? `${durH}hr${durM > 0 ? ` ${durM}min` : ''}` : `${durM}min`;
      boxesHtml += `<div class="rec-break-time" style="top:${midPx.toFixed(1)}px">Break · ${durStr}</div>`;
    }

    for (const f of (cluster.flights || [])) {
      if (f.local_min == null) continue;
      const key = f.registration + '_' + (f.side === 'arrival' ? 'arr' : 'dep') + '_' + (f.ts || 0);
      if (_pyMap[key] != null)
        cardsHtml += _recFlightCard(f, nowTs, _pyMap[key], sr, ss);
    }
  }
  const clusterHtml = boxesHtml + cardsHtml;

  const colLabels = `<div class="rec-col-labels">
    <span class="rec-col-arr">Arrivals</span>
    <span class="rec-col-dep">Departures</span>
  </div>`;

  const axisHtml = layout.segs.map(s => {
    const cls = s.type === 'gap' ? 'rec-axis-gap' : 'rec-axis-active';
    const h   = s.type === 'gap' ? COMPRESS_GAP_PX : s.height;
    return `<div class="${cls}" style="top:${s.startPx}px;height:${h}px"></div>`;
  }).join('');

  const body = `<div class="rec-timeline">
    <div class="rec-timeline-inner" style="height:${layout.totalPx}px">
      ${axisHtml}
      ${hourLabels}
      ${gapHtml}
      ${srLine}${ssLine}
      ${currentTimeLine}
      ${clusterHtml}
    </div>
  </div>`;

  return `<div class="rec-day${todayCls}">${hdr}${colLabels}${body}</div>`;
}

// Helper: convert unix timestamp to local minutes-from-midnight for timeline positioning
function _tsToLocalMin(ts, day) {
  if (!ts) return 0;
  const d = new Date(ts * 1000);
  return d.getHours() * 60 + d.getMinutes();
}

async function loadSystemTasks() {
  const tasksEl = $('sys-tasks-body');
  const apisEl  = $('sys-apis-body');
  if (!tasksEl && !apisEl) return;
  try {
    const d = await api('/system-tasks');
    const now = d.now;

    function _dot(ok) {
      if (ok === null || ok === undefined) return '<span class="sys-dot pending"></span>';
      return `<span class="sys-dot ${ok ? 'ok' : 'err'}"></span>`;
    }
    function _rel(ts, now) {
      if (!ts) return '—';
      const diff = ts - now;
      const abs  = Math.abs(diff);
      const str  = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.round(abs/60)}m` : abs < 86400 ? `${Math.round(abs/3600)}h` : `${Math.round(abs/86400)}d`;
      return diff < 0 ? `${str} ago` : `in ${str}`;
    }
    function _row(item, subs) {
      const lastStr = item.last_ts ? _rel(item.last_ts, now) : 'Never';
      const nextStr = item.next_ts
        ? (item.next_ts <= now ? 'Now' : _rel(item.next_ts, now))
        : (item.interval ? '—' : 'On demand');
      const tip = item.error ? ` title="${esc(item.error)}"` : '';
      const subHtml = (subs && subs.length)
        ? `<span class="sys-subdep">${subs.map(s => `${_dot(s.ok)} ${esc(s.label)}`).join('&nbsp;&nbsp;&nbsp;')}</span><span></span><span></span><span></span>`
        : '';
      return `<span${tip}>${_dot(item.ok)}</span>
              <span class="sys-name"${tip}>${esc(item.name)}</span>
              <span class="sys-time">${lastStr}</span>
              <span class="sys-time">${nextStr}</span>
              <span></span>
              <span class="sys-desc">${esc(item.desc)}</span>
              <span></span><span></span>
              ${subHtml}`;
    }

    if (tasksEl) {
      const header = `<span></span><span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em">Task</span>
                      <span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">Last Run</span>
                      <span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">Next Run</span>
                      <hr class="sys-sep">`;
      const apiByName = name => (d.apis || []).find(a => a.name === name);
      const fr24      = apiByName('FR24 Airport Feed');
      const openMeteo = apiByName('Open-Meteo');
      const logostream= apiByName('Logostream');
      const adsbFi    = apiByName('adsb.fi Military');
      const icaoList  = apiByName('ICAOList (GitHub)');
      const rows = d.tasks.map(item => {
        let subs = [];
        if (item.name === 'Airport Scan') {
          subs = [
            fr24       && { ok: fr24.ok,       label: 'Flightradar 24' },
            openMeteo  && { ok: openMeteo.ok,  label: 'Open-Meteo' },
            logostream && { ok: logostream.ok, label: 'Logostream' },
          ].filter(Boolean);
        } else if (item.name === 'Military Scan') {
          subs = [adsbFi && { ok: adsbFi.ok, label: 'adsb.fi' }].filter(Boolean);
        } else if (item.name === 'ICAO List Update') {
          subs = [icaoList && { ok: icaoList.ok, label: 'ICAOList (GitHub)' }].filter(Boolean);
        }
        return _row(item, subs);
      });
      tasksEl.innerHTML = `<div class="sys-grid">${header}${rows.join('<hr class="sys-sep">')}</div>`;
    }
    if (apisEl) {
      const header = `<span></span><span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em">API</span>
                      <span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">Last Call</span>
                      <span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">Next</span>
                      <hr class="sys-sep">`;
      apisEl.innerHTML = `<div class="sys-grid">${header}${d.apis.map(item => _row(item)).join('<hr class="sys-sep">')}</div>`;
    }
  } catch(e) {
    if (tasksEl) tasksEl.innerHTML = '<span style="color:var(--danger);font-size:12px">Failed to load</span>';
    if (apisEl)  apisEl.innerHTML  = '<span style="color:var(--danger);font-size:12px">Failed to load</span>';
  }
}

async function loadInfo() {
  loadSystemTasks();
  try {
    const s = await api('/status');
    const vEl = $('info-version');
    if (vEl) vEl.textContent = s.version ? `v${s.version}` : '';

    const grid = $('info-status-grid');
    if (!grid) return;

    const airport = s.airport_name
      ? `${s.airport_name} (${s.airport_iata})`
      : (s.airport_iata || s.airport_code || '—');

    // Populate airport card
    const airportCodeEl = $('info-airport-code');
    if (airportCodeEl) airportCodeEl.value = s.airport_iata || s.airport_code || '';
    const tzInEl = $('info-timezone-input');
    if (tzInEl && !tzInEl.dataset.userEdited) tzInEl.value = s.effective_tz || s.airport_tz || '';

    function _fmtRuntime(secs) {
      if (!secs && secs !== 0) return '—';
      const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600), m = Math.floor((secs % 3600) / 60);
      const parts = [];
      if (d) parts.push(`${d}d`);
      if (h || d) parts.push(`${h}h`);
      parts.push(`${m}m`);
      return parts.join(' ');
    }

    const statusRows = [
      { dot: true,  name: 'Status',           value: 'Running' },
      { dot: false, name: 'Current Time',     value: s.current_time ? `${esc(s.current_time)} <span style="color:var(--dim);font-size:11px">${esc(s.effective_tz || '')}</span>` : '—' },
      { dot: false, name: 'Server Name',      value: s.hostname ? esc(s.hostname) : '—' },
      { dot: false, name: 'Operating System', value: s.os   ? esc(s.os)   : '—' },
      { dot: false, name: 'Architecture',     value: s.arch ? esc(s.arch) : '—' },
      { dot: false, name: 'Connection',       value: s.connection ? esc(s.connection) : '—' },
      { dot: false, name: 'Runtime',          value: _fmtRuntime(s.runtime_secs) },
    ];
    grid.innerHTML = `<div class="sys-status-grid">${statusRows.map(r =>
      `<span class="sys-dot ${r.dot ? 'ok' : ''}" style="${r.dot ? '' : 'visibility:hidden'}"></span>
       <span class="sys-name">${esc(r.name)}</span>
       <span class="sys-time">${r.value}</span>`
    ).join('<hr class="sys-sep">')}</div>`;
  } catch (e) {
    const grid = $('info-status-grid');
    if (grid) grid.innerHTML = '<span style="color:var(--danger);font-size:12px">Unreachable</span>';
  }
}

function switchSubtab(name) {
  document.querySelectorAll('#tab-settings .srch-subtab').forEach(b => b.classList.toggle('active', b.dataset.subtab === name));
  document.querySelectorAll('.set-subtab-page').forEach(p => p.classList.toggle('hidden', p.id !== 'subtab-' + name));
  if (name === 'airports') { apLoad(); atLoad(); stTagsLoad(); }
  if (name === 'logs') logsLoad();
}

// ── Logs ──────────────────────────────────────────────────────────────────────
async function logsLoad() {
  const el = $('logs-output');
  if (!el) return;
  try {
    const data = await api('/logs?lines=1000');
    el.textContent = data.text || '(log file is empty)';
    el.scrollTop = el.scrollHeight;
  } catch (e) {
    el.textContent = 'Failed to load log: ' + e.message;
  }
}

// ── Airport overrides ────────────────────────────────────────────────────────
async function apLoad() {
  const list = $('ap-list');
  if (!list) return;
  const data = await api('/airports');
  if (!data.length) {
    list.innerHTML = '<div class="detail" style="padding:4px 2px">No custom airports yet.</div>';
    return;
  }
  list.innerHTML = data.map(a => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(a.iata)}</div>
        <div class="filter-secondary">${esc(a.name)}${a.country_code ? ' · ' + esc(a.country_code) : ''}</div>
      </div>
      <button class="del-btn" onclick="apDelete('${esc(a.iata)}')">✕</button>
    </div>`).join('');
}

async function apAdd() {
  const code = $('ap-code').value.trim().toUpperCase();
  const name = $('ap-name').value.trim();
  const cc   = $('ap-country').value.trim().toUpperCase();
  if (!code || !name) { toast('Code and name are required'); return; }
  await api('/airports', { method: 'POST', body: JSON.stringify({ iata: code, name, country_code: cc }) });
  $('ap-code').value = ''; $('ap-name').value = ''; $('ap-country').value = '';
  apLoad();
  toast('Airport added');
}

async function apDelete(iata) {
  await api(`/airports/${encodeURIComponent(iata)}`, { method: 'DELETE' });
  apLoad();
}

// ── Aircraft type overrides ──────────────────────────────────────────────────
function _atRow(a) {
  return `<div class="filter-row">
    <div class="main">
      <div class="filter-primary">${esc(a.icao)}</div>
      <div class="filter-secondary">${esc(a.name)}</div>
    </div>
    <button class="del-btn" onclick="atDelete('${esc(a.icao)}')">✕</button>
  </div>`;
}

async function atLoad() {
  const list = $('at-list');
  if (!list) return;
  const data = await api('/aircraft-types');
  list.innerHTML = data.length
    ? data.map(_atRow).join('')
    : '<div class="detail" style="padding:4px 2px">No custom types yet.</div>';
}

async function atAdd() {
  const icao = $('at-code').value.trim().toUpperCase();
  const name = $('at-name').value.trim();
  if (!icao || !name) { toast('Code and name are required'); return; }
  await api('/aircraft-types', { method: 'POST', body: JSON.stringify({ icao, name }) });
  $('at-code').value = ''; $('at-name').value = '';
  atLoad();
  toast('Aircraft type added');
}

async function atDelete(icao) {
  await api(`/aircraft-types/${encodeURIComponent(icao)}`, { method: 'DELETE' });
  atLoad();
}

async function atRefresh() {
  toast('Refreshing ICAOList from GitHub…');
  await api('/aircraft-types/refresh', { method: 'POST' });
  toast('Refresh started — may take a few seconds');
}

// ── Session panel tag filter ──────────────────────────────────────────────────

let _sessionFilterTags = null;  // null = show all; Set = filter to these tags

async function kwStatLoad(tags, settings) {
  const el = $('kw-stat-selects');
  if (!el || !tags.length) return;
  el.innerHTML = [0,1,2].map(i => {
    const saved = settings[`COLLECTION_KW_STAT_${i+1}`] || '';
    const opts = ['', ...tags].map(t =>
      `<option value="${esc(t)}"${t === saved ? ' selected' : ''}>${t || '— Not set —'}</option>`
    ).join('');
    return `<div style="display:flex;align-items:center;gap:8px">
      <span style="font-size:12px;color:var(--dim);width:16px;flex-shrink:0">${i+1}.</span>
      <select class="setting-select" style="flex:1" onchange="_saveSetting('COLLECTION_KW_STAT_${i+1}',this.value)">${opts}</select>
    </div>`;
  }).join('');
}

async function stTagsLoad() {
  const el = $('st-tags-list');
  if (!el) return;
  el.innerHTML = '<span style="color:var(--dim);font-size:12px">Loading…</span>';
  try {
    const [tagsData, settings] = await Promise.all([api('/catalog-stats/tags'), api('/settings')]);
    kwStatLoad(tagsData.tags || [], settings);
    const selected = new Set((settings.collection_session_tags || '').split(',').map(t => t.trim()).filter(Boolean));
    _sessionFilterTags = selected.size ? selected : null;
    const tags = tagsData.tags || [];
    if (!tags.length) { el.innerHTML = '<span style="color:var(--dim);font-size:12px">No tags found in catalog</span>'; return; }
    el.innerHTML = tags.map(t => {
      const active = !selected.size || selected.has(t);
      return `<button class="st-tag-pill${active ? ' active' : ''}" data-tag="${esc(t)}" onclick="stTagsToggle(this)">${esc(t)}</button>`;
    }).join('');
  } catch (e) {
    el.innerHTML = '<span style="color:var(--dim);font-size:12px">Failed to load tags</span>';
  }
}

function stTagsToggle(btn) {
  btn.classList.toggle('active');
  const pills = [...document.querySelectorAll('#st-tags-list .st-tag-pill')];
  const active = pills.filter(b => b.classList.contains('active')).map(b => b.dataset.tag);
  const val = active.length === pills.length ? '' : active.join(',');
  _sessionFilterTags = val ? new Set(val.split(',')) : null;
  Object.keys(_colSpCache).forEach(k => delete _colSpCache[k]);
  _saveSetting('collection_session_tags', val);
}

// ── Search tab ───────────────────────────────────────────────────────────────
let _srchCatApNames = {};     // IATA → short airport name, from autocomplete
let _srchTabInited  = false;  // dropdowns created once per page load
let _srchFiltersTs  = 0;      // epoch ms when fl+rt filters were last fetched
let _srchCheckMs    = null;   // check interval in ms, loaded lazily from settings
let _srchCatStale   = false;  // true when catalogue needs re-init after a force refresh
let _srchInited     = false;
let _srchTimer      = null;
let _srchFlTimer    = null;
let _srchFlData     = null;   // null=not fetched; array=fetched (possibly empty)
let _srchActiveSub  = 'flights';

async function _srchMaybeRefreshFilters() {
  if (_srchCheckMs === null) {
    try {
      const s = await api('/settings');
      _srchCheckMs = (parseInt(s.CHECK_INTERVAL_MINUTES, 10) || 30) * 60_000;
    } catch { _srchCheckMs = 30 * 60_000; }
  }
  if (Date.now() - _srchFiltersTs > _srchCheckMs) {
    _srchFiltersTs = Date.now();
    _srchFlLoadFilters();
    _srchRtLoadFilters();
  }
}

function _srchSetBtn(subtab) {
  const btn = $('btn-refresh'), lbl = $('btn-refresh-label');
  if (!btn || !lbl) return;
  if (subtab === 'catalog') {
    btn.onclick = () => loadCollection(true);
    lbl.textContent = 'Refresh Collection';
  } else {
    btn.onclick = () => forceCheck();
    lbl.textContent = 'Refresh Feed';
  }
}

function _srchSubtab(name) {
  _srchActiveSub = name;
  document.querySelectorAll('.srch-subtab').forEach(b =>
    b.classList.toggle('active', b.dataset.srchSubtab === name));
  document.querySelectorAll('.srch-page').forEach(p =>
    p.classList.toggle('hidden', p.id !== `srch-page-${name}`));
  _srchSetBtn(name);
  if (name === 'catalog') {
    if (_srchCatStale) { _srchCatStale = false; _srchInited = false; }
    _srchInit();
  }
  if (name === 'route') $('srch-rt-status').textContent = 'Enter a flight number or select a filter.';
}

async function _srchInit() {
  if (_srchInited) return;
  _srchInited = true;
  $('srch-status').textContent = 'Loading filters…';
  try {
    const d = await api('/search/autocomplete');

    // Manufacturers — from aircraft_manufacturer LR property
    _srchDDSetOptions('srch-dd-cat-mfr', d.manufacturers || []);

    // Types — show as "B789 (Boeing)" style
    const typeOpts = (d.types || []).map(t => t.manufacturer ? `${t.value} (${t.manufacturer})` : t.value);
    _srchDDSetOptions('srch-dd-cat-type', typeOpts);

    // Airlines — display without parenthetical code, keep full value for API matching
    _srchDDSetOptions('srch-dd-cat-airline', (d.airlines || []).map(a => ({
      value: a.value,
      label: a.value.replace(/\s*\([^)]*\)\s*$/, '').trim(),
    })));

    // Airports — show shortened full name, keep IATA as value for API matching
    (d.airports || []).forEach(ap => {
      _srchCatApNames[ap.iata] = _shortAirportName(ap.full_name || '') || ap.iata;
    });
    _srchDDSetOptions('srch-dd-cat-airport', (d.airports || []).map(ap => ({
      value: ap.iata,
      label: `${ap.iata} · ${_srchCatApNames[ap.iata]}`,
    })));

    // Keywords
    _srchDDSetOptions('srch-dd-cat-keyword', d.keywords || []);

    $('srch-status').textContent = 'Enter a registration or select a filter.';
  } catch (e) {
    $('srch-status').textContent = 'Failed to load catalogue filters.';
  }
}

function _srchSelectedVals(selId) {
  const sel = $(selId);
  if (!sel) return [];
  return [...sel.selectedOptions].map(o => o.value).filter(Boolean);
}

function _srchClear() {
  $('srch-rego').value = '';
  ['srch-dd-cat-mfr','srch-dd-cat-type','srch-dd-cat-airline','srch-dd-cat-airport','srch-dd-cat-keyword'].forEach(id => {
    if (_srchDDs[id]) _srchDDClear(id);
  });
  $('srch-results').innerHTML = '';
  $('srch-status').textContent = 'Enter a registration or select a filter.';
  _srchSyncClearVisibility();
}

function _srchRun(immediate) {
  _srchSyncClearVisibility();
  clearTimeout(_srchTimer);
  _srchTimer = setTimeout(_srchExec, immediate ? 0 : 350);
}

async function _srchExec() {
  const rego     = ($('srch-rego').value || '').trim();
  // Type values may have "(Manufacturer)" suffix — strip it for the API
  const types         = [...(_srchDDs['srch-dd-cat-type']?.values    || [])].map(v => v.replace(/\s*\(.+?\)$/, ''));
  const manufacturers = [...(_srchDDs['srch-dd-cat-mfr']?.values     || [])];
  const airlines      = [...(_srchDDs['srch-dd-cat-airline']?.values  || [])];
  const airports      = [...(_srchDDs['srch-dd-cat-airport']?.values  || [])];
  const keywords      = [...(_srchDDs['srch-dd-cat-keyword']?.values  || [])];

  if (!rego && !types.length && !manufacturers.length && !airlines.length && !airports.length && !keywords.length) {
    $('srch-results').innerHTML = '';
    $('srch-status').textContent = 'Enter a registration or select a filter.';
    return;
  }

  $('srch-status').textContent = 'Searching…';
  const params = new URLSearchParams();
  if (rego) params.set('rego', rego);
  types.forEach(v         => params.append('type',         v));
  manufacturers.forEach(v => params.append('manufacturer', v));
  airlines.forEach(v      => params.append('airline',      v));
  airports.forEach(v      => params.append('airport',      v));
  keywords.forEach(v      => params.append('keyword',      v));

  try {
    const d = await api(`/search?${params}`);
    if (d.error) { $('srch-status').textContent = `Error: ${d.error}`; return; }

    // Group rows by registration
    const byReg = new Map();
    for (const row of (d.results || [])) {
      if (!byReg.has(row.registration)) {
        byReg.set(row.registration, { reg: row.registration, airline: row.airline, aircraft_type: row.aircraft_type, manufacturer: row.manufacturer, sessions: [] });
      }
      byReg.get(row.registration).sessions.push({ date: row.date, airport: row.airport, photos: row.photos, keywords: row.keywords, notes: row.notes || '' });
    }

    const regs = [...byReg.values()];
    $('srch-status').textContent = regs.length
      ? `${regs.length} aircraft`
      : 'No results.';

    const _catHtml = _srchCols(regs.map(r => {
      const badge = r.manufacturer ? mfrBadge(r.manufacturer) : '';
      const flag  = _flag(_regoCountryCode(r.reg), { h: 14 });
      // Extract ICAO from parenthetical e.g. "AirAsia (AXM)" → icao="AXM", name="AirAsia"
      const icaoMatch  = (r.airline || '').match(/\(([A-Z]{2,4})\)\s*$/);
      const airlineIcao = icaoMatch ? icaoMatch[1] : '';
      const airlineName = (r.airline || '').replace(/\s*\([^)]*\)\s*$/, '').trim();
      const logo = airlineName ? _srchLogoWithFallback(airlineIcao, airlineName, 20, '') : '';
      const rows = r.sessions.map(s => {
        const cc       = _airportCountry(s.airport);
        const aflag    = cc ? _flag(cc, {h:11}) : '';
        const apName   = _srchCatApNames[s.airport] || s.airport;
        const kwPills  = s.keywords.map(k => `<span class="col-sp-tag ${_colTagClass(k)}">${esc(k)}</span>`).join('');
        const notesHtml = s.notes ? `<span style="font-size:11px;color:var(--dim);font-style:italic;white-space:nowrap;flex-shrink:0">${esc(s.notes)}</span>` : '';
        if (window.innerWidth < 768) {
          const hasKw = kwPills.length > 0;
          return `<div class="srch-fl-row srch-fl-row-m">
            <div class="srch-fl-m-row1">
              <span class="srch-fl-date">${esc(s.date)}</span>
              ${aflag}<span>${esc(s.airport)}</span>
            </div>
            ${hasKw ? `<div class="srch-fl-m-row2">${kwPills}${notesHtml}</div>` : ''}
          </div>`;
        }
        return `<div class="srch-fl-row" style="display:flex;gap:8px;align-items:center">
          <span class="srch-fl-date" style="flex-shrink:0;width:90px">${esc(s.date)}</span>
          <span class="srch-fl-fn srch-cat-ap" style="display:inline-flex;align-items:center;gap:5px;white-space:nowrap;flex-shrink:0">${aflag}${esc(apName)}</span>
          <span class="srch-fl-route" style="flex:1">${s.photos} photo${s.photos !== 1 ? 's' : ''}</span>
          ${notesHtml}
          ${kwPills ? `<span style="display:inline-flex;align-items:center;gap:3px;flex-shrink:0">${kwPills}</span>` : ''}
        </div>`;
      }).join('');
      const sessionPill = `<span style="display:inline-flex;align-items:center;gap:5px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:2px 10px;font-size:11px;white-space:nowrap;flex-shrink:0"><span style="color:var(--dim);text-transform:uppercase;letter-spacing:.05em;font-size:10px">Sessions</span><span style="font-weight:600">${r.sessions.length}</span></span>`;
      const airlineHtml = airlineName ? `<span style="font-size:12px;color:var(--dim)">${esc(airlineName)}${r.aircraft_type ? `<span style="margin:0 4px;opacity:.4">·</span>${esc(r.aircraft_type)}` : ''}</span>` : '';
      const headerHtml = window.innerWidth < 768
        ? `<div class="srch-fl-header-m">
            <div class="srch-fl-hm-row1"><span class="srch-fl-rego"><span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${logo || flag || ''}</span>${esc(r.reg)}</span>${badge}</div>
            ${airlineHtml ? `<div class="srch-fl-hm-row2">${airlineHtml}</div>` : ''}
            <div class="srch-fl-hm-row3">${sessionPill}</div>
          </div>`
        : `<div class="srch-fl-header">
            <span class="srch-fl-rego"><span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${logo || flag || ''}</span>${esc(r.reg)}</span>
            ${badge}
            ${airlineHtml}
            <span style="flex:1"></span>
            ${sessionPill}
          </div>`;
      return `<div class="srch-fl-card">
        ${headerHtml}
        <div class="srch-fl-rows">${rows}</div>
      </div>`;
    }));
    $('srch-results').innerHTML = _catHtml;
    // Align airport name widths per masonry column
    requestAnimationFrame(() => {
      $('srch-results').querySelectorAll('.srch-col').forEach(col => {
        const spans = col.querySelectorAll('.srch-cat-ap');
        let maxW = 0;
        spans.forEach(el => { maxW = Math.max(maxW, el.scrollWidth); });
        if (maxW > 0) spans.forEach(el => { el.style.width = (maxW + 12) + 'px'; });
      });
    });
  } catch (e) {
    $('srch-status').textContent = 'Search failed.';
  }
}

// ── Route search ─────────────────────────────────────────────────────────────
let _srchRtTimer = null;

function _srchRtClear() {
  $('srch-rt-fn').value = '';
  ['srch-dd-rt-origin','srch-dd-rt-dest','srch-dd-rt-airline'].forEach(id => { if (_srchDDs[id]) _srchDDClear(id); });
  _srchRtMirror(null);
  $('srch-rt-results').innerHTML = '';
  $('srch-rt-status').textContent = 'Enter a flight number or select a filter.';
  _srchSyncClearVisibility();
}

let _srchRtHomeLabel = '';

async function _srchRtLoadFilters() {
  try {
    const d = await api('/search/route-filters');
    _srchRtHomeLabel = d.home || '';
    _srchDDSetOptions('srch-dd-rt-origin',  d.origins  || []);
    _srchDDSetOptions('srch-dd-rt-dest',    d.dests    || []);
    _srchDDSetOptions('srch-dd-rt-airline', d.airlines || []);
  } catch (_) {}
}

function _srchRtSetGreyed(id, greyed, homeLabel) {
  const trigger = $(`${id}-trigger`);
  const lbl     = $(`${id}-label`);
  if (greyed) {
    if (_srchDDs[id]) _srchDDs[id].values.clear();
    $(`${id}-panel`)?.querySelectorAll('.srch-dd-opt').forEach(o => o.classList.remove('selected'));
    if (lbl)     { lbl.textContent = homeLabel; lbl.style.color = 'var(--dim)'; lbl.style.fontStyle = 'italic'; lbl.classList.remove('has-value'); }
    if (trigger) { trigger.style.opacity = '0.5'; }
  } else {
    if (trigger) { trigger.style.opacity = ''; }
    if (lbl)     { lbl.style.color = ''; lbl.style.fontStyle = ''; }
    _srchDDUpdateLabel(id);
  }
}

function _srchRtMirror(side) {
  // 'side' = which dropdown the user just interacted with — it always wins
  const originHas = (_srchDDs['srch-dd-rt-origin']?.values?.size || 0) > 0;
  const destHas   = (_srchDDs['srch-dd-rt-dest']?.values?.size   || 0) > 0;
  const home = _srchRtHomeLabel;

  // Always restore both first
  _srchRtSetGreyed('srch-dd-rt-origin', false, home);
  _srchRtSetGreyed('srch-dd-rt-dest',   false, home);

  // The side that triggered takes priority; fall back to whichever has a value
  const greyDest   = (side === 'origin' ? originHas : side === 'dest' ? false : originHas) && home;
  const greyOrigin = (side === 'dest'   ? destHas   : side === 'origin' ? false : destHas)  && home;

  if (greyDest)        _srchRtSetGreyed('srch-dd-rt-dest',   true, home);
  else if (greyOrigin) _srchRtSetGreyed('srch-dd-rt-origin', true, home);
}

function _srchRtRun(immediate) {
  _srchSyncClearVisibility();
  clearTimeout(_srchRtTimer);
  _srchRtTimer = setTimeout(_srchRtExec, immediate ? 0 : 400);
}

async function _srchRtExec() {
  const fn      = ($('srch-rt-fn').value || '').trim();
  const origins  = [...(_srchDDs['srch-dd-rt-origin']?.values  || [])];
  const dests    = [...(_srchDDs['srch-dd-rt-dest']?.values    || [])];
  const airlines = [...(_srchDDs['srch-dd-rt-airline']?.values || [])];
  const hasFilter = fn || origins.length || dests.length || airlines.length;
  if (!hasFilter) {
    $('srch-rt-results').innerHTML = '';
    $('srch-rt-status').textContent = 'Enter a flight number or select a filter.';
    return;
  }
  $('srch-rt-status').textContent = 'Searching…';
  try {
    const params = new URLSearchParams({ fn });
    origins.forEach(v => params.append('origin', v));
    dests.forEach(v => params.append('dest', v));
    airlines.forEach(v => params.append('airline', v));
    const d = await api(`/search/route?${params}`);
    const results = d.results || [];

    // Group by flight_number → aircraft_type rows; capture airline/route from first result
    const byFn = new Map();
    for (const r of results) {
      if (!byFn.has(r.flight_number)) byFn.set(r.flight_number, {
        fn: r.flight_number, types: [],
        airline: r.airline || '',
        origin_iata: r.origin_iata || '', origin_name: r.origin_name || '',
        dest_iata: r.dest_iata || '',     dest_name: r.dest_name || '',
        airport_iata: r.airport_iata || '', airport_name: r.airport_name || ''
      });
      byFn.get(r.flight_number).types.push(r);
    }
    const groups = [...byFn.values()];

    $('srch-rt-status').textContent = groups.length
      ? `${groups.length} flight${groups.length > 1 ? 's' : ''}`
      : 'No results.';

    $('srch-rt-results').innerHTML = _srchCols(groups.map(g => {
      // Header: logo + flight number + airline · route
      const logo = g.airline ? _srchLogoWithFallback('', g.airline, 20, '') : '';
      const routeTxt = (() => {
        if (window.innerWidth < 768) {
          const home = esc(g.airport_iata);
          if (g.origin_iata) return `${esc(g.origin_iata)} → ${home}`;
          if (g.dest_iata)   return `${home} → ${esc(g.dest_iata)}`;
          return home;
        }
        const home = esc(_shortAirportName(g.airport_name) || g.airport_iata);
        if (g.origin_iata) { const o = esc(_shortAirportName(g.origin_name) || g.origin_iata); return `${o} → ${home}`; }
        if (g.dest_iata)   { const d = esc(_shortAirportName(g.dest_name)   || g.dest_iata);   return `${home} → ${d}`; }
        return home;
      })();
      const subTxt = [g.airline ? esc(g.airline) : '', routeTxt].filter(Boolean).join('<span style="margin:0 5px;opacity:.4">·</span>');

      const totalCount = g.types.reduce((s, t) => s + (t.count || 0), 0);
      const rawPcts = g.types.map(t => totalCount > 0 ? (t.count / totalCount) * 100 : 0);
      const minPct = Math.min(...rawPcts), maxPct = Math.max(...rawPcts);
      let cumOffset = 0;
      const rows = g.types.map((t, ti) => {
        const mfr = t.aircraft_type ? _deriveManufacturerFromType(t.aircraft_type) : '';
        const badge = mfr ? mfrBadge(mfr) : '';
        const lastDt = t.last_seen_ts ? new Date(t.last_seen_ts * 1000).toLocaleDateString(undefined, { day:'numeric', month:'short', year:'numeric' }) : '—';
        const pct = Math.round(rawPcts[ti]);
        const start = Math.round(cumOffset);
        const end = Math.round(cumOffset + rawPcts[ti]);
        cumOffset += rawPcts[ti];
        // rel=1 → highest (green), rel=0 → lowest (red-orange)
        const rel = maxPct > minPct ? (rawPcts[ti] - minPct) / (maxPct - minPct) : 1;
        const r = Math.round(220 + rel * (34 - 220));
        const gv = Math.round(55 + rel * (197 - 55));
        const b = Math.round(40 + rel * (80 - 40));
        const fill = `rgba(${r},${gv},${b},0.22)`;
        const bg = `linear-gradient(to right,var(--surface2) ${start}%,${fill} ${start}%,${fill} ${end}%,var(--surface2) ${end}%)`;
        return `<div class="srch-fl-row" style="grid-template-columns:minmax(150px,auto) 1fr auto;background:${bg}">
          <span class="srch-fl-fn" style="display:flex;align-items:center;gap:5px">${badge}<span>${esc(t.aircraft_type)}</span></span>
          <span class="srch-fl-date"><span style="color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:.04em">Last seen</span> ${esc(lastDt)}</span>
          <span class="srch-fl-status" style="color:var(--dim);text-align:right">${pct}%</span>
        </div>`;
      }).join('');
      return `<div class="srch-fl-card">
        <div class="srch-fl-header">
          <span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${logo}</span>
          <span class="srch-fl-rego">${esc(g.fn)}</span>
          <span style="font-size:12px;color:var(--dim)">${subTxt}</span>
        </div>
        <div class="srch-fl-rows">
          <div style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 0">Equipment History</div>
          ${rows}
        </div>
      </div>`;
    }));
  } catch (e) {
    $('srch-rt-status').textContent = 'Search failed.';
  }
}

function _deriveManufacturerFromType(t) {
  t = (t || '').toUpperCase();
  if (t.startsWith('B') && /^B[0-9]/.test(t)) return 'Boeing';
  if (t.startsWith('A') && /^A[0-9]/.test(t)) return 'Airbus';
  if (t.startsWith('E') && /^E[0-9]/.test(t)) return 'Embraer';
  if (t.startsWith('AT')) return 'ATR';
  if (t.startsWith('DH')) return 'De Havilland';
  if (t.startsWith('CRJ') || t.startsWith('CR')) return 'Bombardier';
  return '';
}

// ── Registration search ───────────────────────────────────────────────────────
function _srchCols(cards) {
  const n = window.innerWidth >= 2000 ? 3 : window.innerWidth >= 900 ? 2 : 1;
  if (n === 1) return `<div class="srch-col-wrap"><div class="srch-col">${cards.join('')}</div></div>`;
  const cols = Array.from({ length: n }, () => ({ html: [], h: 0 }));
  for (const card of cards) {
    // Estimate height: base header + row count * row height
    const rows = (card.match(/srch-fl-row/g) || []).length;
    const h = 52 + (rows > 0 ? 28 + rows * 30 : 0);
    const shortest = cols.reduce((a, b) => a.h <= b.h ? a : b);
    shortest.html.push(card);
    shortest.h += h + 8;
  }
  return `<div class="srch-col-wrap">${cols.map(c => `<div class="srch-col">${c.html.join('')}</div>`).join('')}</div>`;
}

function _srchLogoWithFallback(icao, name, size, fallbackHtml) {
  const src = icao
    ? `/api/airline-logo/${encodeURIComponent(icao)}?v=${_LOGO_V}`
    : `/api/airline-logo-name/${encodeURIComponent((name||'').replace(/\s*\(.*?\)/g,'').trim())}?v=${_LOGO_V}`;
  if (!src) return fallbackHtml;
  return `<span style="display:inline-flex;align-items:center;flex-shrink:0">` +
    `<img src="${src}" loading="lazy" alt="" style="height:${size}px;max-width:${size*2}px;object-fit:contain" onerror="this.style.display='none';this.nextElementSibling.style.display='inline-flex'">` +
    `<span style="display:none">${fallbackHtml}</span>` +
    `</span>`;
}

function _srchLastSeenPill(ts, dateStr) {
  const daysAgo = ts ? Math.floor((Date.now() / 1000 - ts) / 86400) : 999;
  const style = daysAgo < 7
    ? 'background:var(--surface2);border:1px solid var(--border);color:var(--dim)'
    : daysAgo < 30
      ? 'background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.4);color:#eab308'
      : 'background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);color:#ef4444';
  const labelCol = daysAgo < 7 ? 'var(--dim)' : daysAgo < 30 ? 'rgba(234,179,8,0.7)' : 'rgba(239,68,68,0.7)';
  return `<span style="display:inline-flex;align-items:center;gap:5px;${style};border-radius:10px;padding:2px 10px;font-size:11px;white-space:nowrap"><span style="color:${labelCol};text-transform:uppercase;letter-spacing:.05em;font-size:10px">Last seen</span><span style="font-weight:600">${esc(dateStr)}</span></span>`;
}

function _srchFlClear() {
  $('srch-fl-rego').value = '';
  ['srch-dd-mfr','srch-dd-airline','srch-dd-type'].forEach(id => { if (_srchDDs[id]) _srchDDClear(id); });
  _srchFlData = null;
  $('srch-fl-results').innerHTML = '';
  $('srch-fl-status').textContent = 'Enter a registration or select a filter.';
  _srchSyncClearVisibility();
}

// ── Custom searchable dropdown ────────────────────────────────────────────────
const _srchDDs = {};

function _srchDDCreate(containerId, placeholder, options, onChange) {
  const wrap = $(containerId);
  if (!wrap) return;
  const id = containerId;
  _srchDDs[id] = { values: new Set(), options, onChange, placeholder };

  wrap.innerHTML = `
    <button type="button" class="srch-dd-trigger" id="${id}-trigger" onclick="_srchDDToggle('${id}')">
      <span class="srch-dd-trigger-label" id="${id}-label">${esc(placeholder)}</span>
      <span class="srch-dd-arrow">▼</span>
    </button>
    <div class="srch-dd-panel" id="${id}-panel">
      <div class="srch-dd-search">
        <input type="text" placeholder="Search…" oninput="_srchDDSearch('${id}', this.value)" id="${id}-search" autocomplete="off">
      </div>
      <div class="srch-dd-list" id="${id}-list">
        <div class="srch-dd-opt srch-dd-clear" data-val="" onclick="_srchDDClear('${id}')">Clear all</div>
        ${options.length ? options.map(o => `<div class="srch-dd-opt" data-val="${esc(o)}" onclick="_srchDDToggleOpt('${id}', '${esc(o)}')">${esc(o)}</div>`).join('') : '<div class="srch-dd-empty">Loading…</div>'}
      </div>
    </div>`;
}

function _srchDDToggle(id) {
  const panel = $(`${id}-panel`);
  const trigger = $(`${id}-trigger`);
  const isOpen = panel.classList.contains('open');
  document.querySelectorAll('.srch-dd-panel.open').forEach(p => p.classList.remove('open'));
  document.querySelectorAll('.srch-dd-trigger.open').forEach(t => t.classList.remove('open'));
  if (!isOpen) {
    panel.classList.add('open');
    trigger.classList.add('open');
    const inp = $(`${id}-search`);
    if (inp) { inp.value = ''; _srchDDSearch(id, ''); setTimeout(() => inp.focus(), 50); }
  }
}

function _srchDDSearch(id, q) {
  const list = $(`${id}-list`);
  if (!list) return;
  const lq = q.toLowerCase();
  list.querySelectorAll('.srch-dd-opt').forEach(opt => {
    if (opt.classList.contains('srch-dd-clear')) return;
    const v = (opt.dataset.val || '').toLowerCase();
    opt.classList.toggle('hidden', !!lq && !v.includes(lq));
  });
  const empty = $(`${id}-empty`);
  const visible = [...list.querySelectorAll('.srch-dd-opt:not(.hidden):not(.srch-dd-clear)')];
  if (!visible.length) {
    if (!empty) list.insertAdjacentHTML('beforeend', `<div class="srch-dd-empty" id="${id}-empty">No results</div>`);
  } else if (empty) empty.remove();
}

function _srchDDUpdateLabel(id) {
  const dd = _srchDDs[id]; if (!dd) return;
  const lbl = $(`${id}-label`);
  const trigger = $(`${id}-trigger`);
  const n = dd.values.size;
  if (lbl) {
    const singleVal   = n === 1 ? [...dd.values][0] : '';
    const singleLabel = singleVal ? (dd.labelOf ? (dd.labelOf[singleVal] ?? singleVal) : singleVal) : '';
    lbl.textContent = n === 0 ? dd.placeholder : n === 1 ? singleLabel : `${n} selected`;
    lbl.classList.toggle('has-value', n > 0);
  }
  if (trigger) trigger.style.color = n > 0 ? 'var(--accent)' : '';
  _srchSyncClearVisibility();
}

function _srchSyncClearVisibility() {
  document.querySelectorAll('.srch-bar').forEach(bar => {
    const hasInput = [...bar.querySelectorAll('.srch-input')].some(inp => inp.value.trim().length > 0);
    const hasDD    = bar.querySelector('.srch-dd-trigger-label.has-value') !== null;
    const clearBtn = bar.querySelector('.srch-clear');
    if (clearBtn) clearBtn.classList.toggle('srch-clear-hidden', !hasInput && !hasDD);
  });
}

function _srchDDToggleOpt(id, val) {
  const dd = _srchDDs[id]; if (!dd) return;
  if (dd.values.has(val)) dd.values.delete(val); else dd.values.add(val);
  _srchDDUpdateLabel(id);
  const panel = $(`${id}-panel`);
  panel?.querySelectorAll('.srch-dd-opt[data-val]').forEach(o => {
    if (!o.dataset.val) return;
    o.classList.toggle('selected', dd.values.has(o.dataset.val));
  });
  if (dd.onChange) dd.onChange();
}

function _srchDDClear(id) {
  const dd = _srchDDs[id]; if (!dd) return;
  dd.values.clear();
  _srchDDUpdateLabel(id);
  const panel = $(`${id}-panel`);
  panel?.querySelectorAll('.srch-dd-opt').forEach(o => o.classList.remove('selected'));
  if (dd.onChange) dd.onChange();
}

// Close dropdowns when clicking outside
document.addEventListener('click', e => {
  if (!e.target.closest('.srch-dd')) {
    document.querySelectorAll('.srch-dd-panel.open').forEach(p => p.classList.remove('open'));
    document.querySelectorAll('.srch-dd-trigger.open').forEach(t => t.classList.remove('open'));
  }
});

function airlineNameOf(g) { return g.flights[0] ? (_parseDetail(g.flights[0].detail || '').airline || '') : ''; }
function acTypeOf(g)     { return g.flights[0] ? (_parseDetail(g.flights[0].detail || '').acType  || '') : ''; }

function _srchDDSetOptions(id, options) {
  const dd = _srchDDs[id]; if (!dd) return;
  // options can be strings or {value, label} objects
  const isObj = options.length > 0 && typeof options[0] === 'object';
  dd.options = options;
  dd.labelOf = isObj ? Object.fromEntries(options.map(o => [o.value, o.label])) : null;
  const list = $(`${id}-list`); if (!list) return;
  list.innerHTML = `<div class="srch-dd-opt srch-dd-clear" data-val="" onclick="_srchDDClear('${id}')">Clear all</div>` +
    options.map(o => {
      const val = isObj ? o.value : o;
      const lbl = isObj ? o.label : o;
      return `<div class="srch-dd-opt${dd.values.has(val) ? ' selected' : ''}" data-val="${esc(val)}" onclick="_srchDDToggleOpt('${id}', '${esc(val)}')">${esc(lbl)}</div>`;
    }).join('');
}

async function _srchFlLoadFilters() {
  try {
    const d = await api('/search/flight-filters');
    _srchDDSetOptions('srch-dd-mfr',     d.manufacturers || []);
    _srchDDSetOptions('srch-dd-airline', d.airlines       || []);
    _srchDDSetOptions('srch-dd-type',    d.types          || []);
  } catch (_) {}
}

function _srchFlFilter() {
  if (_srchFlData === null) { _srchFlRun(true); return; }
  const mfrs    = _srchDDs['srch-dd-mfr']?.values;
  const airlines = _srchDDs['srch-dd-airline']?.values;
  const types   = _srchDDs['srch-dd-type']?.values;
  const matchSet = (set, val) => !set?.size || [...set].some(s => (val || '').toLowerCase().includes(s.toLowerCase()));
  const filtered = _srchFlData.filter(c =>
    matchSet(mfrs, c.mfr) && matchSet(airlines, c.airline) && matchSet(types, c.type)
  );
  $('srch-fl-status').textContent = `${filtered.length} aircraft`;
  $('srch-fl-results').innerHTML = _srchCols(filtered.map(c => c.html));
}

function _srchFlRun(immediate) {
  _srchSyncClearVisibility();
  _srchFlData = null;
  clearTimeout(_srchFlTimer);
  _srchFlTimer = setTimeout(_srchFlExec, immediate ? 0 : 400);
}

async function _srchFlExec() {
  const rego = ($('srch-fl-rego').value || '').trim();
  const hasFilter = ['srch-dd-mfr','srch-dd-airline','srch-dd-type'].some(id => _srchDDs[id]?.values?.size);
  if (!rego && !hasFilter) {
    $('srch-fl-results').innerHTML = '';
    $('srch-fl-status').textContent = 'Enter a registration or select a filter.';
    return;
  }
  $('srch-fl-status').textContent = 'Searching…';
  try {
    const d = await api(`/search/flights?rego=${encodeURIComponent(rego)}`);
    const results = d.results || [];
    const sightingOnly = d.sighting_only || [];

    // Group by registration
    const byReg = new Map();
    for (const r of results) {
      if (!byReg.has(r.registration)) byReg.set(r.registration, { reg: r.registration, manufacturer: r.manufacturer, last_seen_ts: r.last_seen_ts, flights: [] });
      byReg.get(r.registration).flights.push(r);
    }
    const regs = [...byReg.values()].sort((a, b) => (b.last_seen_ts || 0) - (a.last_seen_ts || 0));
    const sightingSorted = [...sightingOnly].sort((a, b) => (b.last_seen_ts || 0) - (a.last_seen_ts || 0));
    const total = regs.length + sightingSorted.length;

    $('srch-fl-status').textContent = total
      ? `${total} aircraft`
      : 'No results.';

    const sightingCards = sightingSorted.map(s => {
      const flag = (s.airline_icao || s.airline) ? _srchLogoWithFallback(s.airline_icao || '', s.airline, 20, '') : '';
      const badge = s.manufacturer ? mfrBadge(s.manufacturer) : '';
      const lastDt = s.last_seen_ts ? new Date(s.last_seen_ts * 1000).toLocaleDateString(undefined, { day:'numeric', month:'short', year:'numeric' }) : '—';
      const airlineTxt = s.airline
        ? `<span style="font-size:12px;color:var(--dim)">${esc(s.airline)}${s.aircraft_type ? `<span style="margin:0 4px;opacity:.4">·</span>${esc(s.aircraft_type)}` : ''}</span>`
        : '';
      const sLastSeenPill = _srchLastSeenPill(s.last_seen_ts, lastDt);
      return `<div class="srch-fl-card">
        <div class="srch-fl-header">
          <span class="srch-fl-rego"><span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${flag || ''}</span>${esc(s.registration)}</span>
          ${badge}
          ${airlineTxt}
          <span style="flex:1"></span>
          ${sLastSeenPill}
        </div>
      </div>`;
    });

    const allCards = regs.map(g => {
      const { airline: airlineName, acType } = g.flights[0] ? _parseDetail(g.flights[0].detail || '') : {};
      const airlineIcao = g.flights[0] ? (g.flights[0].airline_icao || '') : '';
      const flag = (airlineIcao || airlineName)
        ? _srchLogoWithFallback(airlineIcao, airlineName || '', 20, '')
        : '';
      const badge = g.manufacturer ? mfrBadge(g.manufacturer) : '';
      const lastSeenDt = g.last_seen_ts ? new Date(g.last_seen_ts * 1000).toLocaleDateString(undefined, { day:'numeric', month:'short', year:'numeric' }) : null;
      const lastSeenPill = lastSeenDt ? _srchLastSeenPill(g.last_seen_ts, lastSeenDt) : '';
      const nowTs = Math.floor(Date.now() / 1000);
      const pastFlights = g.flights.filter(f => f.arrival_ts && f.arrival_ts <= nowTs);
      const chips = g.flights[0] ? (g.flights[0].notif_types || []).map(t =>
        `<span class="chip ${chipClass(t)}" style="font-size:9px;height:16px;padding:0 4px">${chipLabel(t)}</span>`).join('') : '';
      const rows = pastFlights.map(f => {
        const arrDt = new Date(f.arrival_ts * 1000);
        const dateStr = arrDt.toLocaleDateString(undefined, { day:'numeric', month:'short', year:'numeric' });
        const originName = window.innerWidth < 768
          ? (f.origin_iata || f.origin_name || '—')
          : (f.origin_name || f.origin_iata || '—');
        const originCc = f.origin_country_code || _airportCountry(f.origin_iata || '');
        const originFlag = originCc ? _flag(originCc, { h: 11 }) : '';
        return `<div class="srch-fl-row">
          <span class="srch-fl-date">${esc(dateStr)}</span>
          <span class="srch-fl-fn">${esc(f.flight_number)}</span>
          <span class="srch-fl-route" style="display:inline-flex;align-items:center;gap:5px;overflow:hidden"><span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em;flex-shrink:0">From</span>${originFlag}<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(originName)}</span></span>
        </div>`;
      }).join('') || `<div style="font-size:11px;color:var(--dim);padding:6px 0">No arrivals in the past 30 days</div>`;
      const note = `<div style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 0">Arrivals · past 30 days</div>`;
      return `<div class="srch-fl-card">
        <div class="srch-fl-header">
          <span class="srch-fl-rego"><span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${flag || ''}</span>${esc(g.reg)}</span>
          ${badge}
          ${airlineName ? `<span style="font-size:12px;color:var(--dim)">${esc(airlineName)}${acType ? `<span style="margin:0 4px;opacity:.4">·</span>${esc(acType)}` : ''}</span>` : ''}
          ${chips}
          <span style="flex:1"></span>
          ${lastSeenPill}
        </div>
        <div class="srch-fl-rows">${note}${rows}</div>
      </div>`;
    });
    // Merge matched + sighting-only, sorted by last_seen_ts desc
    _srchFlData = [
      ...regs.map((g, i) => ({
        ts: g.last_seen_ts || 0, html: allCards[i],
        mfr: g.manufacturer || '', airline: airlineNameOf(g), type: acTypeOf(g),
      })),
      ...sightingSorted.map((s, i) => ({
        ts: s.last_seen_ts || 0, html: sightingCards[i],
        mfr: s.manufacturer || '', airline: s.airline || '', type: s.aircraft_type || '',
      })),
    ].sort((a, b) => b.ts - a.ts);

    _srchFlFilter();
  } catch (e) {
    console.error('[srch-fl]', e);
    $('srch-fl-status').textContent = 'Search failed: ' + e.message;
  }
}

// ── Boot ─────────────────────────────────────────────────────────────────────

function _syncRecScrollHeight() {
  const el = document.getElementById('tab-recommendation');
  if (el && !el.classList.contains('hidden')) {
    document.documentElement.style.setProperty('--rec-avail-h', el.clientHeight + 'px');
  }
  const vvh = window.visualViewport ? window.visualViewport.height : window.innerHeight;
  document.documentElement.style.setProperty('--app-vvh', vvh + 'px');
  ['col-subtab-summary', 'col-subtab-fleet'].forEach(id => {
    const page = document.getElementById(id);
    if (page && !page.classList.contains('hidden')) {
      document.documentElement.style.setProperty('--col-avail-h', page.clientHeight + 'px');
    }
  });
}
_syncRecScrollHeight();
window.addEventListener('resize', _syncRecScrollHeight);
if (window.visualViewport) window.visualViewport.addEventListener('resize', _syncRecScrollHeight);
_srchSyncClearVisibility();

// Search tab: once results are shown, collapse the filter fields down to just the Clear button (mobile only)
['srch-fl-results', 'srch-rt-results', 'srch-results'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  const page = el.closest('.srch-page');
  if (!page) return;
  const sync = () => page.classList.toggle('srch-has-results', el.textContent.trim().length > 0);
  new MutationObserver(sync).observe(el, { childList: true });
  sync();
});

fetch('/static/sw.js').then(r => r.text()).then(t => {
  const m = t.match(/spotalert-v(\d+)/);
  const el = document.getElementById('dbg-ver');
  if (m && el) el.textContent = 'v' + m[1];
}).catch(() => {});

setupPWA();
loadTab('history');
pollStatus();
setInterval(pollStatus, 30_000);
$('detail-modal').addEventListener('click', e => {
  if (!e.target.closest('.detail-sheet')) closeDetail();
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') { closeDetail(); collapseGridDetail(); }
});
document.addEventListener('click', e => {
  if (!_gridDetailEl) return;
  if (!e.target.closest('.gd-inner') && !e.target.closest('.sq')) collapseGridDetail();
});
