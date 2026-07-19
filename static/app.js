/* SpotAlert PWA — vanilla JS, no build tooling */
'use strict';

// ── i18n (front-end display only — never touches the backend, per-device via
// localStorage, no per-user server sync). Covers: (1) static navigation/
// settings chrome via data-i18n / data-i18n-placeholder / data-i18n-html
// attributes in index.html, (2) the SETTINGS_SCHEMA-driven settings rows
// (label/desc — see SETTINGS_I18N_ZH below, keyed by setting key), and
// (3) fixed, frontend-owned vocabulary that happens to be triggered by
// backend/API data but whose DISPLAY STRINGS are entirely our own — flight
// status words (Scheduled/Arrived/Cancelled/...), notif-type chip labels,
// and Open-Meteo weather-code descriptions (see tLabel/tChip/tWx below).
// (4) airline/airport names, via tExternalName() below — these are free text
// from the FR24 API / local catalog, translated on demand through the Baidu
// Translation API (backend: POST /api/translate-names) and cached server-side
// in static/translations/names_zh.json (hand-editable), not via a fixed
// dictionary here.
// Deliberately NOT translated (yet — same mechanism as (4) can extend to
// these later): manufacturer names, country names. Also out of scope
// entirely: city names, livery names, registrations, log output, and the
// Feed's backend-generated day-header labels (date + relative-day text
// combined server-side). ────────────────────────────────────────────────────
const I18N = {
  en: {}, // English is the literal DOM text itself — no lookups needed.
  zh: {
    'nav.spotting': '拍机',
    'nav.collection': '相册',
    'nav.feed': '动态',
    'nav.search': '搜索',
    'nav.settings': '设置',
    'settings.tab.general': '常规',
    'settings.tab.spotting': '拍机',
    'settings.tab.feed': '动态',
    'settings.tab.filters': '筛选',
    'settings.tab.collection': '收藏',
    'settings.tab.notification': '通知',
    'settings.tab.logs': '日志',
    'settings.language.heading': '语言',
    'settings.language.key': '显示语言',
    'settings.language.desc': '更改的是您账户的应用界面文本，会同步到您的所有设备，不影响航班数据。',
    'settings.site.heading': '站点设置',
    'settings.site.defaultLang.key': '新访客默认语言',
    'settings.site.defaultLang.desc': '尚未登录或尚未设置自己语言偏好的访客（例如登录页面）看到的语言。',
    'settings.site.allowSelfReg.key': '允许申请账号',
    'settings.site.allowSelfReg.desc': '在登录页面显示"申请账号"链接。每个申请仍需您在用户管理中审核并批准或拒绝。',
    'settings.general.heading': '常规',
    'settings.general.airport.key': '监控机场',
    'settings.general.airport.desc': '机场 IATA 代码。如需更改，请创建新实例。',
    'settings.general.tz.key': '时区',
    'settings.general.tz.desc': '与监控机场的位置绑定 — 无法单独设置。',
    'settings.api.heading': 'API',
    'settings.api.logostream.key': 'Logostream API 密钥',
    'settings.api.logostream.desc': '用于获取航空公司尾翼标志的 API 密钥。缺少此项将无法加载标志图片。',
    'settings.api.baidu.appid.key': '百度翻译 App ID',
    'settings.api.baidu.appid.desc': '用于将航空公司/机场名称翻译为中文。缺少此项将保持英文显示。',
    'settings.api.baidu.secret.key': '百度翻译密钥',
    'settings.api.baidu.secret.desc': '与上方 App ID 配对的密钥。',
    'settings.disclaimer.heading': '免责声明',
    'settings.serverstatus.heading': '服务器状态',
    'settings.scheduledtasks.heading': '计划任务',

    'picker.airports': '机场', 'picker.manageUsers': '用户管理',
    'picker.chooseAirport': '选择机场',
    'picker.addAirport': '添加机场', 'picker.reorderAirports': '排序机场',
    'picker.showDeleteButtons': '显示删除按钮',
    'picker.addAirportPlaceholder': '机场 IATA 代码，例如 MEL',
    'picker.add': '添加',
    'picker.manageUsersTitle': '用户管理',
    'picker.addUser': '添加用户',
    'picker.pendingRequests': '待审核的账号申请', 'picker.approveRequest': '批准并创建账号',
    'picker.username': '用户名', 'picker.passwordMin8': '密码（至少 8 位）',
    'picker.role.pilot': '飞行员', 'picker.role.passenger': '乘客', 'picker.role.controller': '空管',
    'picker.createUser': '创建用户',
    'picker.newPasswordOptional': '新密码（留空则保持不变）',
    'picker.saveChanges': '保存更改',
    'picker.changePassword': '修改密码', 'picker.logout': '退出登录',
    'picker.newPassword': '新密码', 'picker.confirmNewPassword': '确认新密码',
    'picker.min8Chars': '至少 8 个字符',
    'picker.updatePassword': '更新密码',
    'login.signIn': '登录', 'login.password': '密码',
    'login.requestAccount': '申请账号', 'login.requestNote': '给管理员的备注（可选）',
    'login.submitRequest': '提交申请',
    'login.requestSubmitted': '申请已提交——请等待管理员审核。',

    'col.summary': '概览', 'col.fleet': '机队',
    'col.noCatalog': '请在设置 → 拍机推荐 中上传目录以使用此功能。',
    'col.photos': '照片', 'col.aircraft': '飞机', 'col.airlines': '航空公司',
    'col.airports': '机场', 'col.sessions': '拍摄场次',
    'col.topAirlines': '常见航空公司', 'col.topAirports': '常见机场', 'col.topTypes': '常见机型',
    'col.hoppers': '多机场穿梭', 'col.mostPhotos': '最多照片', 'col.mostSessions': '最多场次',
    'col.loading': '加载中…',

    'srch.tab.flights': '上次抵港', 'srch.tab.route': '航线机型', 'srch.tab.catalog': '目录',
    'srch.field.registration': '注册号', 'srch.field.registration.placeholder': '例如 VH-OGB, ZK-NNA',
    'srch.field.manufacturer': '制造商', 'srch.field.airline': '航空公司', 'srch.field.type': '机型',
    'srch.field.flightnumber': '航班号', 'srch.field.flightnumber.placeholder': '例如 QF1, NZ101',
    'srch.field.arrivalfrom': '到达自', 'srch.field.departto': '出发至',
    'srch.field.airport': '机场', 'srch.field.keyword': '关键词',
    'srch.clear': '清除',
    'srch.search': '搜索',

    'filters.exclusion.heading': '排除列表',
    'filters.rego.heading': '注册号关注列表',
    'filters.type.heading': '机型关注列表',
    'filters.airline.heading': '航空公司 / 运营商关注列表',
    'filters.livery.heading': '特殊涂装',
    'filters.rare.heading': '稀有机型',
    'filters.add': '添加',
    'filters.airline.optAirline': '航空公司',
    'filters.airline.optOperator': '运营商',
    'filters.ph.registration': '注册号',
    'filters.ph.description': '描述',
    'filters.ph.airlineIcao': '航空公司 ICAO 代码',
    'filters.ph.acType': '机型代码',
    'filters.ph.icao': 'ICAO 代码',

    'config.polling.heading': '轮询',
    'config.departure.heading': '起飞预测',
    'config.cancel.heading': '取消 / 备降',
    'config.military.heading': '军机',
    'config.spotrec.heading': '拍机推荐',

    'collection.customAirports.heading': '自定义机场',
    'collection.customAirports.desc': '添加不在 FR24 数据库中的机场。用户输入的条目始终优先。',
    'collection.customTypes.heading': '自定义机型',
    'collection.customTypes.descPre': '完整名称来源于',
    'collection.customTypes.descPost': '，每 3 个月自动更新一次。用户输入的条目始终优先。',
    'collection.myCatalog.heading': '我的图库',
    'collection.myCatalog.desc': '您自己的私人 Lightroom 图库 — 用于计算您的收藏统计数据，并确定拍机标签页“已拍摄次数上限”筛选所需的已拍摄机型。绝不会与其他用户共享。',
    'collection.upload': '上传',
    'collection.remove': '移除',
    'collection.statKeywords.heading': '收藏统计关键词',
    'collection.statKeywords.desc': '选择 3 个 Lightroom 关键词，用于在收藏仪表盘中显示唯一注册号数量。',
    'collection.sessionTags.heading': '拍摄记录面板标签',
    'collection.sessionTags.desc': '选择哪些 Lightroom 关键词会在拍摄记录展开面板中显示。全选（或全不选）则显示全部。',
    'collection.photosPath.heading': '拍摄照片路径',
    'collection.photosPath.desc': '容器内挂载您拍机照片文件夹的路径，用于收藏/动态/搜索/机队标签页的照片预览功能。您需要在 docker-compose.yml 中自行将照片文件夹挂载到此确切路径 — 此设置仅告知应用去哪里查找，并不会创建挂载。',
    'collection.openToLoad': '打开此标签页以加载…',
    'collection.ph.airportCode': '机场代码',
    'collection.ph.airportName': '机场名称',
    'collection.ph.countryCode': '国家代码',
    'collection.ph.acTypeCode': '机型代码',
    'collection.ph.fullName': '完整名称',

    'notif.push.heading': '推送通知',
    'notif.push.desc': '当 Feed 中新增卡片时，在此设备上接收通知。',
    'notif.push.enableBtn': '启用通知',
    'notif.filters.heading': '筛选类型',
    'notif.spotrem.heading': '拍机提醒',
    'notif.spotrem.desc': '在您选择的时间推送明天的拍机窗口提醒。',
    'notif.spotrem.enable': '启用',
    'notif.spotrem.sendTime.key': '发送时间',
    'notif.spotrem.sendTime.desc': '如果明天符合条件，提醒将在机场当地时间此时发送。',
    'notif.spotrem.weatherGate.key': '天气过滤',
    'notif.spotrem.weatherGate.desc': '根据明天的天气预报决定是否发送提醒。',
    'notif.spotrem.gate.none': '不检查',
    'notif.spotrem.gate.ignoreSevere': '忽略恶劣天气',
    'notif.spotrem.gate.sunnyOnly': '仅晴天',
    'notif.spotrem.minAircraft.key': '最少飞机数量',
    'notif.spotrem.minAircraft.desc': '如果明天的窗口内飞机数量少于此值，则跳过提醒。',

    'logs.heading': '应用日志',
    'logs.refresh': '刷新',
    'logs.download': '下载',

    'disclaimer.fr24': `
      <strong>FlightRadar24 数据</strong> — 本项目使用 FlightRadar24 的非官方 API。
      FlightRadar24 的<a href="https://www.flightradar24.com/terms-and-conditions" target="_blank">服务条款</a>
      规定其数据<strong>仅限个人非商业用途</strong>使用。
      未取得 FlightRadar24 正式数据授权前，请勿将本项目用于任何商业用途。
    `,
    'disclaimer.adsbfi': `
      <strong>adsb.fi 开放数据</strong> — 军机数据来源于
      <a href="https://opendata.adsb.fi" target="_blank">opendata.adsb.fi</a>。
      该数据<strong>仅限个人非商业用途</strong>使用。
      完整使用条款请参见 <a href="https://adsb.fi" target="_blank">adsb.fi</a>。
    `,
  },
};

// SETTINGS_SCHEMA-driven rows (see that array + _settingRow) — keyed by the
// setting's own `key` string since those are already unique and stable.
const SETTINGS_I18N_ZH = {
  CHECK_INTERVAL_MINUTES:      { label: '检查频率', desc: '轮询 FR24 获取新到达航班的频率。数值越低响应越快，但 API 负载越高。' },
  FETCH_PAGES:                 { label: '抓取页数', desc: '每页约覆盖 100 个最近到达航班。若繁忙机场在列表末尾漏掉航班，可增加此值。' },
  DEPARTURE_PATTERN_THRESHOLD: { label: '起飞预测置信度', desc: '显示预测起飞时间所需的最低历史置信度。80% 表示该规律在 5 次中至少 4 次成立。' },
  MONITOR_CANCEL_GRACE_MINS:   { label: '未起飞宽限时间', desc: '对于预定但从未起飞的航班，超过其预计到达时间多久后才判定为取消。' },
  MONITOR_DIVERTED_GRACE_MINS: { label: '失联降落宽限时间', desc: '对于已在空中追踪但信号中断的航班，超过预计到达时间多久后才判定为备降。' },
  MONITOR_ABSENCE_CHECKS:      { label: '连续缺失次数', desc: '航班需连续多少次检查都未出现在任何 FR24 页面，才会被判定为可能取消/备降。' },
  MONITOR_CONFIRM_CALL_CAP:    { label: '确认调用上限', desc: '每次检查中，用于确认疑似取消/备降的最大 FR24 查询次数 — 防止触发限流的保护措施。' },
  SPECIAL_LIVERY_KEYWORDS:         { label: '关键词', desc: '若航空公司名称包含以下任一词语（不区分大小写）则视为匹配，例如 "retro"、"special"。' },
  SPECIAL_LIVERY_EXCLUDE_KEYWORDS: { label: '排除关键词', desc: '若航空公司名称包含以下任一词语，则不予匹配 — 用于屏蔽常规涂装。' },
  RARE_PLANE_MIN_ABSENCE_DAYS: { label: '最短未出现天数', desc: '仅当某机型至少这么多天未在本机场出现时，才视为"稀有"。' },
  SESSION_PHOTOS_PATH: { label: '挂载路径', desc: '照片文件夹在容器内的挂载路径（见 docker-compose.yml）。默认为 /app/photos。' },
  MILITARY_CHECK_INTERVAL_MINUTES: { label: '检查频率', desc: '查询 adsb.fi 获取机场附近军机动态的频率。' },
  MILITARY_RADIUS_NM:              { label: '探测半径', desc: '仅考虑此半径范围内的军机。数值越小误报越少。' },
  MILITARY_MAX_ALT_FT:             { label: '最高高度', desc: '忽略高空过境飞机 — 仅提醒可能适合拍摄的低空活动。' },
  MILITARY_RENOTIFY_HOURS:         { label: '重复提醒冷却时间', desc: '某军机注册号提醒后，此时长内不再重复提醒。' },
  SPOT_MAX_GAP_HOURS:    { label: '拍机窗口间隔', desc: '航班之间的间隔超过此时长，将开启新的拍机窗口而非并入当前窗口。' },
  SPOT_LULL_MINS:        { label: '空闲时段长度', desc: '拍机窗口内超过此时长的空闲时段会被特别标出，方便您安排休息。' },
  SPOT_MAX_LULLS:        { label: '显示空闲时段数量', desc: '每个拍机窗口最多列出的空闲时段数量，保持推荐内容简洁易读。' },
  SPOT_LIGHTING_GATE:    { label: '避开不佳光线', desc: '开启后，与日出、日落或正午强光时段重叠的拍机窗口将被跳过。' },
  SPOT_MAX_SPOTTED:      { label: '已拍摄次数上限', desc: '某机型在本机场已拍摄达到此次数后，停止推荐。0 表示始终包含。' },
  SPOT_LIGHT_BUFFER_MINS:{ label: '日出/日落缓冲时间', desc: '日出/日落前后被视为不佳光线的分钟数 — 此时飞机为顺光但角度较为刺眼。' },
  SPOT_BAD_LIGHT_START:  { label: '正午强光时段开始', desc: '正午强光时段的开始时间。此时段内飞机看起来会显得平淡、褪色。' },
  SPOT_BAD_LIGHT_END:    { label: '正午强光时段结束', desc: '正午强光时段的结束时间。留空则完全关闭正午强光检测。' },
};

// Flight/departure status words — backend-driven (current_status, arr_label,
// dep_label) but from a small, fixed vocabulary this frontend itself
// defines the display text for (see _STATUS_STYLE / the _pill() COLORS map
// in _renderRouteBar). "Reassigned to X" (aircraft swap) is deliberately
// excluded — it embeds a live registration, not fixed vocabulary.
const _LABEL_ZH = {
  'Scheduled': '计划', 'Arriving': '抵达中', 'Arrived': '已到达', 'Departed': '已起飞',
  'Cancelled': '取消', 'Diverted': '备降', 'Swapped': '换机', 'N/A': '无',
  'Estimated': '预计', 'Predicted': '预测',
  'Confirmed Cancelled': '确认取消', 'Confirmed Diverted': '确认备降',
  'Presumed Cancelled': '推测取消', 'Presumed Diverted': '推测备降',
};
function tLabel(s) {
  if (_lang !== 'zh' || !s) return s;
  return _LABEL_ZH[s] || s;
}

// Notif-type chip labels (see chipLabel()) — the underlying notif_type
// string is backend-defined, but this display abbreviation is our own.
const _CHIP_ZH = {
  'Livery': '涂装', 'Rare': '稀有', 'Rego': '注册号', 'Type': '机型',
  'Airline': '航司', 'Operator': '运营商', 'Military': '军机',
};
function tChip(s) {
  if (_lang !== 'zh' || !s) return s;
  return _CHIP_ZH[s] || s;
}

// Account role names (Controller/Pilot/Passenger) shown as uppercase pills in
// the header and airport picker "Logged in as" rows.
const _ROLE_ZH = { controller: '空管', pilot: '飞行员', passenger: '乘客' };
function tRole(role) {
  role = role || '';
  if (_lang === 'zh') return _ROLE_ZH[role.toLowerCase()] || role;
  return role.toUpperCase();
}

// Collection Stat Keywords — a free-text setting in principle, but the
// picker dropdown the Controller actually chooses from (see index.html's
// collection-stat-kw select) only ever offers this fixed set. Machine
// translation via the Baidu API produced poor results for these short,
// context-dependent aviation-photography terms, so these are hand-picked
// instead — deliberately trading "works for any keyword" for correctness on
// the keywords that are actually selectable. If the picker's option list
// ever grows, this dictionary needs a matching new entry (unmapped keywords
// harmlessly fall back to English via the `|| s` below).
const _COL_KW_ZH = {
  'Cargo': '货运', 'Drone': '无人机', 'Featured': '精选', 'Helicopters': '直升机',
  'Historical': '历史', 'Military': '军机', 'Police': '警用',
  'Private Planes': '私人飞机', 'Special Livery': '特殊涂装',
};
function tColKeyword(s) {
  if (_lang !== 'zh' || !s) return s;
  return _COL_KW_ZH[s] || s;
}

// Open-Meteo weather-code descriptions (see _WX_CODES) — the numeric code
// comes from the API, but these description strings are authored entirely
// by this frontend.
const _WX_ZH = {
  'Clear':'晴朗','Mainly clear':'大部晴朗','Partly cloudy':'局部多云','Overcast':'阴天',
  'Fog':'雾','Icy fog':'冻雾','Light drizzle':'小毛毛雨','Drizzle':'毛毛雨','Heavy drizzle':'大毛毛雨',
  'Light rain':'小雨','Rain':'中雨','Heavy rain':'大雨',
  'Light snow':'小雪','Snow':'中雪','Heavy snow':'大雪',
  'Light showers':'小阵雨','Showers':'阵雨','Heavy showers':'强阵雨',
  'Snow showers':'阵雪','Heavy snow showers':'强阵雪',
  'Thunderstorm':'雷暴','Thunderstorm+hail':'雷暴伴冰雹','Heavy thunderstorm+hail':'强雷暴伴冰雹',
};
function tWx(s) {
  if (_lang !== 'zh' || !s) return s;
  return _WX_ZH[s] || s;
}

// "Today" / "Yesterday" / "N days ago" — a fixed template this frontend
// itself renders from a plain day-count number (Collection dashboard's
// last-session stat), not backend-sourced text.
function tDaysAgo(n) {
  if (_lang !== 'zh') return n === 0 ? 'Today' : n === 1 ? 'Yesterday' : `${n} days ago`;
  return n === 0 ? '今天' : n === 1 ? '昨天' : `${n} 天前`;
}

// Small, scattered, one-off UI strings this frontend hardcodes directly into
// JS template literals (route-bar labels, Spotting timeline axis labels,
// detail-panel field labels, disqualification-reason chips, etc.) — same
// "fixed vocabulary we author ourselves" scope as tLabel/tChip/tWx, just too
// numerous/one-off each to justify their own named helper. Looked up by the
// literal English string itself rather than a semantic key.
const UI_ZH = {
  'Arrivals': '到达', 'Departures': '离开',
  'Sunrise': '日出', 'Sunset': '日落',
  'Before Sunrise': '日出前', 'After Sunset': '日落后', 'Harsh Light': '顶光',
  'Now': '当前时间',
  'Arr. From': '到达自', 'At': '位于', 'Next Dep.': '下一班起飞',
  'Last Visit': '上次到访', 'Spotted': '已拍摄', 'Last Spotted': '上次拍摄', 'Last Seen': '上次出现', 'Never': '从未',
  'First visit': '首次到访',
  'From': '来自',
  'No arrivals in the past 30 days': '过去 30 天内无到达记录',
  'Arrivals · past 30 days': '到达记录 · 过去 30 天',
  'No window': '无拍机窗口',
  'Window': '窗口', 'Alt': '备选', 'No results.': '无结果。',
  'REMARKS': '备注', 'LIVERY': '涂装',
  'today': '今天', 'yesterday': '昨天',
  'Detected': '检测于',
  'Last Session': '上次拍摄场次', 'Last session': '上次拍摄场次',
  'Not set': '未设置',
  'Top Airlines': '常见航空公司', 'Top Airports': '常见机场', 'Top Aircraft Types': '常见机型',
  'Sessions': '拍摄场次',
  'Updated': '更新于',
  'All Manufacturer': '全部制造商', 'All Manufacturers': '全部制造商',
  'All Airline': '全部航空公司', 'All Airlines': '全部航空公司',
  'All Type': '全部机型', 'All Types': '全部机型',
  'All Origins': '全部出发地', 'All Destinations': '全部目的地',
  'All Airports': '全部机场', 'All Keywords': '全部关键词',
  'Enter a registration or select a filter.': '请输入注册号或选择筛选条件。',
  'Enter a flight number or select a filter.': '请输入航班号或选择筛选条件。',
  'Searching…': '搜索中…',
  'Enter a registration': '请输入注册号',
  'Loading filters…': '加载筛选条件中…',
  'Failed to load catalogue filters.': '加载目录筛选条件失败。',
  'Search failed.': '搜索失败。',
  'Equipment History': '机型使用记录',
  'Logged in as:': '登录身份：',
  'All airports': '所有机场', 'No airports assigned': '未分配机场',
  'Invalid username or password': '用户名或密码错误',
  'No airports assigned to your account yet.': '您的账户尚未分配任何机场。',
  'Passwords do not match': '两次输入的密码不一致',
  'Loading…': '加载中…',
  'No photos found.': '未找到照片。',
  'Failed to load photos.': '照片加载失败。',
  'Edit': '编辑', 'Delete': '删除', 'Approve': '批准', 'Decline': '拒绝',
  'No users yet.': '暂无用户。',
  'Could not load users.': '无法加载用户列表。',
  'Username is required': '用户名不能为空',
  'Username and a password of at least 8 characters are required': '需要用户名以及至少 8 位的密码',
  'New password must be at least 8 characters': '新密码至少需要 8 个字符',
  'Refresh Feed': '刷新动态', 'Refresh Collection': '刷新收藏', 'Refresh Spotting': '刷新拍机',
  'Restart Server': '重启服务器',
  'Add': '添加', 'Add keyword…': '添加关键词…',
  'No catalog uploaded yet.': '尚未上传目录。', 'Uploaded:': '已上传：',
  'Failed to load catalog status.': '加载目录状态失败。',
  'No tags found in catalog': '目录中未找到标签',
  'Upload a Lightroom catalog in Settings → Collection to use Fleet tracking.': '请在设置 → 收藏中上传 Lightroom 目录以使用机队追踪功能。',
  'Push notifications are not supported in this browser.': '此浏览器不支持推送通知。',
  'Notifications are enabled on this device.': '此设备已启用通知。',
  'Notifications are off on this device.': '此设备的通知已关闭。',
  'Could not check notification status.': '无法检查通知状态。',
  'Could not load preferences.': '无法加载偏好设置。',
  'Enable Notifications': '启用通知', 'Disable Notifications': '关闭通知',
  'Track a Fleet': '追踪机队',
  'Enter IATA or ICAO airline code': '输入 IATA 或 ICAO 航空公司代码',
  'Search': '搜索', 'Cancel': '取消',
  'Add Airline': '添加航空公司', 'Add airline': '添加航空公司',
  'Registration Watchlist': '注册号关注列表', 'Aircraft Type Watchlist': '机型关注列表',
  'Airline Watchlist': '航空公司关注列表', 'Rare Plane/Airline': '稀有机型/航空公司',
  'Military Aircraft': '军用飞机', 'Special Livery': '特殊涂装',
  'Status': '状态', 'Current Time': '当前时间', 'Server Name': '服务器名称',
  'Operating System': '操作系统', 'Architecture': '架构', 'Connection': '连接方式',
  'Runtime': '运行时长', 'Running': '运行中',
  'Failed to load': '加载失败', 'Unreachable': '无法连接',
  'Task': '任务', 'Last Run': '上次运行', 'Next Run': '下次运行',
  'API': '接口', 'Last Call': '上次调用', 'Next': '下次',
};

// Scheduled-task/API names + descriptions shown in the Server Status card
// (Settings → System) — the underlying strings come from web.py's
// /api/system-tasks response and are authored entirely by this app, so a
// fixed translation table (same reasoning as _COL_KW_ZH) is appropriate.
const _SYS_NAME_ZH = {
  'Airport Scan': '机场扫描', 'Military Scan': '军机扫描', 'Flight Cleanup': '航班记录清理',
  'Collection Stats': '收藏统计', 'DB Backup': '数据库备份', 'Fleet Update': '机队更新',
  'ICAO List Update': 'ICAO 列表更新',
  'FR24 Airport Feed': 'FR24 机场数据', 'Open-Meteo': 'Open-Meteo', 'adsb.fi Military': 'adsb.fi 军机数据',
  'ICAOList (GitHub)': 'ICAOList (GitHub)', 'Logostream': 'Logostream',
};
const _SYS_DESC_ZH = {
  'FR24 airport feed → filter matching → store flights + clusters': 'FR24 机场数据 → 筛选匹配 → 存储航班与聚类',
  'adsb.fi query for military traffic near airport': '通过 adsb.fi 查询机场附近的军机动态',
  'Prune flight records older than 30 days': '清理超过 30 天的航班记录',
  'Lightroom catalog stats cache refresh': '刷新 Lightroom 目录统计缓存',
  'SQLite database backup to disk': '将 SQLite 数据库备份到磁盘',
  'Refresh all fleet card data from FR24': '从 FR24 刷新所有机队卡片数据',
  'Refresh aircraft type database': '刷新机型数据库',
  'Arrivals/departures board (positive + negative pages)': '到达/离开航班板（正向 + 反向分页）',
  'Weather + sunrise/sunset for timeline clusters': '天气及日出日落数据（用于时间线聚类）',
  'Military aircraft positions near airport': '机场附近的军机位置',
  'Aircraft type database (90-day refresh)': '机型数据库（每 90 天刷新）',
  'Airline tail logos (on demand, disk-cached)': '航空公司尾翼徽标（按需获取，磁盘缓存）',
};
function tSysName(s) { return (_lang === 'zh' && s) ? (_SYS_NAME_ZH[s] || s) : s; }
function tSysDesc(s) { return (_lang === 'zh' && s) ? (_SYS_DESC_ZH[s] || s) : s; }
function tt(s) {
  if (_lang !== 'zh' || !s) return s;
  return UI_ZH[s] || s;
}
// "Low Light (N min)" / "Spotted N×" — fixed templates around a number.
function tLowLight(mins) { return _lang === 'zh' ? `弱光（${mins} 分钟）` : `Low Light (${mins} min)`; }
function tSpottedN(n)    { return _lang === 'zh' ? `已拍摄 ${n} 次` : `Spotted ${n}×`; }
function tMinsEarlier(n) { return _lang === 'zh' ? `提早 ${n} 分钟` : `${n}m earlier`; }
function tMinsShorter(n) { return _lang === 'zh' ? `缩短 ${n} 分钟` : `${n}m shorter`; }
function tAircraftN(n)   { return _lang === 'zh' ? `${n} 架飞机` : `${n} aircraft`; }
function tAircraftPhotos(n, photos) {
  const p = (photos || 0).toLocaleString();
  return _lang === 'zh' ? `${n} 架飞机 · ${p} 张照片` : `${n} aircraft · ${p} photos`;
}
function tPhotosN(n) { return _lang === 'zh' ? `${n} 张照片` : `${n} photo${n !== 1 ? 's' : ''}`; }
// Search Catalogue's per-session date field is a plain "YYYY-MM-DD" string
// (not a unix ts like _srchShortDate expects) — same "YY/MM/DD for zh"
// convention as the rest of the app's short-date displays.
function _srchCatDate(dateStr) {
  if (!dateStr) return dateStr;
  if (_lang !== 'zh') return dateStr;
  const [y, m, d] = dateStr.split('-');
  return y ? `${y.slice(2)}/${m}/${d}` : dateStr;
}
// Livery/sticker names are arbitrary FR24 text (like airline/airport names —
// deliberately NOT translated wholesale), but for Chinese, keep the name
// itself as-is and swap just the trailing "Livery"/"Sticker(s)" word for its
// Chinese equivalent — e.g. "Harry Potter Livery" -> "Harry Potter 涂装".
// A few names recur constantly across airlines/airports (alliance liveries,
// generic "Retro Livery" repaints) — common enough that the user asked for
// these specific full names to be hardcoded outright, unlike arbitrary
// one-off livery names.
const _LIVERY_FULL_ZH = {
  'skyteam': '天合联盟涂装',
  'skyteam livery': '天合联盟涂装',
  'star alliance': '星空联盟涂装',
  'star alliance livery': '星空联盟涂装',
  'retro livery': '复古涂装',
  'retro': '复古涂装',
  'oneworld': '寰宇一家涂装',
  'oneworld livery': '寰宇一家涂装',
  'one world': '寰宇一家涂装',
  'one world livery': '寰宇一家涂装',
};
function tLiveryName(name) {
  if (_lang !== 'zh' || !name) return name;
  const full = _LIVERY_FULL_ZH[name.trim().toLowerCase()];
  if (full) return full;
  // Bilingual FR24 livery names embed a Chinese translation in their own
  // parenthetical, e.g. "Cultural Jining (文化济宁)" — with the UI already in
  // Chinese, showing the English name alongside its own translation is
  // redundant. Show just the Chinese portion with the usual 涂装 suffix (the
  // livery/sticker distinction the plain-English branch below makes isn't
  // recoverable here — that word lives outside the parenthetical this text
  // was extracted from — so this always uses the more general term).
  const cjkMatch = name.match(/\(([^)]*[一-鿿][^)]*)\)/);
  if (cjkMatch) return `${cjkMatch[1].trim()} 涂装`;
  const m = name.match(/^(.*?)\s*(liveries|livery|stickers?)\s*$/i);
  if (!m) return name;
  const base = m[1].trim();
  if (!base) return name;
  const suffix = /^liver/i.test(m[2]) ? '涂装' : '贴纸';
  return `${base} ${suffix}`;
}
// "Break · 2hr 11min" — Spotting timeline's quiet-period badge.
function tBreak(durH, durM) {
  if (_lang !== 'zh') return `Break · ${durH > 0 ? `${durH}hr${durM > 0 ? ` ${durM}min` : ''}` : `${durM}min`}`;
  const dur = durH > 0 ? `${durH}小时${durM > 0 ? `${durM}分钟` : ''}` : `${durM}分钟`;
  return `空闲 · ${dur}`;
}

// Feed/Spotting day-section headers (day.label) are built server-side,
// mixing a formatted date with a relative-day suffix ("Mon, 12 Jul" / "Today"
// / "Sat, 11 Jul – Yesterday") — backend text, not this frontend's own fixed
// vocabulary, so it's out of scope for tt()/I18N. But every day object also
// carries the raw ISO `date` string alongside `label`, and this frontend
// already knows "today" (via _appTz, the airport's own timezone — the same
// one the backend used to compute its label, never the viewing device's own
// tz) — so for Chinese, rebuild the label entirely client-side from `date`
// and ignore the backend's English string, rather than trying to translate
// it. English keeps using the backend's label unchanged (zero behavior
// change). No backend edit needed.
const _WEEKDAY_ZH = ['周日','周一','周二','周三','周四','周五','周六'];
function _zhDateLabel(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return `${m}月${d}日 ${_WEEKDAY_ZH[wd]}`;
}
// Full year-month-day form (no weekday) for Collection's session dates —
// "2026年5月30日" instead of the backend's English "30 May 2026" label.
// Needs the raw ISO date string (not the pre-formatted label) as input.
function _zhFullDate(dateStr) {
  const [y, m, d] = dateStr.split('-').map(Number);
  return `${y}年${m}月${d}日`;
}
// item must carry both a raw ISO `date` field and a pre-formatted English
// `date_label` (see web.py's /api/catalog-stats) — falls back to date_label
// whenever the raw date is missing/unparseable, same safety net as the rest
// of this file's backend-label-vs-client-reformat helpers.
function _colDateLabel(item) {
  if (_lang === 'zh' && item && item.date) {
    try { return _zhFullDate(item.date); } catch (e) { /* fall through */ }
  }
  return item ? item.date_label : '';
}
function _todayStrInAppTz() {
  try { return new Intl.DateTimeFormat('en-CA', { timeZone: _appTz || undefined }).format(new Date()); }
  catch (e) { return new Intl.DateTimeFormat('en-CA').format(new Date()); }
}
function _diffDaysFromToday(dateStr) {
  const todayStr = _todayStrInAppTz();
  return Math.round((Date.parse(todayStr + 'T00:00:00Z') - Date.parse(dateStr + 'T00:00:00Z')) / 86400000);
}
// Feed: matches web.py's /api/feed — Tomorrow/Today/Yesterday show with NO
// date at all; every other day shows just the date, never combined.
function tFeedDayLabel(dateStr, fallback) {
  if (_lang !== 'zh' || !dateStr) return fallback;
  const diff = _diffDaysFromToday(dateStr);
  if (diff === -1) return '明天';
  if (diff === 0) return '今天';
  if (diff === 1) return '昨天';
  return _zhDateLabel(dateStr);
}
// Spotting: matches web.py's /api/recommendation — date always shows, with
// Tomorrow/Today/Yesterday appended when applicable.
function tRecDayLabel(dateStr, fallback) {
  if (_lang !== 'zh' || !dateStr) return fallback;
  const diff = _diffDaysFromToday(dateStr);
  const base = _zhDateLabel(dateStr);
  if (diff === -1) return `${base} · 明天`;
  if (diff === 0) return `${base} · 今天`;
  if (diff === 1) return `${base} · 昨天`;
  return base;
}

let _lang = localStorage.getItem('spotalert-lang') || 'en';

// ── Airline/airport name translation (Baidu, server-cached — see backend
// POST /api/translate-names and static/translations/names_zh.json). Unlike
// the fixed-vocabulary t*() helpers above, this is a live network lookup, so
// it must never block a render: callers show English immediately via
// tExternalName()'s empty-cache fallback, then _translateNamesForZh() swaps
// in Chinese text once the batch resolves (see call sites' data-ext-name
// patching). ─────────────────────────────────────────────────────────────
// Keyed by lowercased name — the same airline/airport shows up in different
// casing across data sources (FR24 payloads, Lightroom catalogs, user-entered
// filters, etc.), and without normalizing the key here a casing mismatch would
// look like a cache miss even though the server already has the translation.
let _nameTranslationCache = {};
function _extNameKey(s) { return (s || '').toLowerCase(); }

function tExternalName(name) {
  if (_lang !== 'zh' || !name) return name;
  return _nameTranslationCache[_extNameKey(name)] || name;
}

async function _translateNamesForZh(names) {
  if (_lang !== 'zh') return;
  const need = [...new Set(names.filter(n => n && !_nameTranslationCache[_extNameKey(n)]))];
  if (need.length) {
    try {
      const data = await api('/translate-names', { method: 'POST', body: JSON.stringify({ names: need }) });
      for (const [k, v] of Object.entries(data.translations || {})) {
        _nameTranslationCache[_extNameKey(k)] = v;
      }
    } catch (e) { /* graceful fallback to English — no user-facing error */ }
  }
  // Always patch, even when nothing new needed fetching — this loop is the
  // ONLY thing that ever swaps translated text into a `data-ext-name`
  // element's DOM node (route-bar spans render their synchronous fallback
  // text directly, e.g. the raw English `origin_city` on mobile — see
  // _renderRouteBar — never _routeAirportDisp's own already-cached-aware
  // lookup). A card opened AFTER its airport name was already cached by an
  // earlier _translateNamesForZh() call hits the `need.length === 0` case
  // every time, so returning early here used to skip this loop entirely for
  // that card's freshly-created elements — they'd stay on their English
  // fallback forever, even though the translation was sitting right there in
  // _nameTranslationCache the whole time.
  document.querySelectorAll('[data-ext-name]').forEach(el => {
    const orig = el.dataset.extName;
    const val = _nameTranslationCache[_extNameKey(orig)];
    if (val) {
      el.textContent = el.dataset.extCity ? _cityNameZh(val) : val;
    }
  });
}

// Resolves key against the current language, falling back to origEnglish
// (the literal text/placeholder already in the DOM at page load) rather
// than the raw key string — I18N.en is deliberately empty (English IS the
// DOM's own text), so a naive "translation || key" fallback would render
// the untranslated key itself instead of the original English.
function _i18nResolve(key, origEnglish) {
  return (_lang !== 'en' && I18N[_lang] && I18N[_lang][key]) ? I18N[_lang][key] : origEnglish;
}

function applyI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    if (el.dataset.i18nOrig === undefined) el.dataset.i18nOrig = el.textContent;
    el.textContent = _i18nResolve(el.dataset.i18n, el.dataset.i18nOrig);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    if (el.dataset.i18nPlaceholderOrig === undefined) el.dataset.i18nPlaceholderOrig = el.placeholder;
    el.placeholder = _i18nResolve(el.dataset.i18nPlaceholder, el.dataset.i18nPlaceholderOrig);
  });
  document.querySelectorAll('[data-i18n-html]').forEach(el => {
    if (el.dataset.i18nHtmlOrig === undefined) el.dataset.i18nHtmlOrig = el.innerHTML;
    el.innerHTML = _i18nResolve(el.dataset.i18nHtml, el.dataset.i18nHtmlOrig);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    if (el.dataset.i18nTitleOrig === undefined) el.dataset.i18nTitleOrig = el.title;
    el.title = _i18nResolve(el.dataset.i18nTitle, el.dataset.i18nTitleOrig);
  });
  const sel = $('lang-select');
  if (sel) sel.value = _lang;
}

function setLanguage(lang) {
  _lang = (lang === 'zh') ? 'zh' : 'en';
  localStorage.setItem('spotalert-lang', _lang);
  // Per-user, server-synced (see _authBoot's early-apply block for the read
  // side) so switching language on one device carries over to this same
  // user's other devices/sessions — localStorage above is just this
  // session's own immediate cache. Guarded on _appRole for the same reason
  // as loadSettings() below (never fires for the unauthenticated boot-time
  // applyI18n() call); fire-and-forget since a failed sync just means the
  // next device falls back to its own local preference, same as before this
  // feature existed.
  if (_appRole) {
    api('/me/language', { method: 'PUT', body: JSON.stringify({ language: _lang }) }).catch(() => {});
  }
  applyI18n();
  // Re-render SETTINGS_SCHEMA-driven rows (label/desc) in the new language —
  // those are JS-templated, not static DOM text, so applyI18n() alone can't
  // reach them the way data-i18n does. Guarded on _appRole (only set once
  // _authBoot has actually resolved a logged-in session) so this never fires
  // — and never shows loadSettings' own "Failed to load settings" error
  // toast — for the unauthenticated boot-time applyI18n() call.
  if (_appRole && typeof loadSettings === 'function') loadSettings();
  // Airline/airport names are rendered from live API data (Feed cards, Spotting
  // windows, Collection stats, Search results), not static DOM text, so
  // applyI18n() alone never reaches them — without re-fetching, a user who
  // switches language after a tab has already loaded would keep seeing
  // whatever language it was in until a hard refresh. Re-run whichever tab is
  // currently active (same guard as loadSettings() above — never fires before
  // a real session exists). tExternalName()/_airportDisplayName() branch on
  // _lang at render time and _nameTranslationCache is shared across languages
  // (only consulted when _lang === 'zh'), so re-rendering with the new _lang
  // is enough — no cache reset needed, and switching to Chinese fires the
  // usual _translateNamesForZh() batch lookup for anything not already cached.
  // The language dropdown itself lives on the Settings tab, so activeTab is
  // 'settings' at the moment this runs for essentially every real user
  // interaction — branching on "is Collection/Spotting/Search the active tab
  // right now" would almost never fire. Instead, invalidate every other
  // tab's loaded-flag unconditionally so its normal loadTab()-on-switch path
  // (see switchTab()) re-fetches with the new language the next time the
  // user visits it — plus, defensively, refresh the active tab immediately
  // in the rare case it's already one of these (e.g. a future UI change
  // moves the language control, or this is called programmatically).
  if (_appRole) {
    _recLoaded = false;
    _colLoaded = false;
    _srchFlData = null;
    if (activeTab === 'history') loadFeed();
    else if (activeTab === 'recommendation') loadRecommendation(false);
    else if (activeTab === 'collection') loadCollection(false);
    else if (activeTab === 'search') {
      _srchFlRun(true);
      if (typeof _srchRtRun === 'function') _srchRtRun(true);
      if (typeof _srchRun === 'function') _srchRun(true);
    }
  }
}

// ── Utilities ────────────────────────────────────────────────────────────────

// Fixed, closed set of canonical manufacturer names mfrBadge() ever produces
// (see the if/else chain below) — small enough that Baidu translates them
// reliably (unlike the short, context-dependent Collection Stat Keywords,
// where quality was poor and hardcoding won instead). Prefetched once so
// every badge render across the app (Feed, Collection, Search, etc.) shares
// one batch lookup instead of firing a request per card.
const _MFR_CANONICAL_NAMES = [
  'Boeing', 'Airbus', 'Embraer', 'Bombardier', 'De Havilland', 'McDonnell Douglas',
  'Lockheed Martin', 'Cessna', 'Gulfstream', 'Dassault', 'ATR', 'Saab', 'Fokker',
  'Comac', 'Antonov', 'Sukhoi', 'Pilatus', 'Sikorsky', 'Bell', 'Leonardo', 'BAE Systems',
];
let _mfrPrefetched = false;
function _prefetchMfrNames() {
  if (_mfrPrefetched) return;
  _mfrPrefetched = true;
  _translateNamesForZh(_MFR_CANONICAL_NAMES);
}
// _colMfrBadge/_colRenderRows/etc. pass the RAW catalog manufacturer string
// (e.g. "British Aerospace"), not mfrBadge()'s canonicalized form ("BAE
// Systems") — those two don't always match, so the fixed-list prefetch above
// alone misses them. Fire a per-name lookup too; _translateNamesForZh()
// already no-ops for anything already cached/in-flight, so this stays cheap.
function _mfrDisp(name) {
  _prefetchMfrNames();
  if (name) _translateNamesForZh([name]);
  return tExternalName(name);
}
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
  return `<span class="mfr mfr-${cls}" data-ext-name="${esc(canonical)}">${esc(_mfrDisp(canonical))}</span>`;
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

// ── Auth gate: login screen + airport picker, decided client-side after the
// shell loads (never a server-side redirect — the service worker precaches '/'
// cache-first, so a redirect there could be silently bypassed by a stale cache). ──

function _showAuthView(id) {
  ['view-login', 'view-airport-picker'].forEach(v => $(v).classList.toggle('hidden', v !== id));
  if (id === 'view-login') {
    fetch('/api/version').then(r => r.json()).then(d => {
      $('login-version').textContent = d.version ? `v${d.version}` : '';
    }).catch(() => {});
    $('request-account-link').classList.toggle('hidden', !_allowSelfRegistration);
    $('request-account-form').classList.add('hidden');
  }
}
function _hideAuthViews() {
  ['view-login', 'view-airport-picker'].forEach(v => $(v).classList.add('hidden'));
}

// Set once at boot (_authBoot) — Controller sees everything; Pilot loses the
// operational Settings subtabs but keeps Collection/Fleet/Catalogue; Passenger
// additionally loses the Collection tab and Search's Catalogue subtab. Kept as
// a plain global (not re-derived per render) since role never changes mid-session.
let _appRole = '';
const PILOT_EDITABLE_GROUPS = new Set(['spotrec', 'livery', 'rare']);
// Individual keys hidden entirely from a Pilot's or Passenger's view, even
// within an otherwise pilot-editable group — these stay Controller-only/
// inherited, unlike the rest of the 'livery' group (e.g. Exclude Keywords IS
// independently settable per Pilot, and visible read-only to a Passenger).
const CONTROLLER_ONLY_SETTINGS = new Set(['SPECIAL_LIVERY_KEYWORDS']);

function _applyRoleUI(role) {
  _appRole = role;
  const isController = role === 'controller';
  const isPassenger  = role === 'passenger';
  const isPilot      = role === 'pilot';

  // Controller-identity/global-config cards a Pilot has no business seeing or
  // editing (API key, custom airport/aircraft-type reference data) — hidden
  // entirely, not just disabled, even though the subtabs hosting them (General,
  // Collection) otherwise stay visible to Pilots. The API key is also hidden
  // from Passengers (read-only viewers have no business seeing the raw
  // credential either; the server also redacts it from GET /api/settings).
  const apiCard = document.querySelector('.page-card-api');
  if (apiCard) apiCard.classList.toggle('hidden', isPilot || isPassenger);
  ['settings-card-custom-airports', 'settings-card-custom-aircraft-types'].forEach(id => {
    const el = $(id);
    if (el) el.classList.toggle('hidden', isPilot);
  });
  // Session-photo preview is a Controller-only feature (see _spOpenPhotos'
  // _appRole check) — Pilots/Passengers have no use for its mount-path
  // config and shouldn't see a container filesystem path at all.
  const photosPathCard = $('settings-card-session-photos-path');
  if (photosPathCard) photosPathCard.classList.toggle('hidden', !isController);
  // Site-wide config (default language for anonymous visitors, whether the
  // sign-in screen offers "Request an account") — Controller-only.
  const siteCard = $('settings-card-site');
  if (siteCard) {
    siteCard.classList.toggle('hidden', !isController);
    if (isController) loadSiteSettings();
  }

  // Operational/monitoring-internals Settings subtabs — Controller-only,
  // hidden entirely (not just disabled) for Pilot and Passenger alike.
  // Notification is NOT in this list — push notifications are a universal
  // per-user feature (every role gets their own independent subscription
  // and preferences, never shared with any other user's).
  ['config', 'logs'].forEach(name => {
    const btn = document.querySelector(`.srch-subtab[data-subtab="${name}"]`);
    if (btn) btn.classList.toggle('hidden', !isController);
  });

  // The Settings "Collection" subtab (Custom Airports/Aircraft Types, My
  // Catalog, Collection Stat Keywords, Session Panel Tags) is read-only/
  // catalog-backed throughout — hidden entirely for Passengers, unlike Pilots
  // who still use several of its cards.
  const airportsSubtabBtn = document.querySelector('.srch-subtab[data-subtab="airports"]');
  if (airportsSubtabBtn) airportsSubtabBtn.classList.toggle('hidden', isPassenger);

  // The Filters subtab's 4 "add new" rows render fully interactive today with
  // no role gating — Passengers can view the (read-only, Controller-inherited)
  // lists but must not see inputs suggesting they could add to them.
  document.querySelectorAll('.filter-add-row').forEach(el => el.classList.toggle('hidden', isPassenger));

  // Passenger loses the Collection tab and Search's Catalogue subtab entirely —
  // both are catalog-backed views a read-only viewer has no use for.
  const collectionTab = document.querySelector('.nav-tab[data-tab="collection"]');
  if (collectionTab) collectionTab.classList.toggle('hidden', isPassenger);
  const catalogueSubtab = document.querySelector('.srch-subtab[data-srch-subtab="catalog"]');
  if (catalogueSubtab) catalogueSubtab.classList.toggle('hidden', isPassenger);

  // Passengers have no catalog concept at all — the "My Catalog" upload
  // card only makes sense for Controller/Pilot accounts.
  const myCatalogBlock = $('my-catalog-block');
  if (myCatalogBlock) myCatalogBlock.classList.toggle('hidden', isPassenger);

  // Pilots don't see Custom Airports/Custom Aircraft Types (Controller-only),
  // which would otherwise leave the Collection subtab's left column empty.
  // Physically relocate Collection Stat Keywords + Session Panel Tags into
  // that now-empty column for Pilots only; Controller/Passenger keep the
  // original 2-card/3-card split since their left column isn't empty.
  const col1 = $('airports-subtab-col1');
  const kwCard = $('settings-card-collection-stat-keywords');
  const tagsCard = $('settings-card-session-panel-tags');
  if (col1 && kwCard && tagsCard) {
    if (isPilot) {
      col1.appendChild(kwCard);
      col1.appendChild(tagsCard);
    } else {
      const col2 = $('airports-subtab-col2');
      if (col2) {
        col2.appendChild(kwCard);
        col2.appendChild(tagsCard);
      }
    }
  }

  _syncRefreshBtnVisibility();
}

async function loadSiteSettings() {
  try {
    const s = await api('/controller/site-settings');
    $('site-default-lang').value = s.default_language || 'en';
    $('site-allow-self-reg').checked = !!s.allow_self_registration;
  } catch (e) {}
}

async function _saveSiteSettings() {
  try {
    await api('/controller/site-settings', {
      method: 'PUT',
      body: JSON.stringify({
        default_language: $('site-default-lang').value,
        allow_self_registration: $('site-allow-self-reg').checked,
      }),
    });
    toast('Saved');
  } catch (e) {
    toast('Could not save site settings');
    loadSiteSettings();
  }
}

// Only "Refresh Collection" and "Refresh Spotting" hit endpoints Pilot/Passenger
// can actually call — every other tab's button action (Refresh Feed / force-check,
// Restart Server) is Controller-only server-side. Allowlist rather than blocklist
// so a future tab added to TABS defaults to hidden-for-non-controller, not
// accidentally exposed. Called both on every tab switch and once at boot (the
// default tab shown before any switchTab() call is 'history' — the Feed tab).
const _NON_CONTROLLER_SAFE_REFRESH_TABS = new Set(['collection', 'recommendation']);
function _syncRefreshBtnVisibility() {
  const btn = $('btn-refresh');
  if (!btn) return;
  btn.classList.toggle('hidden', !_NON_CONTROLLER_SAFE_REFRESH_TABS.has(activeTab) && _appRole !== 'controller');
}

let _allowSelfRegistration = false;

async function _authBoot() {
  let me;
  try { me = await api('/me'); } catch { me = { authenticated: false }; }
  _allowSelfRegistration = !!me.allow_self_registration;
  if (!me.authenticated) {
    // Anonymous visitor — no per-user language on file, so fall back to
    // the site's configured default (Controller-set, see Site Settings)
    // unless this device already has its own explicit choice saved.
    if (!localStorage.getItem('spotalert-lang') && me.site_default_language && me.site_default_language !== _lang) {
      _lang = me.site_default_language;
      applyI18n();
    }
    _showAuthView('view-login');
    return;
  }
  // Per-user language, synced server-side (see setLanguage()) — apply it before
  // anything auth-gated renders (including the airport picker below) so there's
  // no flash of whatever language localStorage/this device happened to have.
  if (me.user.language && me.user.language !== _lang) {
    _lang = me.user.language;
    localStorage.setItem('spotalert-lang', _lang);
    applyI18n();
  } else if (!me.user.language && _lang !== 'en') {
    // The server has no language on file but this device's localStorage does
    // (set by setLanguage() below) — the PUT that was supposed to persist it
    // must have failed silently at the time (offline, expired session, etc;
    // that call is fire-and-forget with no retry). Push notifications read
    // the server-side value directly, so a permanently-unsynced preference
    // here means the UI looks fully translated while push text never
    // matches — resync now, every boot, until it actually lands.
    api('/me/language', { method: 'PUT', body: JSON.stringify({ language: _lang }) }).catch(() => {});
  }
  if (!me.airport) {
    _renderAirportPicker(me.airports || [], me.user.role, me.user.username);
    _showAuthView('view-airport-picker');
    return;
  }
  // Resolve the airport's timezone before anything else renders — fmtTs/fmtDate
  // (Feed cards, detail views, etc.) read _appTz as their default, and must
  // never fall back to the viewing device's own timezone.
  try { _appTz = (await api('/status')).effective_tz || ''; } catch {}
  const _role = me.user.role || '';
  // Desktop-only (this whole header-whoami block is already display:none under
  // 767px via CSS) — shows which airport this session is currently scoped to,
  // ahead of the existing "Logged in as" block, since a Controller/Pilot with
  // multiple watched airports can otherwise lose track of which one they're on.
  const _curAirport = (me.airports || []).find(a => a.iata === me.airport);
  const _airportHtml = _curAirport ? `
    <span class="header-whoami-airport">${_flag(_curAirport.country_code, { h: 14 })}${esc(_curAirport.iata)}</span>
    <span class="header-whoami-divider"></span>
  ` : '';
  $('header-whoami').innerHTML = me.user.username ? `
    ${_airportHtml}
    <span class="header-whoami-user">${tt('Logged in as:')} ${esc(me.user.username)}</span>
    <span class="picker-role-pill role-${esc(_role)}">${esc(tRole(_role))}</span>
  ` : '';
  _applyRoleUI(me.user.role);
  _hideAuthViews();
  _handleDeepLinkFlight();
  _handleDeepLinkSpotting();
}

// Tapping a push notification (see static/sw.js's notificationclick handler)
// navigates here with ?flight=<registration> — open that flight's Feed card
// with its detail panel showing, instead of just landing on a generic Feed
// view. Called once per page load, after auth/airport selection are confirmed
// (the deep link is meaningless before that — the notification navigation
// always reaches this same shell regardless of session state).
async function _handleDeepLinkFlight() {
  const params = new URLSearchParams(location.search);
  const reg = params.get('flight');
  if (!reg) return;
  // Strip the query param immediately so a later refresh/back-navigation
  // doesn't keep re-opening the same detail panel.
  history.replaceState(null, '', location.pathname);
  switchTab('history');
  await loadFeed();
  const cards = document.querySelectorAll('#history-list .sq[data-r]');
  let target = null;
  for (const el of cards) {
    try {
      const r = JSON.parse(el.dataset.r);
      if ((r.registration || '').toUpperCase() === reg.toUpperCase()) { target = el; break; }
    } catch {}
  }
  if (target) {
    target.scrollIntoView({ behavior: 'smooth', block: 'center' });
    openDetail(target);
  } else {
    toast(`Could not find ${reg} in the last 30 days of Feed`);
  }
}

// Tapping a spotting-reminder push (see static/sw.js's notificationclick
// handler) navigates here with ?spotting=tomorrow — open the Spotting tab
// scrolled to tomorrow's card, instead of landing on today's by default.
async function _handleDeepLinkSpotting() {
  const params = new URLSearchParams(location.search);
  if (params.get('spotting') !== 'tomorrow') return;
  history.replaceState(null, '', location.pathname);
  switchTab('recommendation');
  await loadRecommendation(false);
  // loadRecommendation() schedules its own requestAnimationFrame to center
  // today's card (see its "Initial position" block) — that rAF is already
  // queued by the time this await resolves, so scrolling to tomorrow's card
  // synchronously here would just get clobbered a frame later. Queuing a
  // second rAF (registered after theirs, so it runs after theirs in the same
  // batch) lets our scroll win instead.
  requestAnimationFrame(() => {
    const card = document.querySelector('#recommendation-content .rec-day.rec-tomorrow');
    if (card) card.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
  });
}

async function doLogin() {
  const username = $('login-username').value.trim();
  const password = $('login-password').value;
  const errEl = $('login-error');
  errEl.classList.add('hidden');
  try {
    const res = await api('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) });
    // Always land on the airport picker after a fresh login, even if this
    // browser has a remembered airport selection from a previous session.
    document.cookie = 'sa_airport=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/';
    await _promptPushOnLogin();
    location.reload();
  } catch (e) {
    errEl.textContent = tt('Invalid username or password');
    errEl.classList.remove('hidden');
  }
}

async function doLogout() {
  try { await api('/auth/logout', { method: 'POST' }); } catch {}
  location.reload();
}

function _toggleRequestAccountForm() {
  const form = $('request-account-form');
  form.classList.toggle('hidden');
  $('ra-error').classList.add('hidden');
  $('ra-success').classList.add('hidden');
}

async function requestAccount() {
  const username = $('ra-username').value.trim();
  const password = $('ra-password').value;
  const note = $('ra-note').value.trim();
  const errEl = $('ra-error');
  errEl.classList.add('hidden');
  $('ra-success').classList.add('hidden');
  if (!username || password.length < 8) {
    errEl.textContent = tt('Username and a password of at least 8 characters are required');
    errEl.classList.remove('hidden');
    return;
  }
  try {
    await api('/auth/request-account', { method: 'POST', body: JSON.stringify({ username, password, note }) });
    $('ra-username').value = '';
    $('ra-password').value = '';
    $('ra-note').value = '';
    $('ra-success').classList.remove('hidden');
  } catch (e) {
    let msg = 'Could not submit request';
    try { msg = JSON.parse(e.message).detail || msg; } catch {}
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }
}

let _pickerAirports = [];  // cached for the Add User form's airport checkboxes
let _pickerIsController = false;
let _pickerClockInterval = null;
let _pickerReorderMode = false;
let _pickerShowDelete = false;
const _PICKER_ORDER_KEY = 'spotalert_airport_order';

function _pickerAirportTime(tz, tzAbbr) {
  if (!tz) return '';
  try {
    // 12-hour clock ticks client-side every 30s; the abbreviation itself
    // (AEST, JST, CEST, ...) is resolved server-side via Python's zoneinfo
    // and passed in — the browser's own Intl 'short' timeZoneName falls back
    // to a bare GMT+offset for most non-US zones, so it can't be trusted here.
    const time = new Intl.DateTimeFormat(_locale(), {
      hour: 'numeric', minute: '2-digit', hour12: _lang !== 'zh', timeZone: tz,
    }).format(new Date());
    return tzAbbr ? `${time} ${tzAbbr}` : time;
  } catch (e) { return ''; }
}

function _tickPickerClocks() {
  document.querySelectorAll('.airport-pick-time[data-tz]').forEach(el => {
    el.textContent = _pickerAirportTime(el.dataset.tz, el.dataset.tzAbbr);
  });
}

// Same trim rules as web.py's _short_airport_name (Search route-filters) —
// drops the noisy "International"/"Airport(s)" suffix so names fit on one
// line in the fixed-size picker card instead of wrapping and stretching it.
function _shortAirportName(name) {
  return (name || '')
    .replace(/\s*\bInternational\b/gi, '')
    .replace(/\s*\bAirports?\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
}

// For Chinese, translate the FULL name (the cache is keyed by the exact
// name as it originates from FR24/the local catalog) rather than shortening
// it — _shortAirportName's suffix-stripping is an English-only affordance
// for fitting fixed-width cards; Baidu's Chinese output is already short
// (e.g. "奥克兰机场") and stripping English words like "International" from
// it would do nothing useful. English keeps the shortened form as before.
function _airportDisplayName(name) {
  if (_lang === 'zh' && _nameTranslationCache[_extNameKey(name)]) return _cityNameZh(_nameTranslationCache[_extNameKey(name)]);
  return _shortAirportName(name);
}

// Deterministic hash-to-hue so every airport in the same country always gets
// the same pill color, without needing a hand-maintained country->color map.
function _countryPillColors(cc) {
  if (!cc) return { bg: 'var(--surface2)', fg: 'var(--dim)' };
  let hash = 0;
  for (let i = 0; i < cc.length; i++) hash = (hash * 31 + cc.charCodeAt(i)) >>> 0;
  const hue = hash % 360;
  return { bg: `hsl(${hue}, 42%, 22%)`, fg: `hsl(${hue}, 70%, 78%)` };
}

function _pickerLoadOrder() {
  try { return JSON.parse(localStorage.getItem(_PICKER_ORDER_KEY)) || []; } catch { return []; }
}

function _pickerSaveOrder() {
  localStorage.setItem(_PICKER_ORDER_KEY, JSON.stringify(_pickerAirports.map(a => a.iata)));
}

function _pickerApplyOrder(airports) {
  const order = _pickerLoadOrder();
  if (!order.length) return airports;
  const byIata = new Map(airports.map(a => [a.iata, a]));
  const sorted = order.map(iata => byIata.get(iata)).filter(Boolean);
  byIata.forEach((a, iata) => { if (!order.includes(iata)) sorted.push(a); });
  return sorted;
}

function _renderAirportPicker(airports, role, username) {
  _pickerAirports = _pickerApplyOrder(airports || []);
  _pickerIsController = role === 'controller';
  if (!_pickerIsController) { _pickerShowDelete = false; }
  $('picker-whoami').innerHTML = username ? `
    <div class="picker-whoami-user">${tt('Logged in as:')} ${esc(username)}</div>
    <span class="picker-role-pill role-${esc(role || '')}">${esc(tRole(role))}</span>
  ` : '';
  _renderAirportCards();
  _translateNamesForZh(_pickerAirports.map(a => a.name).filter(Boolean));
  if (_pickerClockInterval) clearInterval(_pickerClockInterval);
  _pickerClockInterval = _pickerAirports.length ? setInterval(_tickPickerClocks, 30000) : null;
  // Every role can reorder their own view of the airport cards; only a
  // Controller can add new watched airports, delete one, or manage users.
  $('picker-icon-btns').classList.remove('hidden');
  $('picker-icon-btn-add').classList.toggle('hidden', !_pickerIsController);
  $('picker-icon-btn-delete').classList.toggle('hidden', !_pickerIsController);
  $('add-airport-block').classList.add('hidden');
  $('picker-icon-btn-add').classList.remove('active');
  $('picker-subtabs').classList.toggle('hidden', !_pickerIsController);
  _pickerSubtab('airports');
}

function _renderAirportCards() {
  const el = $('airport-picker-list');
  const airports = _pickerAirports;
  if (!airports.length) {
    el.innerHTML = `<div class="airport-picker-empty">${tt('No airports assigned to your account yet.')}</div>`;
    return;
  }
  // Drag-and-drop reordering doesn't work well with touch (no native HTML5 drag
  // source on most mobile browsers) — mobile gets a simpler tap-one-card-then-
  // another-to-swap interaction instead, gated on viewport width like every other
  // mobile-vs-desktop branch in this codebase. Desktop keeps drag-and-drop.
  const isMobile = window.innerWidth < 768;
  el.innerHTML = airports.map(a => {
    const flag = a.country_code ? _flag(a.country_code, { h: 48 }) : '';
    const delBtn = (_pickerIsController && _pickerShowDelete)
      ? `<button class="airport-pick-del" title="Delete ${esc(a.iata)}" onclick="event.stopPropagation(); deleteAirport(this, '${esc(a.iata)}')">✕</button>`
      : '';
    const shortName = _airportDisplayName(a.name) || a.name || '';
    const { bg: pillBg, fg: pillFg } = _countryPillColors(a.country_code);
    const code = esc(a.iata);
    const useDrag = _pickerReorderMode && !isMobile;
    const draggable = useDrag ? ' draggable="true"' : '';
    const dragAttrs = useDrag
      ? ` ondragstart="_pickerDragStart(event, '${esc(a.iata)}')" ondragover="_pickerDragOver(event)" ondragleave="_pickerDragLeave(event)" ondrop="_pickerDrop(event, '${esc(a.iata)}')"`
      : '';
    const swapSelected = (_pickerReorderMode && isMobile && _pickerSwapIata === a.iata) ? ' swap-selected' : '';
    const clickHandler = _pickerReorderMode
      ? (isMobile ? ` onclick="_pickerTapSwap('${esc(a.iata)}')"` : '')
      : ` onclick="selectAirport('${esc(a.iata)}')"`;
    return `<div class="airport-pick-card${_pickerReorderMode ? ' reorder-mode' : ''}${swapSelected}" data-iata="${code}"${draggable}${dragAttrs}${clickHandler}>
      ${delBtn}
      ${flag}
      <span class="airport-pick-code-pill" style="background:${pillBg};color:${pillFg}">${code}</span>
      <div class="airport-pick-time" data-tz="${esc(a.tz || '')}" data-tz-abbr="${esc(a.tz_abbr || '')}">${esc(_pickerAirportTime(a.tz, a.tz_abbr))}</div>
      <div class="airport-pick-name" title="${esc(a.name || '')}" data-ext-name="${esc(a.name || '')}" data-ext-city="1">${esc(shortName)}</div>
    </div>`;
  }).join('');
}

function _toggleReorderMode() {
  _pickerReorderMode = !_pickerReorderMode;
  if (_pickerReorderMode) _pickerShowDelete = false;
  _pickerSwapIata = null;
  $('picker-icon-btn-reorder').classList.toggle('active', _pickerReorderMode);
  $('picker-icon-btn-delete').classList.remove('active');
  _renderAirportCards();
}

function _toggleDeleteMode() {
  _pickerShowDelete = !_pickerShowDelete;
  if (_pickerShowDelete) _pickerReorderMode = false;
  $('picker-icon-btn-delete').classList.toggle('active', _pickerShowDelete);
  $('picker-icon-btn-reorder').classList.remove('active');
  _renderAirportCards();
}

// Mobile reorder: tap one card, then another, and they swap places — a pure
// two-item swap, unlike drag-and-drop's insert-at-drop-position behavior.
let _pickerSwapIata = null;

function _pickerTapSwap(iata) {
  if (_pickerSwapIata === null) {
    _pickerSwapIata = iata;
    _renderAirportCards();
    return;
  }
  if (_pickerSwapIata === iata) {
    _pickerSwapIata = null;
    _renderAirportCards();
    return;
  }
  const fromIdx = _pickerAirports.findIndex(a => a.iata === _pickerSwapIata);
  const toIdx = _pickerAirports.findIndex(a => a.iata === iata);
  if (fromIdx !== -1 && toIdx !== -1) {
    [_pickerAirports[fromIdx], _pickerAirports[toIdx]] = [_pickerAirports[toIdx], _pickerAirports[fromIdx]];
    _pickerSaveOrder();
  }
  _pickerSwapIata = null;
  _renderAirportCards();
}

let _pickerDragIata = null;

function _pickerDragStart(e, iata) {
  _pickerDragIata = iata;
  e.dataTransfer.effectAllowed = 'move';
}

function _pickerDragOver(e) {
  e.preventDefault();
  e.currentTarget.classList.add('drag-over');
}

function _pickerDragLeave(e) {
  e.currentTarget.classList.remove('drag-over');
}

function _pickerDrop(e, targetIata) {
  e.preventDefault();
  e.currentTarget.classList.remove('drag-over');
  if (!_pickerDragIata || _pickerDragIata === targetIata) return;
  const fromIdx = _pickerAirports.findIndex(a => a.iata === _pickerDragIata);
  const toIdx = _pickerAirports.findIndex(a => a.iata === targetIata);
  if (fromIdx === -1 || toIdx === -1) return;
  const [moved] = _pickerAirports.splice(fromIdx, 1);
  _pickerAirports.splice(toIdx, 0, moved);
  _pickerDragIata = null;
  _pickerSaveOrder();
  _renderAirportCards();
}

function _showAddAirportForm() {
  const nowHidden = $('add-airport-block').classList.toggle('hidden');
  $('picker-icon-btn-add').classList.toggle('active', !nowHidden);
}

async function deleteAirport(btn, iata) {
  _armOrConfirm(btn, 'Delete', 'Click again to permanently delete', async () => {
    try {
      await api(`/controller/airports/${iata}`, { method: 'DELETE' });
      toast(`${iata} deleted`);
      showAirportPicker();
    } catch (e) {
      toast('Error: ' + e.message);
    }
  }, 'armed');
}

function _showChangePasswordForm() {
  $('change-password-block').classList.toggle('hidden');
}

async function changePassword() {
  const next = $('cp-new').value;
  const confirm = $('cp-confirm').value;
  const errEl = $('cp-error');
  errEl.classList.add('hidden');
  if (next !== confirm) {
    errEl.textContent = tt('Passwords do not match');
    errEl.classList.remove('hidden');
    return;
  }
  try {
    await api('/auth/change-password', {
      method: 'POST',
      body: JSON.stringify({ new_password: next }),
    });
    $('cp-new').value = '';
    $('cp-confirm').value = '';
    $('change-password-block').classList.add('hidden');
    toast('Password updated — logging out everywhere');
    // Changing the password bumps session_epoch server-side, which invalidates
    // every existing session cookie for this user (including this one) — log
    // this browser out immediately too rather than leaving a stale UI up until
    // its next request 401s.
    setTimeout(doLogout, 1200);
  } catch (e) {
    errEl.textContent = e.message || 'Could not change password';
    errEl.classList.remove('hidden');
  }
}

function _pickerSubtab(name) {
  document.querySelectorAll('.picker-subtab').forEach(b =>
    b.classList.toggle('active', b.dataset.pickerTab === name));
  $('picker-panel-airports').classList.toggle('hidden', name !== 'airports');
  $('picker-panel-users').classList.toggle('hidden', name !== 'users');
  $('change-password-toggle-btn').classList.toggle('hidden', name === 'users');
  if (name === 'users' && _pickerIsController) { loadUserManagement(); loadAccountRequests(); }
}

async function selectAirport(iata) {
  try {
    await api('/airport/select', { method: 'POST', body: JSON.stringify({ airport_iata: iata }) });
    location.reload();
  } catch (e) {
    toast('Could not select that airport');
  }
}

async function showAirportPicker() {
  let me;
  try { me = await api('/me'); } catch { me = { airports: [], user: {} }; }
  _renderAirportPicker(me.airports || [], (me.user || {}).role, (me.user || {}).username);
  _showAuthView('view-airport-picker');
}

// ── Account requests (Controller only, self-registration review) ───────────

let _accountRequestsCache = [];
let _approvingRequestId = null;

async function loadAccountRequests() {
  const listEl = $('account-requests-list');
  try {
    const { requests } = await api('/controller/account-requests');
    _accountRequestsCache = requests;
    $('account-requests-block').classList.toggle('hidden', requests.length === 0);
    listEl.innerHTML = requests.map(r => `
      <div class="user-mgmt-row">
        <div class="user-mgmt-row-hdr"><span class="user-mgmt-username">${esc(r.username)}</span></div>
        ${r.note ? `<div class="user-mgmt-airports">${esc(r.note)}</div>` : ''}
        <div class="user-mgmt-actions">
          <button onclick="_showApproveRequestForm('${esc(r.id)}')">${tt('Approve')}</button>
          <button class="danger" onclick="_declineRequest(this,'${esc(r.id)}','${esc(r.username)}')">${tt('Decline')}</button>
        </div>
      </div>`).join('');
  } catch (e) {
    $('account-requests-block').classList.add('hidden');
  }
}

function _showApproveRequestForm(id) {
  const r = _accountRequestsCache.find(x => x.id === id);
  if (!r) return;
  _approvingRequestId = id;
  $('approve-request-username').textContent = r.username;
  _renderAirportPillSelect('approve-request-airports', []);
  _setRolePillOptions('approve-request', ['pilot', 'passenger'], 'pilot');
  $('approve-request-error').classList.add('hidden');
  $('approve-request-form').classList.remove('hidden');
}

async function _confirmApproveRequest() {
  if (!_approvingRequestId) return;
  const role = $('approve-request-role').value;
  const airport_iatas = _getSelectedAirportPills('approve-request-airports');
  const errEl = $('approve-request-error');
  errEl.classList.add('hidden');
  try {
    await api(`/controller/account-requests/${_approvingRequestId}/approve`, {
      method: 'POST', body: JSON.stringify({ role, airport_iatas }),
    });
    $('approve-request-form').classList.add('hidden');
    _approvingRequestId = null;
    toast('Account created');
    loadAccountRequests();
    loadUserManagement();
  } catch (e) {
    let msg = 'Could not approve request';
    try { msg = JSON.parse(e.message).detail || msg; } catch {}
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }
}

function _declineRequest(btn, id, username) {
  _armOrConfirm(btn, 'Decline', 'Click again to confirm', async () => {
    try {
      await api(`/controller/account-requests/${id}/decline`, { method: 'POST', body: JSON.stringify({}) });
      toast(`Declined ${username}`);
      loadAccountRequests();
    } catch (e) {
      toast('Could not decline request');
    }
  }, 'armed');
}

// ── Manage Users (Controller only) ──────────────────────────────────────────

let _pickerUsersCache = [];
let _editingUserId = null;

async function loadUserManagement() {
  const listEl = $('user-mgmt-list');
  listEl.innerHTML = `<div class="airport-picker-empty">${tt('Loading…')}</div>`;
  try {
    const { users } = await api('/controller/users');
    _pickerUsersCache = users;
    listEl.innerHTML = users.map(u => {
      const airportsTxt = u.role === 'controller' ? tt('All airports') : (u.airport_iatas.join(', ') || tt('No airports assigned'));
      return `<div class="user-mgmt-row">
        <div class="user-mgmt-row-hdr">
          <span class="user-mgmt-username">${esc(u.username)}</span>
          <span class="picker-role-pill role-${esc(u.role)}">${esc(tRole(u.role))}</span>
        </div>
        <div class="user-mgmt-airports">${esc(airportsTxt)}</div>
        <div class="user-mgmt-actions">
          <button onclick="_showEditUserForm('${esc(u.id)}')">${tt('Edit')}</button>
          <button class="danger" onclick="_deleteUser(this,'${esc(u.id)}','${esc(u.username)}')">${tt('Delete')}</button>
        </div>
      </div>`;
    }).join('') || `<div class="airport-picker-empty">${tt('No users yet.')}</div>`;
  } catch (e) {
    listEl.innerHTML = `<div class="airport-picker-empty">${tt('Could not load users.')}</div>`;
  }
}

// Role transitions: only Pilot/Passenger can ever be created (there is exactly
// one Controller per server); the only allowed upgrade is Passenger -> Pilot,
// no downgrades, and the Controller's own role can never be changed via this
// form (that would leave the server with zero controllers).
function _selectRolePill(prefix, role) {
  const btn = document.querySelector(`#${prefix}-role-group .role-pick-pill[data-role="${role}"]`);
  if (!btn || btn.classList.contains('disabled')) return;
  document.querySelectorAll(`#${prefix}-role-group .role-pick-pill`).forEach(b => b.classList.remove('selected'));
  btn.classList.add('selected');
  $(`${prefix}-role`).value = role;
}

function _setRolePillOptions(prefix, enabledRoles, selectedRole) {
  document.querySelectorAll(`#${prefix}-role-group .role-pick-pill`).forEach(b => {
    const allowed = enabledRoles.includes(b.dataset.role);
    b.classList.toggle('disabled', !allowed);
    b.classList.toggle('selected', b.dataset.role === selectedRole);
  });
  $(`${prefix}-role`).value = selectedRole;
}

function _renderAirportPillSelect(containerId, selectedIatas) {
  $(containerId).innerHTML = _pickerAirports.map(a => {
    const sel = selectedIatas.includes(a.iata);
    return `<button type="button" class="airport-pick-pill${sel ? ' selected' : ''}" data-iata="${esc(a.iata)}" onclick="_toggleAirportPill(this)">${esc(a.iata)}</button>`;
  }).join('');
}

function _toggleAirportPill(btn) {
  btn.classList.toggle('selected');
}

function _getSelectedAirportPills(containerId) {
  return Array.from(document.querySelectorAll(`#${containerId} .airport-pick-pill.selected`)).map(b => b.dataset.iata);
}

function _showAddUserForm() {
  $('edit-user-form').classList.add('hidden');
  _editingUserId = null;
  if (!$('add-user-form').classList.contains('hidden')) {
    $('add-user-form').classList.add('hidden');
    $('picker-icon-btn-add-user').classList.remove('active');
    return;
  }
  _renderAirportPillSelect('add-user-airports', []);
  _setRolePillOptions('add-user', ['pilot', 'passenger'], 'pilot');
  $('add-user-form').classList.remove('hidden');
  $('picker-icon-btn-add-user').classList.add('active');
}

function _showEditUserForm(id) {
  const u = _pickerUsersCache.find(x => x.id === id);
  if (!u) return;
  _editingUserId = id;
  $('add-user-form').classList.add('hidden');
  $('picker-icon-btn-add-user').classList.remove('active');
  $('edit-user-username').value = u.username;
  $('edit-user-password').value = '';
  const enabledRoles = u.role === 'passenger' ? ['passenger', 'pilot'] : [u.role];
  _setRolePillOptions('edit-user', enabledRoles, u.role);
  if (u.role === 'controller') {
    $('edit-user-airports').innerHTML = '<div class="airport-pill-note">All airports</div>';
  } else {
    _renderAirportPillSelect('edit-user-airports', u.airport_iatas);
  }
  $('edit-user-error').classList.add('hidden');
  $('edit-user-form').classList.remove('hidden');
}

async function saveEditUser() {
  if (!_editingUserId) return;
  const username = $('edit-user-username').value.trim();
  const password = $('edit-user-password').value;
  const role = $('edit-user-role').value;
  const airport_iatas = role === 'controller' ? undefined : _getSelectedAirportPills('edit-user-airports');
  const errEl = $('edit-user-error');
  errEl.classList.add('hidden');
  if (!username) {
    errEl.textContent = tt('Username is required');
    errEl.classList.remove('hidden');
    return;
  }
  if (password && password.length < 8) {
    errEl.textContent = tt('New password must be at least 8 characters');
    errEl.classList.remove('hidden');
    return;
  }
  try {
    await api(`/controller/users/${_editingUserId}`, {
      method: 'PUT',
      body: JSON.stringify({ username, role, airport_iatas }),
    });
    if (password) {
      await api(`/controller/users/${_editingUserId}/reset-password`, {
        method: 'POST', body: JSON.stringify({ new_password: password }),
      });
    }
    $('edit-user-form').classList.add('hidden');
    _editingUserId = null;
    toast('User updated');
    loadUserManagement();
  } catch (e) {
    let msg = 'Could not update user';
    try { msg = JSON.parse(e.message).detail || msg; } catch {}
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }
}

async function createUser() {
  const username = $('add-user-username').value.trim();
  const password = $('add-user-password').value;
  const role = $('add-user-role').value;
  const airport_iatas = _getSelectedAirportPills('add-user-airports');
  const errEl = $('add-user-error');
  errEl.classList.add('hidden');
  try {
    await api('/controller/users', { method: 'POST', body: JSON.stringify({ username, password, role, airport_iatas }) });
    $('add-user-username').value = '';
    $('add-user-password').value = '';
    $('add-user-form').classList.add('hidden');
    $('picker-icon-btn-add-user').classList.remove('active');
    toast(`Created ${username}`);
    loadUserManagement();
  } catch (e) {
    let msg = 'Could not create user';
    try { msg = JSON.parse(e.message).detail || msg; } catch {}
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }
}

function _deleteUser(btn, id, username) {
  _armOrConfirm(btn, 'Delete', 'Click again to confirm', async () => {
    try {
      await api(`/controller/users/${id}`, { method: 'DELETE' });
      toast(`Deleted ${username}`);
      loadUserManagement();
    } catch (e) {
      toast('Could not delete user');
    }
  }, 'armed');
}

async function addAirport() {
  const codeEl = $('add-airport-code');
  const errEl = $('add-airport-error');
  const code = codeEl.value.trim();
  errEl.classList.add('hidden');
  if (!code) return;
  try {
    const r = await api('/controller/airports', { method: 'POST', body: JSON.stringify({ airport_code: code }) });
    codeEl.value = '';
    toast(`Added ${r.airport_name} (${r.airport_iata})`);
    showAirportPicker();
  } catch (e) {
    let msg = 'Could not add that airport';
    try { msg = JSON.parse(e.message).detail || msg; } catch {}
    errEl.textContent = msg;
    errEl.classList.remove('hidden');
  }
}

applyI18n();
_authBoot();

// The monitored airport's own IANA timezone — set once at boot (_authBoot) and
// kept fresh by pollStatus(). fmtTs/fmtDate must always format in this
// timezone, never the viewing device's own — a spotter checking the feed from
// somewhere else would otherwise see every time shifted to their own clock.
let _appTz = '';

// fmtTs/fmtDate/_fmtLastSeen previously always passed `undefined` as the
// Intl locale (browser default — NOT tied to the app's own language
// setting), which is why switching to Chinese never affected any of the
// weekday/month/AM-PM text these produce. zh-CN's Intl output for these
// exact option shapes is already correct Chinese convention (e.g. "上午
// 11:21", "7月12日周日") with no further string-munging needed — the
// existing ' AM'/' PM' cleanup below simply doesn't match anything in
// Chinese output, so it stays harmless for both languages.
function _locale() { return _lang === 'zh' ? 'zh-CN' : 'en-US'; }

function fmtTs(ts, opts = {}) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  const s = d.toLocaleString(_locale(), {
    hour: 'numeric', minute: '2-digit', hour12: _lang !== 'zh',
    ...(_appTz ? { timeZone: _appTz } : {}), ...opts,
  });
  return s.replace(' AM', 'am').replace(' PM', 'pm');
}

function fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleDateString(_locale(), {
    weekday: 'short', month: 'short', day: 'numeric',
    ...(_appTz ? { timeZone: _appTz } : {}),
  });
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

// ISO-3166 country name -> alpha-2 code, for military cards where the
// "country" comes from adsb.fi's actual operator metadata (via extra_info),
// not a guessed registration prefix (which is unreliable for military serial
// numbers — e.g. a US N-number military asset isn't a real FAA civil
// registration despite the superficially similar format). This is a static,
// exhaustive reference table, not per-aircraft guessing, so it's reliable.
const _COUNTRY_NAME_TO_CC = {
  'afghanistan':'AF','albania':'AL','algeria':'DZ','andorra':'AD','angola':'AO',
  'argentina':'AR','armenia':'AM','australia':'AU','austria':'AT','azerbaijan':'AZ',
  'bahamas':'BS','bahrain':'BH','bangladesh':'BD','belarus':'BY','belgium':'BE',
  'belize':'BZ','benin':'BJ','bhutan':'BT','bolivia':'BO','bosnia and herzegovina':'BA',
  'botswana':'BW','brazil':'BR','brunei':'BN','bulgaria':'BG','burkina faso':'BF',
  'burundi':'BI','cambodia':'KH','cameroon':'CM','canada':'CA','chad':'TD',
  'chile':'CL','china':'CN','colombia':'CO','congo':'CG',
  'democratic republic of the congo':'CD','costa rica':'CR','croatia':'HR',
  'cuba':'CU','cyprus':'CY','czech republic':'CZ','czechia':'CZ','denmark':'DK',
  'djibouti':'DJ','dominican republic':'DO','ecuador':'EC','egypt':'EG',
  'el salvador':'SV','estonia':'EE','ethiopia':'ET','fiji':'FJ','finland':'FI',
  'france':'FR','gabon':'GA','georgia':'GE','germany':'DE','ghana':'GH',
  'greece':'GR','guatemala':'GT','guinea':'GN','haiti':'HT','honduras':'HN',
  'hong kong':'HK','hungary':'HU','iceland':'IS','india':'IN','indonesia':'ID',
  'iran':'IR','iraq':'IQ','ireland':'IE','israel':'IL','italy':'IT',
  'ivory coast':'CI',"cote d'ivoire":'CI','jamaica':'JM','japan':'JP','jordan':'JO',
  'kazakhstan':'KZ','kenya':'KE','kosovo':'XK','kuwait':'KW','kyrgyzstan':'KG',
  'laos':'LA','latvia':'LV','lebanon':'LB','lesotho':'LS','liberia':'LR',
  'libya':'LY','liechtenstein':'LI','lithuania':'LT','luxembourg':'LU',
  'macau':'MO','macedonia':'MK','north macedonia':'MK','madagascar':'MG',
  'malawi':'MW','malaysia':'MY','maldives':'MV','mali':'ML','malta':'MT',
  'mauritania':'MR','mauritius':'MU','mexico':'MX','moldova':'MD','monaco':'MC',
  'mongolia':'MN','montenegro':'ME','morocco':'MA','mozambique':'MZ',
  'myanmar':'MM','burma':'MM','namibia':'NA','nepal':'NP','netherlands':'NL',
  'new zealand':'NZ','nicaragua':'NI','niger':'NE','nigeria':'NG',
  'north korea':'KP','norway':'NO','oman':'OM','pakistan':'PK','panama':'PA',
  'papua new guinea':'PG','paraguay':'PY','peru':'PE','philippines':'PH',
  'poland':'PL','portugal':'PT','qatar':'QA','romania':'RO','russia':'RU',
  'russian federation':'RU','rwanda':'RW','saudi arabia':'SA','senegal':'SN',
  'serbia':'RS','singapore':'SG','slovakia':'SK','slovenia':'SI',
  'somalia':'SO','south africa':'ZA','south korea':'KR','korea, republic of':'KR',
  'south sudan':'SS','spain':'ES','sri lanka':'LK','sudan':'SD','suriname':'SR',
  'sweden':'SE','switzerland':'CH','syria':'SY','taiwan':'TW','tajikistan':'TJ',
  'tanzania':'TZ','thailand':'TH','togo':'TG','trinidad and tobago':'TT',
  'tunisia':'TN','turkey':'TR','turkiye':'TR','turkmenistan':'TM','uganda':'UG',
  'ukraine':'UA','united arab emirates':'AE','united kingdom':'GB',
  'great britain':'GB','united states':'US','united states of america':'US',
  'usa':'US','uruguay':'UY','uzbekistan':'UZ','venezuela':'VE','vietnam':'VN',
  'yemen':'YE','zambia':'ZM','zimbabwe':'ZW',
};

function _countryNameToCode(name) {
  if (!name) return '';
  const key = name.trim().toLowerCase().replace(/\s*\(.*?\)/g, '');
  return _COUNTRY_NAME_TO_CC[key] || '';
}

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
  };
  return tChip(map[_normType(type)]) || type || '?';
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
      ${airline ? `<div class="sq-airline" data-ext-name="${esc(airline)}">${esc(tExternalName(airline))}</div>` : ''}
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
  const label = d.toLocaleDateString(_locale(), { day: 'numeric', month: 'short', year: 'numeric', ...opts });
  const dateStr  = ts  => new Date(ts * 1000).toLocaleDateString('en-CA', opts); // YYYY-MM-DD
  const seenDay  = dateStr(ts);
  const todayDay = dateStr(Math.floor(Date.now() / 1000));
  const msPerDay = 86400000;
  const daysAgo  = Math.round((new Date(todayDay) - new Date(seenDay)) / msPerDay);
  if (daysAgo === 0) return `${label} (${tt('today')})`;
  if (daysAgo === 1) return `${label} (${tt('yesterday')})`;
  return `${label} (${_lang === 'zh' ? `${daysAgo} 天前` : `${daysAgo} days ago`})`;
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

// Map status strings to our canonical states (handles both FR24 raw and canonical values)
function _normStatus(raw) {
  if (!raw) return null;
  const s = raw.toLowerCase();
  if (s === 'arriving' || s === 'in flight')   return 'Arriving';
  if (s === 'arrived' || s === 'on ground' || s === 'landed') return 'Arrived';
  if (s === 'scheduled')                        return 'Scheduled';
  if (s === 'departed')                         return 'Departed';
  if (s === 'cancelled' || s === 'canceled')     return 'Cancelled';
  if (s === 'diverted')                          return 'Diverted';
  if (s === 'swapped')                           return 'Swapped';
  return null;
}

// Status for a single flight bar
function _barStatus(f, nowTs) {
  if (f.current_status) {
    const norm = _normStatus(f.current_status);
    // Terminal states — resolved by the backend, never re-inferred from timestamps below
    // (in particular, must not fall into the "24h+ old with no dep_ts → Departed" heuristic).
    if (norm === 'Cancelled' || norm === 'Diverted' || norm === 'Swapped') return norm;
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
const _STATUS_PRIORITY = ['Arriving', 'Arrived', 'Cancelled', 'Diverted', 'Swapped', 'Scheduled', 'Departed', 'N/A'];
function _cardStatus(card, nowTs) {
  const statuses = (card.flights || []).map(f => _barStatus(f, nowTs));
  for (const s of _STATUS_PRIORITY) {
    if (s === 'Departed' && statuses.some(x => x !== 'Departed' && x !== 'N/A')) continue;
    if (statuses.includes(s)) return s;
  }
  return 'N/A';
}

// Matches _renderRouteBar's own COLORS map (Feed's detail-panel route-bar
// pills) so Spotting's flight-card pills (_recFlightCard, which reuses this
// same dict) get identical accent colors — was previously out of sync on
// two counts: Departed was flat grey here vs orange in the route bar, and
// Estimated/Predicted (departure-side labels _recFlightCard can also show)
// weren't defined here at all, silently falling back to Scheduled's grey.
const _STATUS_STYLE = {
  Scheduled: ['rgba(120,120,120,0.15)', '#999'],
  Arriving:  ['rgba(245,158,11,0.18)',  '#f59e0b'],
  Arrived:   ['rgba(34,197,94,0.18)',   '#22c55e'],
  Departed:  ['rgba(245,158,11,0.18)',  '#f59e0b'],
  Estimated: ['rgba(59,130,246,0.18)',  '#93c5fd'],
  Predicted: ['rgba(245,158,11,0.18)',  '#f59e0b'],
  Cancelled: ['rgba(239,68,68,0.18)',   '#ef4444'],
  Diverted:  ['rgba(168,85,247,0.18)',  '#a855f7'],
  Swapped:   ['rgba(120,120,120,0.10)', 'var(--dim)'],
  'N/A':     ['rgba(120,120,120,0.08)', 'var(--dim)'],
};

function _statusPillInline(status) {
  if (!status) return '';
  const [bg, fg] = _STATUS_STYLE[status] || _STATUS_STYLE['N/A'];
  return `<span class="sq-card-status" style="color:${fg};background:${bg}">${esc(tLabel(status))}</span>`;
}

// Render a rego card (same .sq thumbnail style, enhanced for multi-flight)
function _cardAirlineName(group) {
  const { airline: _parsedAirline } = _parseDetail(group.detail || '');
  const isMilitary = (group.notif_types || []).includes('Military');
  return isMilitary ? (group.extra_info || '').split(' · ')[0] : _parsedAirline;
}

function regoCard(group) {
  const nowTs  = Math.floor(Date.now() / 1000);
  const photo  = group.photo_url || '';
  const { airline: _parsedAirline, acType } = _parseDetail(group.detail || '');
  const isMilitary = (group.notif_types || []).includes('Military');
  const airline = _cardAirlineName(group);
  const count  = (group.flights || []).length;
  const encoded = esc(JSON.stringify(group));

  const chips = (group.notif_types || []).map(t =>
    `<span class="chip ${chipClass(t)}">${chipLabel(t)}</span>`
  ).join('');

  const airlineLogo = isMilitary
    ? _airforceRoundelImg(airline, 23)
    : _airlineLogoByIcao(group.airline_icao || '', 23, _parsedAirline);
  // Grey out the whole thumbnail only when EVERY flight under this card is resolved away
  // (nothing live/actionable left) — a card with even one normal flight stays full-strength,
  // no per-status pill on the thumbnail itself (that's detail-view-only now).
  const allResolvedAway = (group.flights || []).length > 0 &&
    (group.flights || []).every(f => ['Cancelled', 'Diverted', 'Swapped'].includes(_barStatus(f, nowTs)));
  return `<div class="sq${allResolvedAway ? ' sq-resolved-away' : ''}" onclick="openDetail(this)" data-r="${encoded}">
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
      ${airline ? `<div class="sq-airline" data-ext-name="${esc(airline)}">${esc(tExternalName(airline))}</div>` : ''}
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
      <div class="section-heading">${esc(tFeedDayLabel(day.date, day.label))}</div>
      <div class="fc-grid">${(day.cards || []).map(g => regoCard(g)).join('')}</div>
    `).join('');
    const _names = [
      ...data.days.flatMap(day => (day.cards || []).map(g => _cardAirlineName(g))),
      ...data.days.flatMap(day => (day.cards || []).flatMap(g => (g.flights || []).flatMap(f => [f.origin_name, f.dep_dest_name]))),
    ].filter(Boolean);
    _translateNamesForZh(_names);
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

function _detailQuickFilterBtns(registration) {
  // Exclude / Add-to-watchlist quick actions — Controller/Pilot only (both
  // endpoints are server-side role-gated too; this is just UI, not the
  // enforcement boundary). Passengers never see filter management at all.
  if (_appRole !== 'controller' && _appRole !== 'pilot') return '';
  const reg = esc(registration);
  return `<div style="display:flex;gap:6px;flex-shrink:0">
    <button class="detail-quick-btn detail-quick-exclude" title="Exclude ${reg}"
            onclick="event.stopPropagation(); quickExcludeRego(this, '${reg}')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><line x1="6.5" y1="6.5" x2="17.5" y2="17.5"/></svg>
    </button>
    <button class="detail-quick-btn detail-quick-watchlist" title="Add ${reg} to registration watchlist"
            onclick="event.stopPropagation(); quickWatchlistRego(this, '${reg}')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"/><circle cx="12" cy="12" r="2.6"/></svg>
    </button>
  </div>`;
}

// Click-to-arm, click-again-to-confirm — same UX pattern as armRestartBackend,
// but per-button-instance (dataset-based) since these are dynamically created
// per open detail panel, not a single fixed element.
function _armOrConfirm(btn, confirmedTitle, armedTitle, onConfirm, armedClass = 'detail-quick-armed') {
  if (btn.dataset.armed === '1') {
    clearTimeout(+btn.dataset.armTimer);
    btn.dataset.armed = '0';
    btn.classList.remove(armedClass);
    btn.title = confirmedTitle;
    onConfirm();
    return;
  }
  btn.dataset.armed = '1';
  btn.classList.add(armedClass);
  btn.title = armedTitle;
  const timer = setTimeout(() => {
    btn.dataset.armed = '0';
    btn.classList.remove(armedClass);
    btn.title = confirmedTitle;
  }, 4000);
  btn.dataset.armTimer = String(timer);
}

async function quickExcludeRego(btn, rego) {
  _armOrConfirm(btn, `Exclude ${rego}`, `Click again to confirm exclude`, async () => {
    try {
      await api('/filters/exclusion', { method: 'POST', body: JSON.stringify({ registration: rego, description: '' }) });
      toast(`${rego} excluded`);
      if (typeof loadFilters === 'function') loadFilters().catch(() => {});
    } catch (e) { toast('Error: ' + e.message); }
  });
}

async function quickWatchlistRego(btn, rego) {
  _armOrConfirm(btn, `Add ${rego} to registration watchlist`, `Click again to confirm add to watchlist`, async () => {
    try {
      await api('/filters/rego', { method: 'POST', body: JSON.stringify({ registration: rego, description: '' }) });
      toast(`${rego} added to watchlist`);
      if (typeof loadFilters === 'function') loadFilters().catch(() => {});
    } catch (e) { toast('Error: ' + e.message); }
  });
}

// Shared FR24 deep-link builders — every clickable rego/flight-number/airport/
// airline in the app should point at the same URL shapes, so this is the one
// place that knows FR24's URL conventions.
function _fr24AircraftUrl(rego) { return `https://www.flightradar24.com/data/aircraft/${(rego || '').toLowerCase()}`; }
function _fr24FlightUrl(fn)     { return `https://www.flightradar24.com/data/flights/${(fn || '').toLowerCase().replace(/\s/g, '')}`; }
function _fr24AirportUrl(iata)  { return `https://www.flightradar24.com/airport/${(iata || '').toLowerCase()}`; }
function _fr24AirlineUrl(iata, icao) { return `https://www.flightradar24.com/data/airlines/${(iata || '').toLowerCase()}-${(icao || '').toLowerCase()}`; }

function _detailInner(r, closeCmd, showPhoto = true) {
  const fr24 = _fr24AircraftUrl(r.registration);
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
      return `<span style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:20px;background:${bg};color:${fg}">${esc(tLabel(st))}</span>`;
    };
    const isMilitary = (r.notif_types || []).includes('Military');
    // Arrival/departure status doesn't apply to a military track detection —
    // there's no scheduled flight to be "Arrived"/"Departed" against.
    const cardStatusPill = isMilitary ? '' : `<span id="${statusPillId}">${_statusPillHtml(cardSt)}</span>`;

    // Render each flight as a route bar (same design as single-card detail)
    const flightBars = r.flights.map(f => {
      const depLabel = !f.dep_ts ? null : f.dep_ts > nowTs ? (f.dep_label || 'Scheduled') : 'Departed';

      // Use the computed canonical status so the route bar label matches the status pill
      const computedStatus = _barStatus(f, nowTs);
      const resolvedAway = computedStatus === 'Cancelled' || computedStatus === 'Diverted' || computedStatus === 'Swapped';
      const routeLiveStatus = computedStatus === 'Arriving'  ? 'In Flight'
                            : computedStatus === 'Arrived'   ? 'On Ground'
                            : computedStatus === 'Scheduled' ? 'Scheduled'
                            : computedStatus === 'Departed'  ? 'Departed'
                            : null;

      const fData = {
        airport_iata:        airportIata,
        airport_name:        airportName,
        arr_label:           resolvedAway ? computedStatus : null,
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
        resolved_away:      resolvedAway,
        origin_iata:        f.origin_iata,
        origin_name:        f.origin_name,
        origin_city:        f.origin_city,
        airport_iata:       airportIata,
        airport_name:       airportName,
      };
      return _renderRouteBar(fData, fR);
    }).join('');
    // Newest visit first — r.flights is chronological ascending, the carousel should open on the latest.
    const mapFlights = isMilitary ? [...r.flights].reverse() : [];
    const mapPages = isMilitary ? mapFlights.map((f, i) => {
      const mapId = `${lazyId}-map-${i}`;
      const startLabel = fmtTs(f.arrival_ts, { weekday: 'short', hour: '2-digit', minute: '2-digit' });
      // Finish time = last recorded track point for this visit (when it left the
      // radius / went stationary), not just when it was first detected.
      const track = f.track || [];
      const lastTs = track.length ? track[track.length - 1].ts : null;
      const endLabel = lastTs && lastTs !== f.arrival_ts
        ? fmtTs(lastTs, { hour: '2-digit', minute: '2-digit' })
        : null;
      const mapLabel = endLabel ? `${startLabel} – ${endLabel}` : startLabel;
      return `<div class="mil-map-page">
        <div class="mil-map-label">${tt('Detected')} ${esc(mapLabel)}</div>
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
          ${isMilitary ? `<span id="${lazyId}-flag"></span>` : _flag(_regoCountryCode(r.registration), { h: 26, vab: -13 })}
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
      <div style="margin-top:4px;display:flex;justify-content:space-between;align-items:flex-end;gap:8px">
        <div style="min-width:0;flex:1">
          ${airline ? `<div style="font-size:12px;color:var(--dim)" data-ext-name="${esc(airline)}">${esc(tExternalName(airline))}</div>` : ''}
          ${r.extra_info && !isMilitary && (r.notif_types || []).includes('Special Livery') ? `<div style="margin-top:6px;font-size:12px;color:var(--dim);font-style:italic;line-height:1.4">${esc(tLiveryName(r.extra_info))}</div>` : ''}
        </div>
        ${_detailQuickFilterBtns(r.registration)}
      </div>
      ${isMilitary ? `<div style="margin-top:10px;border-top:1px solid var(--border)"></div>${mapSections}` : `<div${r.flights.length > 2 ? ' class="flight-bars-scroll" style="max-height:290px"' : ''}>${flightBars}</div>`}
      <div class="detail-cards">
        ${isMilitary ? '' : `<div class="dc"><span class="lbl">${tt('Last Visit')}</span><span class="val" id="${lazyId}-lastseen" style="color:var(--dim)">—</span></div>`}
        ${_appRole === 'passenger' ? '' : `<div class="dc"><span class="lbl">${tt('Spotted')}</span><div id="${spotId}" style="margin-top:4px;color:var(--dim);font-size:12px">${tt('Never')}</div></div>`}
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
        ${type === 'Military' ? `<span id="${lazyId}-flag"></span>` : _flag(_regoCountryCode(r.registration), { h: 26, vab: -13 })}
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        ${statusStr ? `<span style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:20px;${statusPillStyle}">${esc(tLabel(_normStatus(statusStr) || statusStr))}</span>` : ''}
      </div>
    </div>
    <div style="margin-top:9px;display:flex;gap:6px;flex-wrap:wrap;align-items:center">
      <span class="chip ${chipClass(type)}">${chipLabel(type)}</span>
      ${mfrBadge(r.manufacturer)}
      ${acType ? `<span class="fc-actype">${esc(acType)}</span>` : ''}
    </div>
    <div style="margin-top:4px;display:flex;justify-content:space-between;align-items:flex-end;gap:8px">
      <div style="min-width:0;flex:1">
        ${airline ? `<div style="font-size:12px;color:var(--dim)" data-ext-name="${esc(airline)}">${esc(tExternalName(airline))}</div>` : ''}
        ${extra && type === 'Special Livery' ? `<div style="margin-top:6px;font-size:12px;color:var(--dim);font-style:italic;line-height:1.4">${esc(extra)}</div>` : ''}
      </div>
      ${_detailQuickFilterBtns(r.registration)}
    </div>
    <div id="${lazyId}-route"></div>
    <div class="detail-cards" style="margin-top:6px">
      ${card(tt('Last Seen'), lastSeen || `<span style="color:var(--dim)">${tt('Never')}</span>`)}
      ${_appRole === 'passenger' ? '' : `<div class="dc"><span class="lbl">${tt('Last Spotted')}</span><span class="val" id="${spotId}" style="color:var(--dim)">${tt('Never')}</span></div>`}
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

  // Defensive re-check, AFTER the modal/panel HTML above is already in the DOM
  // (its data-ext-name elements must exist before this can find/patch them —
  // calling this earlier, before render, was itself a bug: when every name is
  // already cached _translateNamesForZh() never awaits anything and its
  // DOM-patch loop runs synchronously to completion before there's anything
  // for it to find). loadFeed() already fires ONE upfront batch covering
  // every card's names, but that's a single fire-and-forget call — if it's
  // still in flight, failed, or for any reason didn't end up covering this
  // specific card's names, this card's own route bar would otherwise be
  // stuck showing English forever (nothing else ever re-requests it). Cheap
  // regardless: _translateNamesForZh() already skips any name that's already
  // cached, so this is a no-op network-wise for the common case where the
  // upfront batch already covered it — it just re-runs the DOM-patch pass
  // against THIS modal's fresh elements using whatever's already cached.
  _translateNamesForZh([
    _cardAirlineName(r),
    ...(r.flights || []).flatMap(f => [f.origin_name, f.dep_dest_name]),
    // Legacy single-row card shape (r.flights absent) carries these directly on r.
    r.origin_name, r.dep_dest_name,
  ].filter(Boolean));

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
           <div class="rfc-remarks-label">${tt('LIVERY')}</div>
           <div style="font-size:12px;color:var(--text);margin-top:4px">${esc(tLiveryName(_rf.extra_info))}</div>
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
    reasons.push({ text: tt('Harsh Light'), dq: false });
  } else if (light === 'low_light' && ts && sr && ss) {
    const minsAfterSr = Math.round((ts - sr) / 60);
    const minsBeforeSs = Math.round((ss - ts) / 60);
    const label = minsAfterSr >= 0 && minsAfterSr < minsBeforeSs
      ? tLowLight(minsAfterSr) : tLowLight(minsBeforeSs);
    reasons.push({ text: label, dq: false });
  }
  if (!qualifying && !light && ts && sr && ss) {
    if (ts < sr) reasons.push({ text: tt('Before Sunrise'), dq: true });
    else if (ts > ss) reasons.push({ text: tt('After Sunset'), dq: true });
  }
  if (f.reason && f.reason.startsWith('spotted_')) {
    const n = f.reason.split('_')[1];
    reasons.push({ text: tSpottedN(n), dq: true });
  }
  const sortedReasons = [...reasons.filter(r => r.dq), ...reasons.filter(r => !r.dq)];
  const reasonsHtml = sortedReasons.length ? `
    <div class="rfc-panel-body">
      <div class="rfc-remarks-label">${tt('REMARKS')}</div>
      <div class="rfc-remarks-pills">${sortedReasons.map(r => `<span class="rfc-remark-pill${r.dq ? ' rfc-remark-dq' : ''}">${esc(r.text)}</span>`).join('')}</div>
    </div>` : '';

  const photoHtml = f.photo_url ? `
    <div class="rfc-panel-photo-wrap">
      <img class="rfc-panel-photo" src="${esc(f.photo_url)}" loading="lazy" alt="">
      <div class="rfc-panel-photo-overlay">
        ${airline ? `<div class="rfc-panel-airline" data-ext-name="${esc(airline)}">${esc(tExternalName(airline))}</div>` : ''}
        <div class="rfc-panel-flight">${flightNum && flightNum !== '—' ? `<a href="${_fr24FlightUrl(flightNum)}" target="_blank" style="color:inherit;text-decoration:none">${esc(flightNum)}</a>` : esc(flightNum)}</div>
      </div>
    </div>` : '';

  const bodyHtml = reasonsHtml;

  return `${photoHtml}${bodyHtml}`;
}

async function _loadAircraftDetail(registration, uid, r) {
  const placeholderId = `detail-lazy-${uid}`;
  // Military cards: the flag next to the registration can't be reliably
  // guessed from the registration string (military serials aren't real
  // civil registrations, even when formatted to look like one — e.g. a US
  // N-number-style military tail can genuinely belong to another country).
  // extra_info's country segment comes from the aircraft's actual ICAO hex
  // allocation block instead (the same authoritative source already used
  // for the airforce roundel), so resolve THAT via the server (which uses
  // a real ISO-3166 library, not guessing) rather than the rego prefix.
  if ((r.notif_types || []).includes('Military') && r.extra_info) {
    const countryName = r.extra_info.split(' · ')[0].trim();
    if (countryName) {
      api(`/country-code/${encodeURIComponent(countryName)}`).then(d => {
        const flagEl = document.getElementById(placeholderId + '-flag');
        if (flagEl && d.code) flagEl.outerHTML = _flag(d.code, { h: 26, vab: -13 });
      }).catch(() => {});
    }
  }
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
          lsEl.textContent = tt('First visit');
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
            const dateLabel = _lang === 'zh'
              ? `${yr}/${String(d.getMonth()+1).padStart(2,'0')}/${day}`
              : `${day} ${mon} '${yr}`;
            const apt = s.airport || '';
            const cc  = _airportCountry(apt);
            const flag = _flag(cc, { h: 11, vab: -1 });
            const codePart = flag
              ? `<span style="display:inline-flex;align-items:center;gap:3px">${flag}${esc(apt)}</span>`
              : esc(apt);
            const sesNotes = (s.notes || '').trim().toLowerCase();
            const hl = isLivery && curLivery && sesNotes && sesNotes === curLivery;
            const isoDate = `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
            const clickable = _appRole === 'controller' && apt;
            const clickAttrs = clickable
              ? ` class="col-ex-pill${hl ? ' col-ex-pill-hl' : ''} col-ex-pill-clickable" onclick="_spOpenPhotos('${esc(registration)}','${esc(apt)}','${isoDate}')"`
              : ` class="col-ex-pill${hl ? ' col-ex-pill-hl' : ''}"`;
            return `<span${clickAttrs}>` +
              `<span class="col-ex-pill-code">${codePart}</span>` +
              `<span class="col-ex-pill-sep"></span>` +
              `<span class="col-ex-pill-count" style="color:var(--text)">${dateLabel}</span>` +
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
                statusEl.innerHTML = `<span style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:3px 9px;border-radius:20px;background:${bg};color:${fg}">${esc(tLabel(norm))}</span>`;
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

// Chinese equivalent of _cityName's English suffix-stripping — drops a
// trailing "国际机场"/"机场" ("international airport"/"airport") the same way
// _cityName drops "International Airport"/"Airport" for English, e.g.
// "上海浦东国际机场" -> "上海浦东", "奥克兰机场" -> "奥克兰".
function _cityNameZh(name) {
  return (name || '').replace(/(国际)?机场\s*$/, '').trim();
}

function _routeAirportDisp(name) {
  if (_lang === 'zh' && _nameTranslationCache[_extNameKey(name)]) return _cityNameZh(_nameTranslationCache[_extNameKey(name)]);
  return _cityName(name);
}

function _renderRouteBar(data, r) {
  const originIata = r.origin_iata  || data.origin_iata  || '';
  const originName = r.origin_name  || data.origin_name  || '';
  const originCity = r.origin_city  || data.origin_city  || '';
  const centerIata = data.airport_iata || r.airport_iata || '';
  const centerName = data.airport_name || r.airport_name || '';
  // A Cancelled/Swapped arrival never actually happened under this identity, so
  // it can't have a "next departure" either — Diverted is deliberately excluded
  // here since a diverted aircraft can still depart again later from wherever
  // it landed instead. Checked on both `r.current_status` (legacy single-row
  // cards, where r is the raw flight row) and `data.arr_label` (rego-group
  // cards, where r has no current_status but fData.arr_label was already set
  // to the resolved-away status string) since the two call sites pass
  // differently-shaped objects.
  const neverArrived = r.current_status === 'Cancelled' || r.current_status === 'Swapped'
    || data.arr_label === 'Cancelled' || data.arr_label === 'Swapped';
  const nextDest   = neverArrived ? '' : (data.next_dep_dest_iata || '');
  const nextName   = data.next_dep_dest_name || '';
  const nextCity   = data.next_dep_dest_city || '';
  const isMobile   = window.innerWidth < 768;
  const originDisp = isMobile ? (originCity || _routeAirportDisp(originName)) : _routeAirportDisp(originName);
  const nextDisp   = isMobile ? (nextCity   || _routeAirportDisp(nextName))   : _routeAirportDisp(nextName);
  const nextFlight = neverArrived ? '' : (data.next_dep_flight || '');
  const nextConf   = neverArrived ? 0  : (data.next_dep_confidence || 0);
  const nextLabel  = neverArrived ? '' : (data.next_dep_label || '');
  const effArrTs   = (_isSameFlight(r) ? r.live_arrival_ts : null) || r.arrival_ts;
  const arrTime    = effArrTs ? fmtTs(effArrTs, { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '';
  const depTime    = neverArrived ? '' : (data.next_dep_ts ? fmtTs(data.next_dep_ts, { weekday: 'short', hour: '2-digit', minute: '2-digit' }) : '');

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
      'Cancelled': ['rgba(239,68,68,0.18)',   '#ef4444'],
      'Diverted':  ['rgba(168,85,247,0.18)',  '#a855f7'],
      'Swapped':   ['rgba(120,120,120,0.10)', 'var(--dim)'],
    };
    const base = 'font-size:9px;font-weight:700;padding:2px 0;border-radius:20px;text-transform:uppercase;letter-spacing:.05em;white-space:nowrap;flex-shrink:0;min-width:76px;text-align:center;box-sizing:border-box';
    if (label === 'Predicted') {
      const pct = conf || 0;
      const bg = `linear-gradient(to right,rgba(245,158,11,0.85) ${pct}%,rgba(245,158,11,0.12) ${pct}%)`;
      return `<span style="${base};background:${bg};color:#92400e">${esc(tLabel(label))}</span>`;
    }
    const [bg, color] = COLORS[label] || COLORS['Scheduled'];
    return `<span style="${base};background:${bg};color:${color}">${esc(tLabel(label))}</span>`;
  };

  const _timeLine = (label, time, conf) => !time ? '' :
    `<span style="display:flex;align-items:center;justify-content:center;gap:6px;margin-top:4px">
      ${_pill(label, conf)}
      <span class="rb-sub" style="margin:0;min-width:72px;text-align:left">${esc(time)}</span>
    </span>`;

  const arrTimeHtml = _timeLine(arrLabel, arrTime, null);
  const depTimeHtml = _timeLine(nextLabel, depTime, nextLabel === 'Predicted' ? nextConf : null);

  if (!originIata && !centerIata) return '';

  const fr24Airport = _fr24AirportUrl;
  const fr24Flight  = _fr24FlightUrl;

  const rightNode = nextDest ? `
    <div class="rb-arrow">✈</div>
    <div class="rb-node">
      <span class="rb-lbl">${tt('Next Dep.')}</span>
      <a class="rb-iata rb-link" href="${fr24Airport(nextDest)}" target="_blank">${esc(nextDest)}</a>
      ${nextDisp   ? `<span class="rb-sub"${nextName ? ` data-ext-name="${esc(nextName)}" data-ext-city="1"` : ''}>${esc(nextDisp)}</span>` : ''}
      ${nextFlight ? `<a class="rb-sub rb-link" href="${fr24Flight(nextFlight)}" target="_blank">${esc(nextFlight)}</a>` : ''}
    </div>` : '';

  return `<div class="rb${r.resolved_away ? ' rb-resolved-away' : ''}">
    <div class="rb-node">
      <span class="rb-lbl">${tt('Arr. From')}</span>
      <a class="rb-iata rb-link" href="${fr24Airport(originIata)}" target="_blank">${esc(originIata)}</a>
      ${originDisp      ? `<span class="rb-sub"${originName ? ` data-ext-name="${esc(originName)}" data-ext-city="1"` : ''}>${esc(originDisp)}</span>` : ''}
      ${r.flight_number ? `<a class="rb-sub rb-link" href="${fr24Flight(r.flight_number)}" target="_blank">${esc(r.flight_number)}</a>` : ''}
    </div>
    <div class="rb-arrow">✈</div>
    <div class="rb-node rb-here">
      <span class="rb-lbl">${tt('At')}</span>
      <a class="rb-iata rb-link" href="${fr24Airport(centerIata)}" target="_blank">${esc(centerIata)}</a>
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
      lbl.textContent = tt('Refresh Collection');
    } else if (name === 'recommendation') {
      btn.onclick = () => { _recLoaded = false; toast('Plotting the windows…'); loadRecommendation(true); };
      lbl.textContent = tt('Refresh Spotting');
    } else if (name === 'settings') {
      btn.onclick = () => armRestartBackend();
      btn.classList.add('btn-danger');
      lbl.textContent = tt('Restart Server');
    } else {
      btn.onclick = () => forceCheck();
      lbl.textContent = tt('Refresh Feed');
    }
    _syncRefreshBtnVisibility();
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
  // Passengers are always read-only for filter/watchlist lists (server 403s
  // any write anyway) — never render the delete button for them.
  const delBtn = (onclick, title) => _appRole === 'passenger' ? '' :
    `<button class="del-btn"${title ? ` title="${title}"` : ''} onclick="${onclick}">✕</button>`;

  $('fl-exclusion').innerHTML = (f.filter_exclusions || f.exclusion_list || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.registration)}</div>
        ${r.description ? `<div class="filter-secondary">${esc(r.description)}</div>` : ''}
      </div>
      ${delBtn(`delExclusion('${esc(r.registration)}')`, 'Remove')}
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-rego').innerHTML = (f.filter_regos || f.rego_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.registration)}</div>
        ${r.description ? `<div class="filter-secondary">${esc(r.description)}</div>` : ''}
      </div>
      ${delBtn(`delRego('${esc(r.registration)}')`)}
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-type').innerHTML = (f.filter_types || f.type_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.aircraft_type)}</div>
        <div class="filter-secondary">${esc(r.airline)}</div>
      </div>
      ${delBtn(`delType('${esc(r.airline)}','${esc(r.aircraft_type)}')`)}
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';

  $('fl-airline').innerHTML = (f.filter_airlines || f.airline_watchlist || []).map(r => `
    <div class="filter-row">
      <div class="main">
        <div class="filter-primary">${esc(r.icao_code)} <span style="color:var(--dim);font-size:11px">${esc(r.entry_type)}</span></div>
        ${r.name ? `<div class="filter-secondary">${esc(r.name)}</div>` : ''}
      </div>
      ${delBtn(`delAirline('${esc(r.icao_code)}','${esc(r.entry_type)}')`)}
    </div>`).join('') || '<div class="detail" style="padding:4px 2px">Empty</div>';
}

async function addExclusion() {
  const rego = $('excl-rego').value.trim().toUpperCase();
  const desc = $('excl-desc').value.trim();
  if (!rego) { toast(tt('Enter a registration')); return; }
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
  if (!rego) { toast(tt('Enter a registration')); return; }
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
  // Monitoring — Cancellation / Diversion
  { group: 'mon-cancel', key: 'MONITOR_CANCEL_GRACE_MINS',   label: 'Never-Departed Grace',  desc: 'How long past a scheduled-but-never-airborne flight\'s ETA to wait, before presuming it was cancelled.',        type: 'number', min: 15, max: 360, unit: 'minutes' },
  { group: 'mon-cancel', key: 'MONITOR_DIVERTED_GRACE_MINS', label: 'Untracked Landing Grace', desc: 'How long past ETA to wait for a tracked-airborne flight that goes silent, before presuming it diverted elsewhere.', type: 'number', min: 10, max: 180, unit: 'minutes' },
  { group: 'mon-cancel', key: 'MONITOR_ABSENCE_CHECKS',      label: 'Absence Streak',        desc: 'Consecutive checks a flight must be missing from every FR24 page before it\'s presumed cancelled/diverted.',   type: 'number', min: 1,  max: 10,  unit: 'checks' },
  { group: 'mon-cancel', key: 'MONITOR_CONFIRM_CALL_CAP',    label: 'Confirmation Call Cap', desc: 'Maximum FR24 lookups per check spent confirming a presumed cancellation/diversion — a safety valve against rate-limiting.', type: 'number', min: 1, max: 20, unit: 'calls' },
  // Special Livery
  { group: 'livery', key: 'SPECIAL_LIVERY_KEYWORDS',           label: 'Keywords',             desc: 'A flight matches if its airline name contains any of these words (case-insensitive). e.g. "retro", "special".',  type: 'tags', restart: true },
  { group: 'livery', key: 'SPECIAL_LIVERY_EXCLUDE_KEYWORDS',   label: 'Exclude Keywords',     desc: 'If the airline name contains any of these words the match is suppressed — use to block standard liveries.',      type: 'tags', restart: true },
  // Rare Plane
  { group: 'rare', key: 'RARE_PLANE_MIN_ABSENCE_DAYS',         label: 'Minimum Days Absent',  desc: 'An aircraft type is only considered "rare" if it hasn\'t been seen at this airport for at least this many days.', type: 'number', min: 1, max: 365, unit: 'days', restart: true },
  // Collection — session photo preview
  { group: 'collection', key: 'SESSION_PHOTOS_PATH', label: 'Mount Path', desc: 'Container-internal path your photo folder is mounted at (see docker-compose.yml). Defaults to /app/photos.', placeholder: '/app/photos' },
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
      return `<input class="setting-input" data-key="${k}" value="${esc(String(v))}" placeholder="${esc(item.placeholder || '—')}">`;
  }
}

function _settingRow(item, value) {
  const uCls = item.unused ? ' setting-unused' : '';
  const uTag = item.unused ? ` <span class="setting-unused-tag">not active</span>` : '';
  // SETTINGS_SCHEMA's own label/desc are the English text; SETTINGS_I18N_ZH
  // (keyed by item.key) supplies the Chinese equivalents — same "fixed
  // vocabulary this frontend authors itself" reasoning as tLabel/tChip/tWx.
  const trans = (_lang === 'zh' && SETTINGS_I18N_ZH[item.key]) || null;
  const label = trans ? trans.label : item.label;
  const desc = trans ? trans.desc : item.desc;
  if (item.type === 'tags') {
    const tags = String(value || '').split(',').map(t => t.trim()).filter(Boolean);
    const readOnly = _appRole === 'passenger';
    return `<div class="setting-row-full${uCls}" data-key="${item.key}">
      <div class="setting-key">${label}${uTag}</div>
      ${desc ? `<div class="setting-desc">${esc(desc)}</div>` : ''}
      <div class="tags-list">${tags.map(t =>
        `<span class="tag-chip">${esc(t)}${readOnly ? '' : `<button class="tag-del" data-tag="${esc(t)}">×</button>`}</span>`
      ).join('')}</div>
      ${readOnly ? '' : `<div class="tags-add-row">
        <input class="tags-input" placeholder="${esc(tt('Add keyword…'))}">
        <button class="add-btn tags-add-btn">${esc(tt('Add'))}</button>
      </div>`}
    </div>`;
  }
  return `<div class="setting-row${uCls}">
    <div class="setting-label">
      <div class="setting-key">${label}${uTag}</div>
      ${desc ? `<div class="setting-desc">${esc(desc)}</div>` : ''}
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
    const addBtn = row.querySelector('.tags-add-btn');
    if (inp && addBtn) {
      const addFn = () => {
        const val = inp.value.trim();
        if (!val) return;
        _addSettingTag(row, key, val);
        inp.value = '';
      };
      addBtn.addEventListener('click', addFn);
      inp.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); addFn(); } });
    }
    // Tags — delete chips (row has none at all for a read-only Passenger view)
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
    // Controller edits everything; a Pilot edits only their own spotting/filter
    // groups (everything else in a visible subtab is still shown, just
    // read-only); a Passenger never edits anything.
    const editable = g => _appRole === 'controller' || (_appRole === 'pilot' && PILOT_EDITABLE_GROUPS.has(g));
    groups.forEach(g => {
      const el = $('settings-' + g);
      if (!el) return;
      el.innerHTML = SETTINGS_SCHEMA.filter(x => x.group === g)
        .filter(x => !(_appRole !== 'controller' && CONTROLLER_ONLY_SETTINGS.has(x.key)))
        .map(item => _settingRow(item, s[item.key] ?? '')).join('');
      if (!editable(g)) {
        el.querySelectorAll('.setting-row, .setting-row-full').forEach(r => r.classList.add('setting-readonly'));
      }
    });
    // Static inputs not in SETTINGS_SCHEMA — populate manually
    const lsEl = $('info-logostream-key');
    if (lsEl && !lsEl.dataset.userEdited) lsEl.value = s.LOGOSTREAM_API_KEY || '';
    const baiduAppIdEl = $('info-baidu-appid');
    if (baiduAppIdEl && !baiduAppIdEl.dataset.userEdited) baiduAppIdEl.value = s.BAIDU_TRANSLATE_APP_ID || '';
    const baiduSecretEl = $('info-baidu-secret');
    if (baiduSecretEl && !baiduSecretEl.dataset.userEdited) baiduSecretEl.value = s.BAIDU_TRANSLATE_SECRET_KEY || '';
    _wireSettings();
  } catch (e) {
    toast('Failed to load settings: ' + e.message);
  }
  if (_appRole === 'controller' || _appRole === 'pilot') loadMyCatalog();
}

// ── My Catalog (per-user Lightroom catalog upload) ─────────────────────────────

async function loadMyCatalog() {
  const statusEl = $('my-catalog-status');
  const removeBtn = $('my-catalog-remove-btn');
  if (!statusEl) return;
  try {
    const s = await api('/catalog/status');
    if (s.has_catalog) {
      statusEl.textContent = `${tt('Uploaded:')} ${s.filename}`;
      if (removeBtn) removeBtn.style.display = '';
    } else {
      statusEl.textContent = tt('No catalog uploaded yet.');
      if (removeBtn) removeBtn.style.display = 'none';
    }
  } catch (e) {
    statusEl.textContent = tt('Failed to load catalog status.');
  }
}

async function uploadMyCatalog() {
  const fileEl = $('my-catalog-file');
  const file = fileEl && fileEl.files[0];
  if (!file) { toast('Choose a .lrcat file first'); return; }
  const statusEl = $('my-catalog-status');
  statusEl.textContent = 'Uploading…';
  const form = new FormData();
  form.append('file', file);
  try {
    const r = await fetch('/api/catalog/upload', { method: 'POST', body: form });
    if (!r.ok) throw new Error(await r.text());
    fileEl.value = '';
    toast('Catalog uploaded');
    _recLoaded = false;
    loadMyCatalog();
  } catch (e) {
    toast('Upload failed: ' + e.message);
    statusEl.textContent = 'Upload failed.';
  }
}

async function removeMyCatalog() {
  try {
    await api('/catalog', { method: 'DELETE' });
    toast('Catalog removed');
    _recLoaded = false;
    loadMyCatalog();
  } catch (e) {
    toast('Remove failed: ' + e.message);
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
    if (s.effective_tz) _appTz = s.effective_tz;
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
      _srchDDCreate('srch-dd-mfr',        tt('All Manufacturer'), [], () => _srchFlFilter());
      _srchDDCreate('srch-dd-airline',    tt('All Airline'),      [], () => _srchFlFilter());
      _srchDDCreate('srch-dd-type',       tt('All Type'),         [], () => _srchFlFilter());
      _srchDDCreate('srch-dd-rt-origin',  tt('All Origins'),      [], () => { _srchRtMirror('origin'); _srchRtRun(window.innerWidth >= 768); });
      _srchDDCreate('srch-dd-rt-dest',    tt('All Destinations'), [], () => { _srchRtMirror('dest');   _srchRtRun(window.innerWidth >= 768); });
      _srchDDCreate('srch-dd-rt-airline', tt('All Airlines'),     [], () => _srchRtRun(window.innerWidth >= 768));
      _srchDDCreate('srch-dd-cat-mfr',    tt('All Manufacturers'),[], () => _srchRun(window.innerWidth >= 768));
      _srchDDCreate('srch-dd-cat-type',   tt('All Types'),        [], () => _srchRun(window.innerWidth >= 768));
      _srchDDCreate('srch-dd-cat-airline',tt('All Airlines'),     [], () => _srchRun(window.innerWidth >= 768));
      _srchDDCreate('srch-dd-cat-airport',tt('All Airports'),     [], () => _srchRun(window.innerWidth >= 768));
      _srchDDCreate('srch-dd-cat-keyword',tt('All Keywords'),     [], () => _srchRun(window.innerWidth >= 768));
      _srchFiltersTs = Date.now();
      _srchFlLoadFilters();
      _srchRtLoadFilters();
    } else {
      _srchMaybeRefreshFilters();
    }
    $('srch-fl-status').textContent = tt('Enter a registration or select a filter.');
    $('srch-rt-status').textContent = tt('Enter a flight number or select a filter.');
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
  if (lbl) lbl.textContent = tt('Loading…');
  if (force) toast('Dusting off the catalog…');
  try {
    const d = await api(force ? '/catalog-stats?force=true' : '/catalog-stats');
    if (d.error) { toast('Collection: ' + d.error); return; }
    const noCatEl = $('col-no-catalog-msg'), dashEl = $('col-dashboard');
    if (d.no_catalog) {
      if (noCatEl) noCatEl.classList.remove('hidden');
      if (dashEl) dashEl.classList.add('hidden');
      return;
    }
    if (noCatEl) noCatEl.classList.add('hidden');
    if (dashEl) dashEl.classList.remove('hidden');
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
  if (_lang === 'zh') {
    const [y, m, d] = dateStr.split('-');
    return `${y.slice(2)}/${m}/${d}`;
  }
  const d = new Date(dateStr + 'T00:00:00');
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: '2-digit' });
}

function _fmtFleetDate(ts) {
  if (!ts) return '—';
  if (_lang === 'zh') return _zhFullDate(new Date(ts * 1000).toLocaleDateString('en-CA'));
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
  const wrap = $('flt-wrap');
  let catStatus;
  try { catStatus = await api('/catalog/status'); } catch { catStatus = { has_catalog: false }; }
  if (!catStatus.has_catalog) {
    if (wrap) {
      wrap.style.paddingLeft = wrap.style.paddingRight = '';
      wrap.innerHTML = `<div class="flt-empty">
        <div style="color:var(--dim);font-size:13px;text-align:center;padding:0 20px;line-height:1.5">
          ${esc(tt('Upload a Lightroom catalog in Settings → Collection to use Fleet tracking.'))}
        </div>
      </div>`;
    }
    return;
  }
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
      <div style="color:var(--dim);font-size:12px;margin-top:4px">${esc(tt('Track a Fleet'))}</div>
    </div>`;
    return;
  }

  _translateNamesForZh(_fleetCards.map(c => c.airline).filter(Boolean));
  let html = _fleetCards.map((c, i) => _fleetCardHtml(c, i)).join('');

  if (_fleetAdding) {
    html += `<div class="flt-card flt-card--input" id="flt-input-card">
      <div class="flt-input-inner">
        <div class="flt-input-label">${esc(tt('Enter IATA or ICAO airline code'))}</div>
        <input class="flt-code-input" id="flt-code-inp" type="text" placeholder="${esc(_lang === 'zh' ? '例如 QF、QFA' : 'e.g. QF, QFA')}" maxlength="4" autocomplete="off" spellcheck="false">
        <div class="flt-input-btns">
          <button class="flt-btn-go" onclick="_fleetConfirm()">${esc(tt('Search'))}</button>
          <button class="flt-btn-cancel" onclick="_fleetCancelAdd()">${esc(tt('Cancel'))}</button>
        </div>
        <div class="flt-input-err" id="flt-inp-err"></div>
      </div>
    </div>`;
  }

  html += `<div class="flt-add-col">
    <button class="flt-add-col-btn" onclick="_fleetAddCard()" title="${esc(tt('Add airline'))}">+</button>
    <div class="flt-add-col-label">${esc(tt('Add Airline'))}</div>
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
    const badge = g.manufacturer ? `<span class="mfr mfr-${mfrCls}" data-ext-name="${esc(g.manufacturer)}">${esc(_mfrDisp(g.manufacturer))}</span>` : '';
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
      const havePreviewable = a.photos > 0 && _appRole === 'controller' && a.last_date && a.last_ap_iata;
      const clickAttr = a.photos === 0 && !isWatched
        ? `onclick="_fleetPillClick(this,'${esc(a.registration)}')"`
        : havePreviewable
          ? `onclick="_spOpenPhotos('${esc(a.registration)}','${esc(a.last_ap_iata)}','${esc(a.last_date)}')"`
          : '';
      const pillCls = havePreviewable ? `${cls} flt-rego-pill-clickable` : cls;
      let right = '';
      if (a.photos > 0 && a.last_date) {
        const apFlag = a.last_ap_cc ? `<span class="flt-ap-flag">${_flagEmoji(a.last_ap_cc, 10)}</span>` : '';
        const apCode = a.last_ap_iata || '';
        const date = _fltShortDate(a.last_date);
        const sep = date && apCode ? '&nbsp;&nbsp;·&nbsp;&nbsp;' : '';
        right = `<span class="flt-pill-ct">${date}${sep}${apFlag}${apCode ? ' ' + esc(apCode) : ''}</span>`;
      }
      return `<span class="flt-rego-pill ${pillCls}" ${clickAttr}>${flag}${esc(a.registration)}${right}</span>`;
    }).join('');
    return header + `<div id="flt-g-${key}" class="flt-pill-wrap" style="display:${open ? 'flex' : 'none'}">${acRows}</div>`;
  }).join('');

  return `<div class="flt-card">
    <div class="flt-card-hd">
      <img class="flt-hd-logo" src="${logoSrc}" onerror="this.style.display='none'" alt="">
      <div class="flt-hd-info">
        <div class="flt-hd-name"><a href="${_fr24AirlineUrl(card.iata, card.icao)}" target="_blank" style="color:inherit;text-decoration:none" data-ext-name="${esc(card.airline)}">${esc(tExternalName(card.airline))}</a> <span style="color:var(--dim);font-weight:400;font-size:12px;margin-left:3px">${esc(card.iata)}/${esc(card.icao)}</span></div>
        <div class="flt-hd-cov">${have} / ${total} · ${pct}% <span style="margin-left:8px;color:var(--dim);font-size:10px;text-transform:uppercase;letter-spacing:.04em">${tt('Updated')} ${_fmtFleetDate(card.updated_at)}</span></div>
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
  if (card) card.innerHTML = `<div class="flt-loading">${_lang === 'zh' ? `正在获取 ${esc(code)}…` : `Fetching ${esc(code)}…`}</div>`;

  try {
    const d = await api(`/fleet-coverage?code=${encodeURIComponent(code)}`);
    if (d.error) throw new Error(d.error);
    const dup = _fleetCards.some(c => (d.icao && c.icao === d.icao) || (d.iata && c.iata === d.iata));
    if (dup) throw new Error(_lang === 'zh' ? `${d.airline || code} 已添加。` : `${d.airline || code} is already added.`);
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

const _LOGO_V = 6;  // bump to bust SW logo cache when server-side logic changes
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
  const cleanName = (rawName || '').replace(/\s*\(.*?\)/g, '').trim();
  // Military entries in the "airline" catalog property follow "Country -
  // Branch" (e.g. "Australia - Royal Australian Air Force", "United States -
  // Air Force") — the trailing parenthetical is a military unit code, not a
  // real airline ICAO, so /api/airline-logo/<code> 404s and the broken image
  // silently hides itself (onerror), leaving a blank slot. Route these to
  // the airforce-roundel endpoint instead, same as Feed/detail cards do.
  const milMatch = cleanName.match(/^(.+?)\s+-\s+.+$/);
  if (milMatch) {
    const src = `/api/airforce-roundel/${encodeURIComponent(milMatch[1].trim())}?v=${_LOGO_V}`;
    return `<span class="col-logo-slot"><img class="col-airline-logo" src="${src}" onerror="this.style.display='none'" loading="lazy" alt=""></span>`;
  }
  const m = rawName && rawName.match(/\(([A-Z]{2,4})\)\s*$/);
  const icao = m ? m[1] : '';
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
  return `<span class="mfr mfr-${cls}" data-ext-name="${esc(t.manufacturer)}">${esc(_mfrDisp(t.manufacturer))}</span>`;
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
        <span class="col-stats-row-name"${s.airport_name ? ` data-ext-name="${esc(s.airport_name)}" data-ext-city="1"` : ''}>${esc(window.innerWidth < 768 ? (s.airport || _airportDisplayName(s.airport_name || '')) : (_airportDisplayName(s.airport_name || '') || s.airport || ''))}</span>
        <span class="col-stats-row-sub">${esc(_colDateLabel(s))}</span>
        <span class="col-stats-row-count" style="align-self:center">${tAircraftPhotos(s.aircraft, s.photos)}</span>
      </div>
    </div>`).join('');
}

function _colRenderRows(items, nameKey, countKey, subFn, prefixFn, rowClass, dataAttr, nameFn, extNameFn, extIsCity) {
  if (!items || !items.length) return '<div class="empty">No data</div>';
  const max = Math.max(...items.map(i => i[countKey]||0), 1);
  return items.map(item => {
    const extName = extNameFn ? extNameFn(item) : '';
    return `
    <div class="col-stats-row ${rowClass||''}" ${dataAttr ? dataAttr(item) : ''}>
      <div class="col-stats-row-bar" style="width:${Math.round((item[countKey]||0)/max*100)}%"></div>
      <div class="col-stats-row-content">
        ${prefixFn ? prefixFn(item) : ''}
        <span class="col-stats-row-name${nameKey==='iata'?' col-stats-iata':''}"${extName ? ` data-ext-name="${esc(extName)}"${extIsCity ? ' data-ext-city="1"' : ''}` : ''}>${nameFn ? nameFn(item) : item[nameKey]}</span>
        ${subFn ? `<span class="col-stats-row-sub">${subFn(item)}</span>` : ''}
        <span class="col-stats-row-count">${(item[countKey]||0).toLocaleString()}</span>
      </div>
    </div>`;
  }).join('');
}

function _colRenderRegoRows(items, metricKey, metricLabel) {
  if (!items || !items.length) return '<div class="empty">No data</div>';
  const max = Math.max(...items.map(i => i[metricKey]||0), 1);
  _translateNamesForZh(items.map(i => (i.airline || '').replace(/\s*\([^)]+\)\s*$/, '').trim()).filter(Boolean));
  return items.map(item => {
    const badge = item.manufacturer
      ? `<span class="mfr mfr-${item.manufacturer.toLowerCase().replace(/\s+/g,'-')}" data-ext-name="${esc(item.manufacturer)}">${esc(_mfrDisp(item.manufacturer))}</span>`
      : '';
    const typeName = item.aircraft_type_name || item.aircraft_type || '';
    const airlineName = (item.airline || '').replace(/\s*\([^)]+\)\s*$/, '').trim();
    const airlineDisp = airlineName ? `<span data-ext-name="${esc(airlineName)}">${esc(tExternalName(airlineName))}</span>` : '';
    const greyParts = [typeName ? esc(typeName) : '', airlineDisp].filter(Boolean);
    const sub = greyParts.length ? `<span style="font-size:10px;color:var(--dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${greyParts.join(' · ')}</span>` : '';
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
  _translateNamesForZh(items.map(h => (h.airline || '').replace(/\s*\([^)]+\)\s*$/, '').trim()).filter(Boolean));
  return items.map(h => {
    const chips = h.airports.map(a => `<span class="col-hopper-chip">${a.flag?a.flag+' ':''}${a.iata}</span>`).join('');
    const badge = h.manufacturer ? `<span class="mfr mfr-${h.manufacturer.toLowerCase().replace(/\s+/g,'-')}" data-ext-name="${esc(h.manufacturer)}">${esc(_mfrDisp(h.manufacturer))}</span>` : '';
    const typeName = h.aircraft_type_name || h.aircraft_type || '';
    const airlineName = (h.airline || '').replace(/\s*\([^)]+\)\s*$/, '').trim();
    const airlineDisp = airlineName ? `<span data-ext-name="${esc(airlineName)}">${esc(tExternalName(airlineName))}</span>` : '';
    const greyParts = [typeName ? esc(typeName) : '', airlineDisp].filter(Boolean);
    const sub = greyParts.length ? `<span style="font-size:10px;color:var(--dim)">${greyParts.join(' · ')}</span>` : '';
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
    const daysText = tDaysAgo(ls.days_ago);
    const pillStyle =
      ls.days_ago < 7  ? 'background:var(--surface2);border:1px solid var(--border);color:var(--dim)' :
      ls.days_ago < 30 ? 'background:rgba(234,179,8,0.15);border:1px solid rgba(234,179,8,0.4);color:#eab308' :
                         'background:rgba(239,68,68,0.15);border:1px solid rgba(239,68,68,0.4);color:#ef4444';
    if (window.innerWidth < 768) {
      $('col-last-session-bar').innerHTML = `
        <div class="lsb-m-row1">${tt('Last Session')}</div>
        <div class="lsb-m-row2">${ls.flag?ls.flag+' ':''}<span data-ext-name="${esc(ls.airport_name||'')}" data-ext-city="1">${esc(_airportDisplayName(ls.airport_name))}</span> <span class="lsb-airport-code">${esc(ls.airport)}</span></div>
        <div class="lsb-m-row3">
          <span class="lsb-date">${esc(_colDateLabel(ls))}</span>
          <span class="lsb-days-ago" style="${pillStyle};border-radius:10px;padding:2px 10px;font-size:11px;font-weight:600">${daysText}</span>
        </div>`;
    } else {
      $('col-last-session-bar').innerHTML = `
        <span class="lsb-label">${tt('Last session')}</span>
        <span style="display:inline-flex;align-items:baseline;gap:0">
          <span class="lsb-airport-name">${ls.flag?ls.flag+' ':''}<span data-ext-name="${esc(ls.airport_name||'')}" data-ext-city="1">${esc(_airportDisplayName(ls.airport_name))}</span></span>
          <span class="lsb-airport-code">${esc(ls.airport)}</span>
        </span>
        <span class="lsb-divider"></span>
        <span class="lsb-date">${esc(_colDateLabel(ls))}</span>
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
      lblEl.textContent = tColKeyword(kw.keyword);
      if (box) {
        box.dataset.keyword = kw.keyword;
        const isLivery = kw.keyword === 'Special Livery';
        box.style.cursor = isLivery ? 'pointer' : 'default';
        box.style.pointerEvents = isLivery ? '' : 'none';
      }
    } else {
      numEl.textContent = '—';
      lblEl.textContent = tt('Not set');
      if (box) { box.dataset.keyword = ''; box.style.cursor = 'default'; box.style.pointerEvents = 'none'; }
    }
  });

  $('col-sessions').innerHTML    = _colRenderSessionRows(d.sessions);
  _translateNamesForZh([
    ...(d.sessions || []).map(s => s.airport_name),
    ...(d.top_airports || []).map(i => i.full_name),
    ...(d.top_airlines || []).map(i => { const m = (i.raw_name||'').match(/^(.*?)\s*\(([A-Z]{2,4})\)\s*$/); return m ? m[1] : (i.name||''); }),
    d.last_session ? d.last_session.airport_name : '',
  ].filter(Boolean));
  const _colAirlineBase = i => { const m = (i.raw_name||'').match(/^(.*?)\s*\(([A-Z]{2,4})\)\s*$/); return m ? m[1] : (i.name||''); };
  $('col-airlines').innerHTML    = _colRenderRows(d.top_airlines, 'name', 'photos',
    i => { const m = (i.raw_name||'').match(/^(.*?)\s*\(([A-Z]{2,4})\)\s*$/); return m ? m[2] : ''; },
    i => _colAirlineLogo(i.raw_name||''), 'col-airline-row',
    i => `data-airline="${(i.raw_name||'').replace(/"/g,'&quot;')}"`,
    i => esc(tExternalName(_colAirlineBase(i))),
    i => _colAirlineBase(i));
  $('col-airports').innerHTML    = _colRenderRows(d.top_airports, 'full_name', 'photos',
    i => i.iata || '',
    i => `<span style="width:24px;flex-shrink:0;display:flex;align-items:center;justify-content:center;margin-left:-4px;font-size:16px">${i.flag||''}</span>`,
    'col-airport-row',
    i => `data-iata="${(i.iata||'').replace(/"/g,'&quot;')}"`,
    i => esc(_airportDisplayName(i.full_name || i.iata)),
    i => i.full_name || i.iata, true);
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
        <span style="display:flex;align-items:center;gap:8px;min-width:0">${logo}<span style="font-size:12px;color:var(--text)">${esc(tLiveryName(a.livery))}</span></span>
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
        const clickable = _appRole === 'controller' && a.reg;
        const clickCls  = clickable ? ' col-sp-row-clickable' : '';
        const clickAttr = clickable
          ? ` onclick="_spOpenPhotos('${esc(a.reg)}','${esc(airport)}','${esc(date)}')"`
          : '';
        if (window.innerWidth < 768) {
          return `<div class="col-sp-row col-sp-row-m${clickCls}"${clickAttr}>
            <div class="col-sp-m-row1"><span class="col-sp-reg">${esc(a.reg)}</span>${badge}</div>
            <div class="col-sp-m-row2">${[a.aircraft_type, a.airline].filter(Boolean).join(' · ')}</div>
            <div class="col-sp-tags">${tagHtml}</div>
          </div>`;
        }
        return `<div class="col-sp-row${clickCls}"${clickAttr}>
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
          _translateNamesForZh(aircraft.map(a => (a.airline || '').replace(/\s*\(.*?\)/g, '').trim()).filter(Boolean));
          const rows = aircraft.map(a => {
            const badge = a.manufacturer ? `<span class="mfr mfr-${a.manufacturer.toLowerCase().replace(/\s+/g,'-')}" data-ext-name="${esc(a.manufacturer)}">${esc(_mfrDisp(a.manufacturer))}</span>` : '';
            const airline = (a.airline || '').replace(/\s*\(.*?\)/g, '').trim();
            const airlineDisp = airline ? `<span data-ext-name="${esc(airline)}">${esc(tExternalName(airline))}</span>` : '';
            const visibleTags = _sessionFilterTags ? a.tags.filter(t => _sessionFilterTags.has(t)) : a.tags;
            const tagHtml = visibleTags.map(t => `<span class="col-sp-tag ${_colTagClass(t)}">${esc(tColKeyword(t))}</span>`).join('');
            const notesHtml = (a.notes && a.tags.includes('Special Livery'))
              ? `<span style="font-size:11px;color:var(--dim);margin-right:2px">${esc(tLiveryName(a.notes))}</span>` : '';
            const parts = [a.aircraft_type ? esc(a.aircraft_type) : '', airlineDisp].filter(Boolean).join('<span class="col-sp-dot">·</span>');
            const clickable = _appRole === 'controller' && a.reg;
            const clickCls  = clickable ? ' col-sp-row-clickable' : '';
            const clickAttr = clickable
              ? ` onclick="_spOpenPhotos('${esc(a.reg)}','${esc(airport)}','${esc(date)}')"`
              : '';
            if (window.innerWidth < 768) {
              return `<div class="col-sp-row col-sp-row-m${clickCls}"${clickAttr}>
                <div class="col-sp-m-row1"><span class="col-sp-reg">${esc(a.reg)}</span>${badge}</div>
                <div class="col-sp-m-row2">${parts}</div>
                <div class="col-sp-tag-group">${tagHtml}${notesHtml}</div>
              </div>`;
            }
            return `<div class="col-sp-row${clickCls}"${clickAttr}>
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
  return `<div class="col-ex-section"><div class="col-ap-label">${tt(label)}</div><div class="col-ex-pills">${pills||'<span class="col-sp-empty">No data</span>'}</div></div>`;
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
  // "2019-07-07" → "07 Jul '19" (en) / "19/07/07" (zh, matches the Feed's
  // "Spotted" session pills — see the col-ex-pill dateLabel in _loadAircraftDetail).
  const [y, m, d] = (dateStr || '').split('-');
  if (!y) return dateStr;
  if (_lang === 'zh') return `${y.slice(2)}/${m}/${d}`;
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return `${d} ${months[+m-1]} '${y.slice(2)}`;
}

// Same "YY/MM/DD for zh, unchanged English otherwise" idea as _colShortDate,
// but takes a unix ts (Search's last_seen_ts fields) instead of an ISO date
// string — used for Search's flight-card/aircraft-card "last seen" dates.
function _srchShortDate(ts) {
  if (!ts) return '—';
  const d = new Date(ts * 1000);
  if (_lang === 'zh') {
    const y = String(d.getFullYear()).slice(2);
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}/${m}/${day}`;
  }
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
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
            const clickable = _appRole === 'controller';
            const clickAttrs = clickable
              ? ` class="col-ex-pill${hlClass} col-ex-pill-clickable" onclick="_spOpenPhotos('${esc(reg)}','${esc(s.iata)}','${esc(s.date)}')"`
              : ` class="col-ex-pill${hlClass}"`;
            return `<span${clickAttrs}>` +
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

// ── Controller-only session photo preview ──────────────────────────────────
// Read-only by construction: this only ever GETs a thumbnail from the server
// (/api/session-photo-pick, /api/session-photo-thumb) — there is no upload,
// delete, or rename affordance anywhere in this lightbox, and the image
// itself is non-draggable/non-selectable (see .sp-photos-body img CSS) so
// casual right-click/drag isn't a one-click save path either.
// Shows exactly one photo per session: whichever has the "Featured" keyword
// in Lightroom if the session has one, otherwise a random photo from it —
// picked server-side (see _pick_session_photo in web.py), which also returns
// the session's airline/type/manufacturer/notes/tags to overlay on the photo
// (same gradient-fade treatment as the Feed's .sq photo cards).
function _spPhotosOverlay() {
  let el = $('sp-photos-overlay');
  if (el) return el;
  el = document.createElement('div');
  el.id = 'sp-photos-overlay';
  el.className = 'sp-photos-overlay';
  el.innerHTML = `<div class="sp-photos-modal" id="sp-photos-body"></div>`;
  el.addEventListener('click', e => { if (e.target === el) _spClosePhotos(); });
  document.body.appendChild(el);
  return el;
}

function _spClosePhotos() {
  const el = $('sp-photos-overlay');
  if (el) el.classList.remove('open');
}

async function _spOpenPhotos(rego, iata, date) {
  if (_appRole !== 'controller' || !rego || !iata || !date) return;
  const overlay = _spPhotosOverlay();
  const body = $('sp-photos-body');
  body.innerHTML = `<div class="sp-photos-loading">${tt('Loading…')}</div><span class="sp-photo-close" onclick="_spClosePhotos()"></span>`;
  overlay.classList.add('open');
  try {
    const d = await api(`/session-photo-pick?rego=${encodeURIComponent(rego)}&iata=${encodeURIComponent(iata)}&date=${encodeURIComponent(date)}`);
    if (!d.id) {
      body.innerHTML = `<div class="sp-photos-empty">${tt('No photos found.')}</div><span class="sp-photo-close" onclick="_spClosePhotos()"></span>`;
      return;
    }
    const cc = _regoCountryCode(d.registration);
    const flag = cc ? _flag(cc, { h: 13 }) : '';
    const badge = d.manufacturer ? mfrBadge(d.manufacturer) : '';
    const airlineName = (d.airline || '').replace(/\s*\([^)]*\)\s*$/, '').trim();
    const airlineDisp = airlineName ? tExternalName(airlineName) : '';
    const metaLine = [d.aircraft_type, airlineDisp].filter(Boolean).join(' · ');
    const notesHtml = d.notes ? `<span class="sp-photo-notes">${esc(tLiveryName(d.notes))}</span>` : '';
    const tagsHtml = (d.tags || []).map(t => `<span class="col-sp-tag ${_colTagClass(t)}">${esc(tColKeyword(t))}</span>`).join('');
    await _translateNamesForZh([airlineName, d.airport_name].filter(Boolean));
    // Mobile: short IATA code, moved up onto row1 (right-aligned, alongside
    // the manufacturer/type/airline line) — there isn't room on a phone
    // width for a full airport name AND a separate row for it. Desktop:
    // full shortened name — normally on its own row2 next to notes/tags,
    // but if there ARE no notes/tags, row2 would otherwise just be an
    // near-empty line holding only the date pill, so it moves up onto row1
    // there too rather than wasting a whole row on it.
    const isMobile = window.innerWidth < 768;
    const apDisp = isMobile ? d.airport : (d.airport_name ? _airportDisplayName(d.airport_name) : d.airport);
    const dateFlagHtml = d.airport_flag ? `<span class="sp-photo-date-flag">${esc(d.airport_flag)}</span>` : '';
    const dateHtml = `<span class="sp-photo-date">${dateFlagHtml}<span>${esc(apDisp)} · ${esc(_colShortDate(date))}</span></span>`;
    const row1LeftHtml = metaLine ? `<div class="sp-photo-row1-left">${badge}<span>${esc(metaLine)}</span></div>` : '';
    const row2LeftHtml = (notesHtml || tagsHtml) ? `<div class="sp-photo-row2-left">${notesHtml}${tagsHtml}</div>` : '';
    const dateOnRow1 = isMobile || !row2LeftHtml;
    const row1Html = (row1LeftHtml || dateOnRow1)
      ? `<div class="sp-photo-row1">${row1LeftHtml}${dateOnRow1 ? dateHtml : ''}</div>` : '';
    const row2Html = (row2LeftHtml || !dateOnRow1)
      ? `<div class="sp-photo-row2">${row2LeftHtml}${dateOnRow1 ? '' : dateHtml}</div>` : '';
    body.innerHTML = `
      <img class="sp-photo-img" src="/api/session-photo-thumb/${d.id}" alt="" oncontextmenu="return false">
      <span class="sp-photo-close" onclick="_spClosePhotos()"></span>
      <div class="sp-photo-top">
        <span class="sp-photo-rego">${flag}${esc(d.registration)}</span>
      </div>
      <div class="sp-photo-bottom">${row1Html}${row2Html}</div>`;
    if (window.twemoji) twemoji.parse(body, {folder:'svg',ext:'.svg'});
  } catch (_) {
    body.innerHTML = `<div class="sp-photos-empty">${tt('Failed to load photos.')}</div><span class="sp-photo-close" onclick="_spClosePhotos()"></span>`;
  }
}

const REC_START = 5 * 60;   // 05:00 in minutes
const REC_END   = 23 * 60;  // 23:00 in minutes

function _recPct(localMin) {
  const clamped = Math.max(REC_START, Math.min(REC_END, localMin));
  return ((clamped - REC_START) / (REC_END - REC_START) * 100).toFixed(2);
}

function _minToHHMM(min) {
  const h = Math.floor(min / 60), m = min % 60;
  const mStr = m < 10 ? '0' + m : m;
  if (_lang === 'zh') return `${h < 10 ? '0' + h : h}:${mStr}`;
  const ap = h >= 12 ? 'pm' : 'am';
  const h12 = h % 12 || 12;
  return `${h12}:${mStr}${ap}`;
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
  const tz = data.timezone || '';
  const render = d => _renderDayCard(d, tz);
  const cards = data.days.filter(d => d.clusters && d.clusters.length > 0 || d.is_today);
  if (!cards.length) return '<div class="rec-scroll">' + data.days.slice(0,3).map(render).join('') + '</div>';
  return `<div class="rec-scroll">${data.days.map(render).join('')}</div>`;
}

// Wall-clock hour/minute of a given instant (default: now) AT THE AIRPORT's own
// timezone — never the viewing device's local time (a spotter checking a
// different airport's feed from home would otherwise see their own local
// clock, not the airport's).
function _nowHM(tzName, d) {
  d = d || new Date();
  if (!tzName) return { h: d.getHours(), m: d.getMinutes() };
  try {
    const parts = new Intl.DateTimeFormat('en-GB', {
      timeZone: tzName, hour: '2-digit', minute: '2-digit', hour12: false,
    }).formatToParts(d);
    return {
      h: parseInt(parts.find(p => p.type === 'hour').value, 10),
      m: parseInt(parts.find(p => p.type === 'minute').value, 10),
    };
  } catch {
    return { h: d.getHours(), m: d.getMinutes() };
  }
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
  const stPill = st ? `<span style="font-size:9px;font-weight:700;padding:1px 5px;border-radius:10px;background:${stBg};color:${stFg};text-transform:uppercase;letter-spacing:.04em">${esc(tLabel(st))}</span>` : '';

  const sideClass = isArr ? 'rfc-arr' : 'rfc-dep';
  const livery = f.extra_info ? `<span class="rfc-livery-txt">${esc(tLiveryName(f.extra_info))}${icon}</span>` : (icon ? `<span>${icon}</span>` : '');
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
// Mobile's 3-row mini card (rego / chips+type / status+time) is taller than
// desktop's 2-row card, so overlap-avoidance needs more vertical room per card —
// both the per-card height AND the underlying timeline scale need to grow together,
// or the taller mobile card just gets more artificially nudged away from its true
// chronological position instead of actually having room to breathe.
const TIMELINE_SCALE_PX = window.matchMedia('(max-width: 767px)').matches ? 6 : 5;  // px per minute in active segments (base/minimum)
const CARD_H_PX = window.matchMedia('(max-width: 767px)').matches ? 76 : 56;
// Cards whose natural time-based positions are closer than CARD_H_PX get pushed
// apart by _adjustPos to exactly CARD_H_PX — with zero margin that reads as
// touching/overlapping borders even though nothing technically overlaps. This
// adds a small visual breathing gap on top of the hard minimum.
const CARD_GAP_PX = 6;
const LAYOUT_PAD_MINS = 15;
// Two events further apart than this never share an active segment (a compressed
// gap forms between them instead) — used both by _buildLayout and by the density
// check below to avoid mistaking an intentionally-compressed gap for real cramping.
const ACTIVE_NEIGHBOR_MAX_GAP_MINS = COMPRESS_GAP_MINS - 2 * LAYOUT_PAD_MINS;

function _buildLayout(eventMins, scalePxPerMin) {
  const scale = scalePxPerMin || TIMELINE_SCALE_PX;
  const PAD = LAYOUT_PAD_MINS;
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
    if (span > ACTIVE_NEIGHBOR_MAX_GAP_MINS) {
      segs.push({ type: 'gap', startMin: sMin, endMin: eMin, startPx: curPx });
      curPx += COMPRESS_GAP_PX;
    } else {
      const h = span * scale;
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

// A day with many mini-cards packed into a short time window would otherwise stack
// them tightly (only the intentional COMPRESS_GAP squashing keeps the timeline
// short — nothing previously grew the scale to fit a BURST of same-side cards).
// A single day-wide scale multiplier (the first version of this fix) stretched
// the ENTIRE day's timeline to fix one crowded pocket, inflating plenty of
// already-fine spacing along with it. Instead, stretch only the specific
// contiguous run of active segments that's actually crowded: group the base-
// scale segments into runs (broken only by compressed gaps), and for each run,
// compare its natural height against how much room its busiest side (arrivals
// or departures, whichever has more cards in that run) needs at
// DENSITY_TARGET_GAP_PX per card — scaling up only that run if it falls short.
// Must be at least CARD_H_PX + CARD_GAP_PX — the same hard minimum pitch
// _adjustPos enforces between same-side cards below. If this target undershoots
// that floor, _adjustPos ends up doing most of the separation work itself,
// forcibly pushing cards well past the positions this scale factor accounted
// for — the container still grows to fit them (see _maxCardBottom below), but
// the hour-marker labels stay put at their now-stale scaled positions, so the
// axis visibly stops lining up with where the cards actually land.
const DENSITY_TARGET_GAP_PX = CARD_H_PX + CARD_GAP_PX;
function _buildDensityAwareLayout(eventMins, arrMins, depMins) {
  const base = _buildLayout(eventMins, TIMELINE_SCALE_PX);

  const runs = [];
  let cur = null;
  for (const s of base.segs) {
    if (s.type === 'active') {
      if (!cur) { cur = []; runs.push(cur); }
      cur.push(s);
    } else {
      cur = null;
    }
  }
  function countIn(mins, lo, hi) {
    let n = 0;
    for (const m of mins) if (m >= lo && m <= hi) n++;
    return n;
  }
  const factorBySeg = new Map();
  for (const run of runs) {
    // A run spans from PAD before its first event to PAD after its last — but
    // only the CORE span actually containing events is what needs to grow.
    // Stretching the whole run (including the plain padding at its edges) would
    // dilute the factor across space no card sits in, undershooting the target
    // gap right where the cards actually are.
    const runLo = run[0].startMin, runHi = run[run.length - 1].endMin;
    const inRun = [...arrMins, ...depMins].filter(m => m >= runLo && m <= runHi);
    if (!inRun.length) continue;
    const coreLo = Math.min(...inRun), coreHi = Math.max(...inRun);
    const coreSegs = run.filter(s => s.startMin >= coreLo && s.endMin <= coreHi);
    if (!coreSegs.length) continue;
    const naturalH = coreSegs.reduce((sum, s) => sum + s.height, 0);
    const need = Math.max(countIn(arrMins, coreLo, coreHi), countIn(depMins, coreLo, coreHi)) * DENSITY_TARGET_GAP_PX;
    const factor = naturalH > 0 && need > naturalH ? need / naturalH : 1;
    for (const s of coreSegs) factorBySeg.set(s, factor);
  }

  const segs = [];
  let curPx = 0;
  for (const s of base.segs) {
    if (s.type === 'gap') {
      segs.push({ type: 'gap', startMin: s.startMin, endMin: s.endMin, startPx: curPx });
      curPx += COMPRESS_GAP_PX;
    } else {
      const height = s.height * (factorBySeg.get(s) || 1);
      segs.push({ type: 'active', startMin: s.startMin, endMin: s.endMin, startPx: curPx, height });
      curPx += height;
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

function _renderDayCard(day, tzName) {
  const todayCls = day.is_today ? ' rec-today' : '';
  const tomorrowCls = day.is_tomorrow ? ' rec-tomorrow' : '';
  const clusters = day.clusters || [];
  const nowTs    = Math.floor(Date.now() / 1000);
  const sr = day.sunrise_ts || 0;
  const ss = day.sunset_ts  || 0;

  // Weather
  const wc     = day.weather_code || 0;
  const wxIcon = _WX_ICONS[wc] || '🌡';
  const wxDesc = tWx(_WX_CODES[wc]) || '';
  const severe = day.weather_severe;
  const wxStyle= severe ? 'color:var(--danger);font-weight:600' : 'color:var(--dim)';
  const tempRange = day.temp_min != null && day.temp_max != null
    ? `<span class="rdc-weather rdc-temp" style="color:var(--dim)">${day.temp_min}° – ${day.temp_max}°</span>` : '';
  const wxHtml = wxDesc ? `<span class="rdc-weather" style="${wxStyle}">${wxIcon} ${esc(wxDesc)}</span>${tempRange}` : '';

  // Window times with am/pm
  function _toAmPm(min) {
    const h = Math.floor(min / 60), m = min % 60;
    const mStr = m < 10 ? '0'+m : m;
    if (_lang === 'zh') return `${h < 10 ? '0'+h : h}:${mStr}`;
    const ap = h >= 12 ? 'pm' : 'am';
    const h12 = h % 12 || 12;
    return `${h12}:${mStr}${ap}`;
  }

  const primary = clusters[0];
  const winHtml = primary && primary.show_window
    ? `<span class="rdc-window">${tt('Window')}: ${_toAmPm(primary.recommended_start_local_min)} – ${_toAmPm(primary.end_local_min)}</span>`
    : '';

  const primaryDur = primary ? (primary.end_local_min - primary.recommended_start_local_min) : 0;
  const shorterAlts = (primary && primary.show_window && primary.alternative_windows || []).filter(w => {
    const dur = w.end_local_min - w.start_local_min;
    return dur < primaryDur;
  });
  const altHtml = shorterAlts.length
    ? `<span class="rdc-alt">${tt('Alt')}: ${shorterAlts.map(w => {
        const dur = w.end_local_min - w.start_local_min;
        const earlierMins = primary.recommended_start_local_min - w.start_local_min;
        const shorterMins = primaryDur - dur;
        const parts = [];
        if (earlierMins > 0) parts.push(tMinsEarlier(earlierMins));
        if (shorterMins > 0) parts.push(tMinsShorter(shorterMins));
        return `${_toAmPm(w.start_local_min)}–${_toAmPm(w.end_local_min)}${parts.length ? ' (' + parts.join(', ') + ')' : ''}`;
      }).join(window.innerWidth < 768 ? '<br>· ' : ' · ')}</span>`
    : '';

  const totalRegs = day.total_regs || 0;

  const hdr = `<div class="rec-day-hdr">
    <div>
      <div class="rec-d-label">${esc(tRecDayLabel(day.date, day.label))}</div>
      ${winHtml || `<span class="rdc-window" style="color:var(--dim);font-weight:400">${tt('No window')}</span>`}
      ${altHtml}
    </div>
    <div style="text-align:right">
      <div class="rec-d-count">${totalRegs > 0 ? tAircraftN(totalRegs) : ''}</div>
      ${wxHtml}
    </div>
  </div>`;

  if (!clusters.length) {
    return `<div class="rec-day${todayCls}${tomorrowCls}">${hdr}<div class="rec-empty">No activity</div></div>`;
  }

  // Build compressed layout from all event times
  const eventMins = [];
  const arrMins = [], depMins = [];
  for (const cluster of clusters) {
    for (const f of (cluster.flights || [])) {
      if (f.local_min == null) continue;
      eventMins.push(f.local_min);
      (f.side === 'arrival' ? arrMins : depMins).push(f.local_min);
    }
    if (cluster.recommended_start_local_min != null) eventMins.push(cluster.recommended_start_local_min);
    if (cluster.end_local_min != null) eventMins.push(cluster.end_local_min);
  }
  const layout = _buildDensityAwareLayout(eventMins, arrMins, depMins);

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
    const py = layout.toY(_tsToLocalMin(day.sunrise_ts, tzName));
    srLine = `<span class="rec-sun-line" style="top:${py.toFixed(1)}px">${tt('Sunrise')}</span>`;
  }
  if (day.sunset_ts) {
    const py = layout.toY(_tsToLocalMin(day.sunset_ts, tzName));
    ssLine = `<span class="rec-sun-line rec-sun-set" style="top:${py.toFixed(1)}px">${tt('Sunset')}</span>`;
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
      floor = ev.py + CARD_H_PX + CARD_GAP_PX;
    }
  }
  _adjustPos(arrEvts);
  _adjustPos(depEvts);
  // _adjustPos can push the last card(s) on a dense side past what the
  // pre-computed layout height accounted for — make sure the container is
  // always at least as tall as whatever they actually needed, or the last
  // few cards on a busy day render past the timeline's own bottom edge.
  const _maxCardBottom = Math.max(0, ...arrEvts.map(e => e.py), ...depEvts.map(e => e.py)) + CARD_H_PX;
  if (_maxCardBottom > layout.totalPx) {
    // layout.segs (what axisHtml below renders as the visible vertical guideline)
    // was fixed at _buildDensityAwareLayout time and knows nothing about this
    // push — without extending it too, the axis line stops short while cards
    // pushed down by _adjustPos keep going past it. Stretch the last active
    // segment (or append one, if the layout ended on a compressed gap) to
    // close the gap between where the axis ends and where the last card does.
    const extra = _maxCardBottom - layout.totalPx;
    const lastSeg = layout.segs[layout.segs.length - 1];
    if (lastSeg && lastSeg.type === 'active') {
      lastSeg.height += extra;
    } else {
      layout.segs.push({
        type: 'active', startMin: lastSeg ? lastSeg.endMin : 0,
        endMin: lastSeg ? lastSeg.endMin : 0, startPx: layout.totalPx, height: extra,
      });
    }
    layout.totalPx = _maxCardBottom;
  }
  const _pyMap = {};
  for (const ev of arrEvts) _pyMap[ev.f.registration + '_arr_' + (ev.f.ts || 0)] = ev.py;
  for (const ev of depEvts) _pyMap[ev.f.registration + '_dep_' + (ev.f.ts || 0)] = ev.py;

  // Current time line (today only) — built after card layout so overlaps can be checked
  let currentTimeLine = '';
  if (day.is_today) {
    const { h: nowH, m: nowM } = _nowHM(tzName);
    const nowPy = layout.toY(nowH * 60 + nowM);
    const nowStr = `${nowH < 10 ? '0'+nowH : nowH}:${nowM < 10 ? '0'+nowM : nowM}`;
    const HALF = CARD_H_PX / 2;
    const arrOvlp = arrEvts.some(ev => nowPy >= ev.py - HALF && nowPy <= ev.py + HALF);
    const depOvlp = depEvts.some(ev => nowPy >= ev.py - HALF && nowPy <= ev.py + HALF);
    const leftSeg  = arrOvlp ? '' : `<div class="rct-seg rct-left"><span class="rec-current-label">${tt('Now')} ${nowStr}</span></div>`;
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

    // Per-side adjustment: if an out-of-window card visually overlaps the box's
    // current top/bottom span on one side, push that side's border past the
    // card so the line avoids it. Other side stays clean. "Out of window" =
    // local_min outside [ws, we], regardless of f.qualifying.
    //
    // Checks full overlap with [globalTop, globalBot], not just a straddle of
    // one specific edge — same-side overlap-avoidance (_adjustPos) can push a
    // card enough that it lands entirely past the boundary instead of merely
    // straddling it (e.g. a crowded arrival column pushing a card down past a
    // lightly-loaded departure column's card that's chronologically only a
    // minute later), which a straddle-only check would miss entirely.
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
      const cardTop = py - CARD_H_PX / 2, cardBot = py + CARD_H_PX / 2;
      if (cardBot <= globalTop || cardTop >= globalBot) continue;  // no overlap with the box at all
      const distToTop = Math.abs(py - globalTop), distToBot = Math.abs(py - globalBot);
      if (distToTop <= distToBot) {
        if (isLeft) adjLTop = Math.max(adjLTop, cardBot);
        else adjRTop = Math.max(adjRTop, cardBot);
      } else {
        if (isLeft) adjLBot = Math.min(adjLBot, cardTop);
        else adjRBot = Math.min(adjRBot, cardTop);
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
      boxesHtml += `<div class="rec-break-time" style="top:${midPx.toFixed(1)}px">${tBreak(durH, durM)}</div>`;
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
    <span class="rec-col-arr">${tt('Arrivals')}</span>
    <span class="rec-col-dep">${tt('Departures')}</span>
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

  return `<div class="rec-day${todayCls}${tomorrowCls}">${hdr}${colLabels}${body}</div>`;
}

// Helper: convert unix timestamp to minutes-from-midnight IN THE AIRPORT'S OWN
// TIMEZONE, not the viewing device's — same reasoning as _nowHM above.
function _tsToLocalMin(ts, tzName) {
  if (!ts) return 0;
  const { h, m } = _nowHM(tzName, new Date(ts * 1000));
  return h * 60 + m;
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
      if (_lang === 'zh') {
        const str = abs < 60 ? `${abs}秒` : abs < 3600 ? `${Math.round(abs/60)}分钟` : abs < 86400 ? `${Math.round(abs/3600)}小时` : `${Math.round(abs/86400)}天`;
        return diff < 0 ? `${str}前` : `${str}后`;
      }
      const str  = abs < 60 ? `${abs}s` : abs < 3600 ? `${Math.round(abs/60)}m` : abs < 86400 ? `${Math.round(abs/3600)}h` : `${Math.round(abs/86400)}d`;
      return diff < 0 ? `${str} ago` : `in ${str}`;
    }
    function _row(item, subs) {
      const lastStr = item.last_ts ? _rel(item.last_ts, now) : tt('Never');
      const nextStr = item.next_ts
        ? (item.next_ts <= now ? (_lang === 'zh' ? '现在' : 'Now') : _rel(item.next_ts, now))
        : (item.interval ? '—' : (_lang === 'zh' ? '按需' : 'On demand'));
      const tip = item.error ? ` title="${esc(item.error)}"` : '';
      const subHtml = (subs && subs.length)
        ? `<span class="sys-subdep">${subs.map(s => `${_dot(s.ok)} ${esc(s.label)}`).join('&nbsp;&nbsp;&nbsp;')}</span><span></span><span></span><span></span>`
        : '';
      // .sys-time-combo (hidden on desktop, shown on mobile in place of the two separate
      // last/next cells — see the .sys-grid mobile CSS) keeps the two-column desktop
      // layout completely unchanged while giving mobile one "18m ago / in 12m" line instead
      // of two stacked full-width lines.
      return `<span${tip}>${_dot(item.ok)}</span>
              <span class="sys-name"${tip}>${esc(item.name)}</span>
              <span class="sys-time"><span class="sys-time-solo">${lastStr}</span><span class="sys-time-combo">${lastStr} / ${nextStr}</span></span>
              <span class="sys-time">${nextStr}</span>
              <span></span>
              <span class="sys-desc">${esc(item.desc)}</span>
              <span></span><span></span>
              ${subHtml}`;
    }

    if (tasksEl) {
      const header = `<span></span><span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em">${esc(tt('Task'))}</span>
                      <span class="sys-col-header-right" style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">${esc(tt('Last Run'))}</span>
                      <span class="sys-col-header-right" style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">${esc(tt('Next Run'))}</span>
                      <hr class="sys-sep">`;
      const apiByName = name => (d.apis || []).find(a => a.name === name);
      const fr24      = apiByName('FR24 Airport Feed');
      const openMeteo = apiByName('Open-Meteo');
      const logostream= apiByName('Logostream');
      const adsbFi    = apiByName('adsb.fi Military');
      const icaoList  = apiByName('ICAOList (GitHub)');
      // DB Backup and ICAO List Update are server-maintenance internals — Controller-only,
      // same reasoning as the host-machine details in the Server Status card above.
      const _CONTROLLER_ONLY_TASKS = new Set(['DB Backup', 'ICAO List Update']);
      const visibleTasks = d.tasks.filter(item => _appRole === 'controller' || !_CONTROLLER_ONLY_TASKS.has(item.name));
      // Airport Scan / Military Scan are per-airport (each watched airport polls
      // independently, staggered across the interval) — tag the name with the
      // currently selected airport so it's clear this row isn't global.
      const _PER_AIRPORT_TASKS = new Set(['Airport Scan', 'Military Scan']);
      const rows = visibleTasks.map(item => {
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
        const translated = { ...item, name: tSysName(item.name), desc: tSysDesc(item.desc) };
        const displayItem = (_PER_AIRPORT_TASKS.has(item.name) && _feedAirportIata)
          ? { ...translated, name: `${translated.name} (${_feedAirportIata})` }
          : translated;
        return _row(displayItem, subs);
      });
      tasksEl.innerHTML = `<div class="sys-grid">${header}${rows.join('<hr class="sys-sep">')}</div>`;
    }
    if (apisEl) {
      const header = `<span></span><span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em">${esc(tt('API'))}</span>
                      <span class="sys-col-header-right" style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">${esc(tt('Last Call'))}</span>
                      <span class="sys-col-header-right" style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em;text-align:right">${esc(tt('Next'))}</span>
                      <hr class="sys-sep">`;
      const _PER_AIRPORT_APIS = new Set(['FR24 Airport Feed', 'Open-Meteo', 'adsb.fi Military']);
      const apiRows = d.apis.map(item => {
        const translated = { ...item, name: tSysName(item.name), desc: tSysDesc(item.desc) };
        const displayItem = (_PER_AIRPORT_APIS.has(item.name) && _feedAirportIata)
          ? { ...translated, name: `${translated.name} (${_feedAirportIata})` }
          : translated;
        return _row(displayItem);
      });
      apisEl.innerHTML = `<div class="sys-grid">${header}${apiRows.join('<hr class="sys-sep">')}</div>`;
    }
  } catch(e) {
    if (tasksEl) tasksEl.innerHTML = `<span style="color:var(--danger);font-size:12px">${esc(tt('Failed to load'))}</span>`;
    if (apisEl)  apisEl.innerHTML  = `<span style="color:var(--danger);font-size:12px">${esc(tt('Failed to load'))}</span>`;
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
    if (tzInEl) tzInEl.value = s.effective_tz || s.airport_tz || '';

    function _fmtRuntime(secs) {
      if (!secs && secs !== 0) return '—';
      const d = Math.floor(secs / 86400), h = Math.floor((secs % 86400) / 3600), m = Math.floor((secs % 3600) / 60);
      const units = _lang === 'zh' ? ['天', '小时', '分钟'] : ['d', 'h', 'm'];
      const parts = [];
      if (d) parts.push(`${d}${units[0]}`);
      if (h || d) parts.push(`${h}${units[1]}`);
      parts.push(`${m}${units[2]}`);
      return parts.join(_lang === 'zh' ? '' : ' ');
    }

    // Host-machine identity/network details (name, OS, arch, connection type) are
    // Controller-only — a Pilot/Passenger has no business seeing what hardware the
    // server happens to run on.
    const _CONTROLLER_ONLY_STATUS_ROWS = new Set(['Server Name', 'Operating System', 'Architecture', 'Connection']);
    const statusRows = [
      { dot: true,  name: 'Status',           value: tt('Running') },
      { dot: false, name: 'Current Time',     value: s.current_time ? `${esc(s.current_time)} <span style="color:var(--dim);font-size:11px">${esc(s.effective_tz || '')}</span>` : '—' },
      { dot: false, name: 'Server Name',      value: s.hostname ? esc(s.hostname) : '—' },
      { dot: false, name: 'Operating System', value: s.os   ? esc(s.os)   : '—' },
      { dot: false, name: 'Architecture',     value: s.arch ? esc(s.arch) : '—' },
      { dot: false, name: 'Connection',       value: s.connection ? esc(s.connection) : '—' },
      { dot: false, name: 'Runtime',          value: _fmtRuntime(s.runtime_secs) },
    ].filter(r => _appRole === 'controller' || !_CONTROLLER_ONLY_STATUS_ROWS.has(r.name));
    grid.innerHTML = `<div class="sys-status-grid">${statusRows.map(r =>
      `<span class="sys-dot ${r.dot ? 'ok' : ''}" style="${r.dot ? '' : 'visibility:hidden'}"></span>
       <span class="sys-name">${esc(tt(r.name))}</span>
       <span class="sys-time">${r.value}</span>`
    ).join('<hr class="sys-sep">')}</div>`;
  } catch (e) {
    const grid = $('info-status-grid');
    if (grid) grid.innerHTML = `<span style="color:var(--danger);font-size:12px">${esc(tt('Unreachable'))}</span>`;
  }
}

function switchSubtab(name) {
  document.querySelectorAll('#tab-settings .srch-subtab').forEach(b => b.classList.toggle('active', b.dataset.subtab === name));
  document.querySelectorAll('.set-subtab-page').forEach(p => p.classList.toggle('hidden', p.id !== 'subtab-' + name));
  if (name === 'airports') { apLoad(); atLoad(); stTagsLoad(); }
  if (name === 'logs') logsLoad();
  if (name === 'notification') loadNotificationSettings();
}

// ── Push Notifications (universal — every role, own settings) ───────────────

function _urlBase64ToUint8Array(base64String) {
  const padding = '='.repeat((4 - base64String.length % 4) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; i++) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

async function loadNotificationSettings() {
  const statusEl = $('push-notif-status');
  const btn = $('push-notif-toggle-btn');
  if (!statusEl || !btn) return;
  // Preferences are a server-side setting independent of whether THIS device
  // can actually subscribe — load them regardless, even on a browser/context
  // (headless testing, desktop, non-secure origin) that can't itself receive
  // pushes, so the early return below for "not supported" doesn't also hide
  // the per-type toggle list.
  _loadPushNotifPrefs();
  _loadSpottingReminderPrefs();
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
    statusEl.textContent = tt('Push notifications are not supported in this browser.');
    btn.style.display = 'none';
    return;
  }
  try {
    const reg = await navigator.serviceWorker.ready;
    const sub = await reg.pushManager.getSubscription();
    if (sub) {
      statusEl.textContent = tt('Notifications are enabled on this device.');
      btn.textContent = tt('Disable Notifications');
      btn.dataset.enabled = '1';
    } else {
      statusEl.textContent = tt('Notifications are off on this device.');
      btn.textContent = tt('Enable Notifications');
      btn.dataset.enabled = '0';
    }
  } catch (e) {
    statusEl.textContent = tt('Could not check notification status.');
  }
}

// Friendly labels match monitor.py's _PUSH_TITLE_LABELS — shown as the
// per-type toggle list, not the raw notif_type string. Spotting Reminder is
// deliberately excluded here — it gets its own "Enable" checkbox inside the
// Spotting Reminder card instead of sitting in this generic filter list.
const _PUSH_NOTIF_TYPE_LABELS = {
  'Special Livery':          'Special Livery',
  'Watchlist Registration':  'Registration Watchlist',
  'Watchlist Aircraft Type': 'Aircraft Type Watchlist',
  'Watchlist Airline':       'Airline Watchlist',
  'Rare Plane/Airline':      'Rare Plane/Airline',
  'Military':                'Military Aircraft',
};

async function _loadPushNotifPrefs() {
  const el = $('push-notif-prefs-list');
  if (!el) return;
  try {
    const prefs = await api('/push/notification-prefs');
    el.innerHTML = Object.entries(_PUSH_NOTIF_TYPE_LABELS).map(([type, label]) => `
      <div class="setting-row">
        <span class="setting-label"><span class="setting-key">${esc(tt(label))}</span></span>
        <input type="checkbox" ${prefs[type] !== false ? 'checked' : ''}
               onchange="_togglePushNotifTypePref('${esc(type)}', this.checked)"
               style="width:18px;height:18px;cursor:pointer">
      </div>
    `).join('');
    const srToggle = $('sr-enabled-toggle');
    if (srToggle) srToggle.checked = prefs['Spotting Reminder'] !== false;
  } catch (e) {
    el.innerHTML = `<span style="font-size:12px;color:var(--dim)">${esc(tt('Could not load preferences.'))}</span>`;
  }
}

async function _togglePushNotifTypePref(notifType, enabled) {
  try {
    await api('/push/notification-prefs', {
      method: 'POST',
      body: JSON.stringify({ notif_type: notifType, enabled }),
    });
  } catch (e) {
    toast('Failed to save preference: ' + (e.message || e));
  }
}

// Spotting Reminder's extra settings (time / weather gate / min aircraft) —
// shown under its row in the per-type toggle list once populated by
// _loadSpottingReminderPrefs (called alongside _loadPushNotifPrefs, same
// server-side-setting-independent-of-local-push-support reasoning).
async function _loadSpottingReminderPrefs() {
  const el = $('spotting-reminder-prefs');
  if (!el) return;
  try {
    const p = await api('/push/spotting-reminder-prefs');
    $('sr-send-time').value = p.send_time || '18:00';
    $('sr-weather-gate').value = p.weather_gate || 'none';
    $('sr-min-aircraft').value = p.min_aircraft || 2;
    el.classList.remove('hidden');
  } catch (e) {
    el.classList.add('hidden');
  }
}

async function _saveSpottingReminderPrefs() {
  const send_time = $('sr-send-time').value;
  const weather_gate = $('sr-weather-gate').value;
  const min_aircraft = Math.max(2, parseInt($('sr-min-aircraft').value, 10) || 2);
  $('sr-min-aircraft').value = min_aircraft;
  try {
    await api('/push/spotting-reminder-prefs', {
      method: 'POST',
      body: JSON.stringify({ send_time, weather_gate, min_aircraft }),
    });
    toast('Spotting reminder settings saved');
  } catch (e) {
    toast('Failed to save: ' + (e.message || e));
  }
}

// Shared by the manual Settings toggle and the auto-subscribe-on-install flow
// below — requests permission, subscribes, and registers with the backend.
// Returns true on success, false if permission was denied or anything failed
// (caller decides how to report that; the auto-subscribe path stays silent).
async function _pushSubscribe() {
  const reg = await navigator.serviceWorker.ready;
  const perm = await Notification.requestPermission();
  if (perm !== 'granted') return false;
  const { key } = await api('/push/vapid-public-key');
  if (!key) return false;
  const sub = await reg.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: _urlBase64ToUint8Array(key),
  });
  const subJson = sub.toJSON();
  await api('/push/subscribe', {
    method: 'POST',
    body: JSON.stringify({ endpoint: subJson.endpoint, keys: subJson.keys }),
  });
  return true;
}

async function togglePushNotifications() {
  const statusEl = $('push-notif-status');
  const btn = $('push-notif-toggle-btn');
  if (!statusEl || !btn) return;
  try {
    if (btn.dataset.enabled === '1') {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        try { await api('/push/unsubscribe', { method: 'DELETE', body: JSON.stringify({ endpoint: sub.endpoint }) }); } catch {}
        await sub.unsubscribe();
      }
      await loadNotificationSettings();
      toast('Notifications disabled');
      return;
    }
    const ok = await _pushSubscribe();
    if (!ok) { statusEl.textContent = 'Notification permission was denied.'; return; }
    await loadNotificationSettings();
    toast('Notifications enabled');
  } catch (e) {
    statusEl.textContent = 'Failed: ' + (e.message || e);
  }
}

// Auto-enable on first login (every role — push notifications are now a
// universal per-user feature) — the user shouldn't have to find the Settings
// toggle manually just to get notifications working. Must run synchronously
// inside the login button's click-derived task, BEFORE the post-login
// location.reload() — iOS Safari only shows the Notification.requestPermission()
// system prompt when called within a live user-gesture window; calling it
// later during page boot (no gesture behind it) silently no-ops. Only ever
// attempted ONCE per device (localStorage flag) — after that, the Settings
// toggle is the sole source of truth, whether the user turns it off or
// denies the prompt.
async function _promptPushOnLogin() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
  if (localStorage.getItem('push-auto-subscribe-attempted')) return;
  localStorage.setItem('push-auto-subscribe-attempted', '1');
  try {
    const reg = await navigator.serviceWorker.ready;
    const existing = await reg.pushManager.getSubscription();
    if (existing) return;
    await _pushSubscribe();
  } catch (e) { /* silent — don't disrupt login, Settings toggle remains available */ }
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
    if (!tags.length) { el.innerHTML = `<span style="color:var(--dim);font-size:12px">${esc(tt('No tags found in catalog'))}</span>`; return; }
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
    lbl.textContent = tt('Refresh Collection');
  } else {
    btn.onclick = () => forceCheck();
    lbl.textContent = tt('Refresh Feed');
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
  if (name === 'route') $('srch-rt-status').textContent = tt('Enter a flight number or select a filter.');
}

async function _srchInit() {
  if (_srchInited) return;
  _srchInited = true;
  $('srch-status').textContent = tt('Loading filters…');
  try {
    const d = await api('/search/autocomplete');

    // Manufacturers — from aircraft_manufacturer LR property
    await _translateNamesForZh(d.manufacturers || []);
    _srchDDSetOptions('srch-dd-cat-mfr', (d.manufacturers || []).map(m => ({ value: m, label: _mfrDisp(m) })));

    // Types — show as "B789 (Boeing)" style
    const typeOpts = (d.types || []).map(t => ({
      value: t.manufacturer ? `${t.value} (${t.manufacturer})` : t.value,
      label: t.manufacturer ? `${t.value} (${_mfrDisp(t.manufacturer)})` : t.value,
    }));
    _srchDDSetOptions('srch-dd-cat-type', typeOpts);

    // Airlines — display without parenthetical code, keep full value for API matching
    await _translateNamesForZh((d.airlines || []).map(a => a.value.replace(/\s*\([^)]*\)\s*$/, '').trim()));
    _srchDDSetOptions('srch-dd-cat-airline', (d.airlines || []).map(a => ({
      value: a.value,
      label: tExternalName(a.value.replace(/\s*\([^)]*\)\s*$/, '').trim()),
    })));

    // Airports — show shortened full name, keep IATA as value for API matching
    await _translateNamesForZh((d.airports || []).map(ap => ap.full_name).filter(Boolean));
    (d.airports || []).forEach(ap => {
      _srchCatApNames[ap.iata] = _airportDisplayName(ap.full_name || '') || ap.iata;
    });
    _srchDDSetOptions('srch-dd-cat-airport', (d.airports || []).map(ap => ({
      value: ap.iata,
      label: `${ap.iata} · ${_srchCatApNames[ap.iata]}`,
    })));

    // Keywords
    _srchDDSetOptions('srch-dd-cat-keyword', d.keywords || []);

    $('srch-status').textContent = tt('Enter a registration or select a filter.');
  } catch (e) {
    $('srch-status').textContent = tt('Failed to load catalogue filters.');
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
  $('srch-status').textContent = tt('Enter a registration or select a filter.');
  _srchSyncClearVisibility();
}

// On mobile, editing a filter (typing, picking a dropdown option) must NOT
// auto-run the search — as soon as results render, .srch-has-results collapses
// the filter bar down to just the Clear button (see the CSS rule by that name),
// which made it impossible to open a second dropdown after the first one had
// already produced results. Auto-run stays live on desktop, where there's no
// such collapse and results updating as you type is the whole point. `immediate`
// (Enter key, the mobile Search button, subtab/language switch) always bypasses
// this gate — those are explicit "run now" requests, not incidental edits.
function _srchRun(immediate) {
  _srchSyncClearVisibility();
  clearTimeout(_srchTimer);
  if (!immediate && window.innerWidth < 768) return;
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
    $('srch-status').textContent = tt('Enter a registration or select a filter.');
    return;
  }

  $('srch-status').textContent = tt('Searching…');
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
      ? tAircraftN(regs.length)
      : tt('No results.');

    await _translateNamesForZh(regs.map(r => (r.airline || '').replace(/\s*\([^)]*\)\s*$/, '').trim()).filter(Boolean));

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
        const kwPills  = s.keywords.map(k => `<span class="col-sp-tag ${_colTagClass(k)}">${esc(tColKeyword(k))}</span>`).join('');
        const notesHtml = s.notes ? `<span style="font-size:11px;color:var(--dim);font-style:italic;white-space:nowrap;flex-shrink:0">${esc(tLiveryName(s.notes))}</span>` : '';
        const dateDisp = _srchCatDate(s.date);
        const clickable = _appRole === 'controller' && s.airport && s.date;
        const clickCls  = clickable ? ' srch-fl-row-clickable' : '';
        const clickAttr = clickable
          ? ` onclick="_spOpenPhotos('${esc(r.reg)}','${esc(s.airport)}','${esc(s.date)}')"`
          : '';
        if (window.innerWidth < 768) {
          const hasKw = kwPills.length > 0;
          return `<div class="srch-fl-row srch-fl-row-m${clickCls}"${clickAttr}>
            <div class="srch-fl-m-row1">
              <span class="srch-fl-date">${esc(dateDisp)}</span>
              ${aflag}<span>${esc(s.airport)}</span>
            </div>
            ${hasKw ? `<div class="srch-fl-m-row2">${kwPills}${notesHtml}</div>` : ''}
          </div>`;
        }
        return `<div class="srch-fl-row${clickCls}" style="display:flex;gap:8px;align-items:center"${clickAttr}>
          <span class="srch-fl-date" style="flex-shrink:0;width:90px">${esc(dateDisp)}</span>
          <span class="srch-fl-fn srch-cat-ap" style="display:inline-flex;align-items:center;gap:5px;white-space:nowrap;flex-shrink:0">${aflag}${esc(apName)}</span>
          <span class="srch-fl-route" style="flex:1">${tPhotosN(s.photos)}</span>
          ${notesHtml}
          ${kwPills ? `<span style="display:inline-flex;align-items:center;gap:3px;flex-shrink:0">${kwPills}</span>` : ''}
        </div>`;
      }).join('');
      const sessionPill = `<span style="display:inline-flex;align-items:center;gap:5px;background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:2px 10px;font-size:11px;white-space:nowrap;flex-shrink:0"><span style="color:var(--dim);text-transform:uppercase;letter-spacing:.05em;font-size:10px">${tt('Sessions')}</span><span style="font-weight:600">${r.sessions.length}</span></span>`;
      const airlineHtml = airlineName ? `<span style="font-size:12px;color:var(--dim)"><span data-ext-name="${esc(airlineName)}">${esc(tExternalName(airlineName))}</span>${r.aircraft_type ? `<span style="margin:0 4px;opacity:.4">·</span>${esc(r.aircraft_type)}` : ''}</span>` : '';
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
    $('srch-status').textContent = tt('Search failed.');
  }
}

// ── Route search ─────────────────────────────────────────────────────────────
let _srchRtTimer = null;

function _srchRtClear() {
  $('srch-rt-fn').value = '';
  ['srch-dd-rt-origin','srch-dd-rt-dest','srch-dd-rt-airline'].forEach(id => { if (_srchDDs[id]) _srchDDClear(id); });
  _srchRtMirror(null);
  $('srch-rt-results').innerHTML = '';
  $('srch-rt-status').textContent = tt('Enter a flight number or select a filter.');
  _srchSyncClearVisibility();
}

let _srchRtHomeLabel = '';

// /api/search/route-filters bakes "{IATA} · {short name}" into one string
// server-side (both the filter value AND display label) — split it back out
// so the name half can be translated while the value (used for API matching)
// stays exactly as the server sent it.
function _srchRtParseIataLabel(s) {
  const m = (s || '').match(/^([A-Z0-9]{3,4})\s*·\s*(.+)$/);
  return m ? { iata: m[1], name: m[2] } : null;
}
function _srchRtDispLabel(x) {
  return x.p ? `${x.p.iata} · ${_lang === 'zh' ? _cityNameZh(tExternalName(x.p.name)) : x.p.name}` : x.raw;
}
async function _srchRtLoadFilters() {
  try {
    const d = await api('/search/route-filters');
    const home    = { raw: d.home || '', p: _srchRtParseIataLabel(d.home || '') };
    const origins = (d.origins || []).map(s => ({ raw: s, p: _srchRtParseIataLabel(s) }));
    const dests   = (d.dests   || []).map(s => ({ raw: s, p: _srchRtParseIataLabel(s) }));
    const names = [...origins, ...dests, home].filter(x => x.p).map(x => x.p.name);
    await _translateNamesForZh([...names, ...(d.airlines || [])]);
    _srchRtHomeLabel = _srchRtDispLabel(home);
    _srchDDSetOptions('srch-dd-rt-origin',  origins.map(x => ({ value: x.raw, label: _srchRtDispLabel(x) })));
    _srchDDSetOptions('srch-dd-rt-dest',    dests.map(x => ({ value: x.raw, label: _srchRtDispLabel(x) })));
    _srchDDSetOptions('srch-dd-rt-airline', (d.airlines || []).map(a => ({ value: a, label: tExternalName(a) })));
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
  if (!immediate && window.innerWidth < 768) return;
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
    $('srch-rt-status').textContent = tt('Enter a flight number or select a filter.');
    return;
  }
  $('srch-rt-status').textContent = tt('Searching…');
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
    await _translateNamesForZh(groups.flatMap(g => [g.origin_name, g.dest_name, g.airport_name, g.airline]).filter(Boolean));

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
        const home = esc(_airportDisplayName(g.airport_name) || g.airport_iata);
        if (g.origin_iata) { const o = esc(_airportDisplayName(g.origin_name) || g.origin_iata); return `${o} → ${home}`; }
        if (g.dest_iata)   { const d = esc(_airportDisplayName(g.dest_name)   || g.dest_iata);   return `${home} → ${d}`; }
        return home;
      })();
      const airlineDisp = g.airline ? `<span data-ext-name="${esc(g.airline)}">${esc(tExternalName(g.airline))}</span>` : '';
      const subTxt = [airlineDisp, routeTxt].filter(Boolean).join('<span style="margin:0 5px;opacity:.4">·</span>');

      const totalCount = g.types.reduce((s, t) => s + (t.count || 0), 0);
      const rawPcts = g.types.map(t => totalCount > 0 ? (t.count / totalCount) * 100 : 0);
      const minPct = Math.min(...rawPcts), maxPct = Math.max(...rawPcts);
      let cumOffset = 0;
      const rows = g.types.map((t, ti) => {
        const mfr = t.aircraft_type ? _deriveManufacturerFromType(t.aircraft_type) : '';
        const badge = mfr ? mfrBadge(mfr) : '';
        const lastDt = _srchShortDate(t.last_seen_ts);
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
          <span class="srch-fl-date"><span style="color:var(--dim);text-transform:uppercase;font-size:10px;letter-spacing:.04em">${tt('Last Seen')}</span> ${esc(lastDt)}</span>
          <span class="srch-fl-status" style="color:var(--dim);text-align:right">${pct}%</span>
        </div>`;
      }).join('');
      return `<div class="srch-fl-card">
        <div class="srch-fl-header">
          <span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${logo}</span>
          <span class="srch-fl-rego"><a href="${_fr24FlightUrl(g.fn)}" target="_blank" style="color:inherit;text-decoration:none">${esc(g.fn)}</a></span>
          <span style="font-size:12px;color:var(--dim)">${subTxt}</span>
        </div>
        <div class="srch-fl-rows">
          <div style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 0">${tt('Equipment History')}</div>
          ${rows}
        </div>
      </div>`;
    }));
  } catch (e) {
    $('srch-rt-status').textContent = tt('Search failed.');
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
  return `<span style="display:inline-flex;align-items:center;gap:5px;${style};border-radius:10px;padding:2px 10px;font-size:11px;white-space:nowrap"><span style="color:${labelCol};text-transform:uppercase;letter-spacing:.05em;font-size:10px">${tt('Last Seen')}</span><span style="font-weight:600">${esc(dateStr)}</span></span>`;
}

function _srchFlClear() {
  $('srch-fl-rego').value = '';
  ['srch-dd-mfr','srch-dd-airline','srch-dd-type'].forEach(id => { if (_srchDDs[id]) _srchDDClear(id); });
  _srchFlData = null;
  $('srch-fl-results').innerHTML = '';
  $('srch-fl-status').textContent = tt('Enter a registration or select a filter.');
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
  list.innerHTML = `<div class="srch-dd-opt srch-dd-clear" data-val="" onclick="_srchDDClear('${id}')">${tt('Clear all')}</div>` +
    options.map(o => {
      const val = isObj ? o.value : o;
      const lbl = isObj ? o.label : o;
      return `<div class="srch-dd-opt${dd.values.has(val) ? ' selected' : ''}" data-val="${esc(val)}" onclick="_srchDDToggleOpt('${id}', '${esc(val)}')">${esc(lbl)}</div>`;
    }).join('');
}

async function _srchFlLoadFilters() {
  try {
    const d = await api('/search/flight-filters');
    await _translateNamesForZh([...(d.manufacturers || []), ...(d.airlines || [])]);
    _srchDDSetOptions('srch-dd-mfr',     (d.manufacturers || []).map(m => ({ value: m, label: _mfrDisp(m) })));
    _srchDDSetOptions('srch-dd-airline', (d.airlines || []).map(a => ({ value: a, label: tExternalName(a) })));
    _srchDDSetOptions('srch-dd-type',    d.types          || []);
  } catch (_) {}
}

function _srchFlFilter(immediate) {
  if (!immediate && window.innerWidth < 768) { _srchSyncClearVisibility(); return; }
  if (_srchFlData === null) { _srchFlRun(true); return; }
  const mfrs    = _srchDDs['srch-dd-mfr']?.values;
  const airlines = _srchDDs['srch-dd-airline']?.values;
  const types   = _srchDDs['srch-dd-type']?.values;
  const matchSet = (set, val) => !set?.size || [...set].some(s => (val || '').toLowerCase().includes(s.toLowerCase()));
  const filtered = _srchFlData.filter(c =>
    matchSet(mfrs, c.mfr) && matchSet(airlines, c.airline) && matchSet(types, c.type)
  );
  $('srch-fl-status').textContent = tAircraftN(filtered.length);
  $('srch-fl-results').innerHTML = _srchCols(filtered.map(c => c.html));
}

function _srchFlRun(immediate) {
  _srchSyncClearVisibility();
  _srchFlData = null;
  clearTimeout(_srchFlTimer);
  if (!immediate && window.innerWidth < 768) return;
  _srchFlTimer = setTimeout(_srchFlExec, immediate ? 0 : 400);
}

async function _srchFlExec() {
  const rego = ($('srch-fl-rego').value || '').trim();
  const hasFilter = ['srch-dd-mfr','srch-dd-airline','srch-dd-type'].some(id => _srchDDs[id]?.values?.size);
  if (!rego && !hasFilter) {
    $('srch-fl-results').innerHTML = '';
    $('srch-fl-status').textContent = tt('Enter a registration or select a filter.');
    return;
  }
  $('srch-fl-status').textContent = tt('Searching…');
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
      ? tAircraftN(total)
      : tt('No results.');

    const sightingCards = sightingSorted.map(s => {
      const flag = (s.airline_icao || s.airline) ? _srchLogoWithFallback(s.airline_icao || '', s.airline, 20, '') : '';
      const badge = s.manufacturer ? mfrBadge(s.manufacturer) : '';
      const lastDt = _srchShortDate(s.last_seen_ts);
      const airlineTxt = s.airline
        ? `<span style="font-size:12px;color:var(--dim)"><span data-ext-name="${esc(s.airline)}">${esc(tExternalName(s.airline))}</span>${s.aircraft_type ? `<span style="margin:0 4px;opacity:.4">·</span>${esc(s.aircraft_type)}` : ''}</span>`
        : '';
      const sLastSeenPill = _srchLastSeenPill(s.last_seen_ts, lastDt);
      return `<div class="srch-fl-card">
        <div class="srch-fl-header">
          <span class="srch-fl-rego"><span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${flag || ''}</span><a href="${_fr24AircraftUrl(s.registration)}" target="_blank" style="color:inherit;text-decoration:none">${esc(s.registration)}</a></span>
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
      const lastSeenDt = g.last_seen_ts ? _srchShortDate(g.last_seen_ts) : null;
      const lastSeenPill = lastSeenDt ? _srchLastSeenPill(g.last_seen_ts, lastSeenDt) : '';
      const nowTs = Math.floor(Date.now() / 1000);
      const pastFlights = g.flights.filter(f => f.arrival_ts && f.arrival_ts <= nowTs);
      const chips = g.flights[0] ? (g.flights[0].notif_types || []).map(t =>
        `<span class="chip ${chipClass(t)}" style="font-size:9px;height:16px;padding:0 4px">${chipLabel(t)}</span>`).join('') : '';
      const rows = pastFlights.map(f => {
        const dateStr = _srchShortDate(f.arrival_ts);
        const originName = window.innerWidth < 768
          ? (f.origin_iata || f.origin_name || '—')
          : (f.origin_name || f.origin_iata || '—');
        const originCc = f.origin_country_code || _airportCountry(f.origin_iata || '');
        const originFlag = originCc ? _flag(originCc, { h: 11 }) : '';
        return `<div class="srch-fl-row">
          <span class="srch-fl-date">${esc(dateStr)}</span>
          <span class="srch-fl-fn">${esc(f.flight_number)}</span>
          <span class="srch-fl-route" style="display:inline-flex;align-items:center;gap:5px;overflow:hidden"><span style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em;flex-shrink:0">${tt('From')}</span>${originFlag}<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap" data-ext-name="${esc(originName)}">${esc(tExternalName(originName))}</span></span>
        </div>`;
      }).join('') || `<div style="font-size:11px;color:var(--dim);padding:6px 0">${tt('No arrivals in the past 30 days')}</div>`;
      const note = `<div style="font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;padding:0 0 6px 0">${tt('Arrivals · past 30 days')}</div>`;
      return `<div class="srch-fl-card">
        <div class="srch-fl-header">
          <span class="srch-fl-rego"><span style="width:40px;display:inline-flex;align-items:center;justify-content:center;flex-shrink:0">${flag || ''}</span><a href="${_fr24AircraftUrl(g.reg)}" target="_blank" style="color:inherit;text-decoration:none">${esc(g.reg)}</a></span>
          ${badge}
          ${airlineName ? `<span style="font-size:12px;color:var(--dim)"><span data-ext-name="${esc(airlineName)}">${esc(tExternalName(airlineName))}</span>${acType ? `<span style="margin:0 4px;opacity:.4">·</span>${esc(acType)}` : ''}</span>` : ''}
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

    _srchFlFilter(true);
    const _names = [
      ...sightingSorted.map(s => s.airline),
      ...regs.map(g => g.flights[0] ? _parseDetail(g.flights[0].detail || '').airline : ''),
      ...regs.flatMap(g => g.flights.map(f => f.origin_name)),
    ].filter(Boolean);
    _translateNamesForZh(_names);
  } catch (e) {
    console.error('[srch-fl]', e);
    $('srch-fl-status').textContent = tt('Search failed.') + ' ' + e.message;
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
