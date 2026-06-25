const DATA_URL = './fishing-spots.json';
const DEFAULT_CENTER = [114.3055, 30.5928];
const SCORE_BANDS = [
  { min: 0.75, label: '优质 0.75–1.00', color: '#0f9f6e', text: '优' },
  { min: 0.6, label: '良好 0.60–0.74', color: '#2f7ed8', text: '良' },
  { min: 0.4, label: '一般 0.40–0.59', color: '#f59e0b', text: '中' },
  { min: 0, label: '待观察 0.00–0.39', color: '#ef4444', text: '低' },
];
const UNKNOWN_SCORE_BAND = { label: '未评分', color: '#64748b', text: '?' };
const iconCache = new Map();

const state = {
  data: null,
  spots: [],
  filtered: [],
  map: null,
  markers: [],
  mapType: 'normal',
};

const $ = (id) => document.getElementById(id);
const summary = $('summary');
const list = $('spotList');
const emptyState = $('emptyState');

function getTk() {
  const params = new URLSearchParams(location.search);
  return params.get('tk') || localStorage.getItem('TIANDITU_TK') || '';
}

function loadTianditu(tk) {
  return new Promise((resolve, reject) => {
    if (window.T) return resolve();
    const script = document.createElement('script');
    script.src = `https://api.tianditu.gov.cn/api?v=4.0&tk=${encodeURIComponent(tk)}`;
    script.onload = resolve;
    script.onerror = () => reject(new Error('天地图 JS API 加载失败，请检查 TK 或网络'));
    document.head.appendChild(script);
  });
}

async function loadData() {
  const res = await fetch(DATA_URL, { cache: 'no-store' });
  if (!res.ok) throw new Error(`钓点数据加载失败：${res.status}`);
  state.data = await res.json();
  state.spots = state.data.features.map((f) => ({
    ...f.properties,
    lng: f.geometry.coordinates[0],
    lat: f.geometry.coordinates[1],
  }));
  state.filtered = [...state.spots];
  buildFishFilter();
  buildMonthFilter();
  updateSummary();
  renderList();
}

function buildFishFilter() {
  const select = $('fishFilter');
  if (!select) return;
  const fish = new Set();
  state.spots.forEach((s) => (s.fish_species || []).forEach((name) => fish.add(name)));
  [...fish].sort().forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  });
}

function getPublishMonth(spot) {
  const match = String(spot.publish_time || '').match(/^(\d{4})-(\d{2})/);
  return match ? match[2] : '';
}

function formatMonth(month) {
  if (!month) return '未注明月份';
  return `${Number(month)}月`;
}

function buildMonthFilter() {
  const select = $('monthFilter');
  if (!select) return;
  const counts = new Map();
  state.spots.forEach((spot) => {
    const month = getPublishMonth(spot);
    counts.set(month, (counts.get(month) || 0) + 1);
  });
  [...counts.keys()].sort((a, b) => {
    if (!a) return 1;
    if (!b) return -1;
    return Number(a) - Number(b);
  }).forEach((month) => {
    const option = document.createElement('option');
    option.value = month || '__unknown__';
    option.textContent = `${formatMonth(month)}（${counts.get(month)}）`;
    select.appendChild(option);
  });
}

function updateSummary() {
  const total = state.spots.length;
  const visible = state.filtered.length;
  const generated = state.data?.generated_at ? `，数据生成：${state.data.generated_at}` : '';
  summary.textContent = `共 ${total} 个入库钓点，当前显示 ${visible} 个${generated}`;
}

function getScoreBand(score) {
  const value = Number(score);
  if (!Number.isFinite(value)) return UNKNOWN_SCORE_BAND;
  return SCORE_BANDS.find((band) => value >= band.min) || UNKNOWN_SCORE_BAND;
}

function getScoreIcon(spot) {
  const band = getScoreBand(spot.quality_score);
  if (iconCache.has(band.label)) return iconCache.get(band.label);
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="36" height="46" viewBox="0 0 36 46">
    <path d="M18 45S4 28.9 4 16.9C4 7.6 10.3 2 18 2s14 5.6 14 14.9C32 28.9 18 45 18 45Z" fill="${band.color}" stroke="#fff" stroke-width="3"/>
    <circle cx="18" cy="17" r="8.2" fill="rgba(255,255,255,.92)"/>
    <text x="18" y="20.7" text-anchor="middle" font-family="Arial, sans-serif" font-size="9" font-weight="700" fill="${band.color}">${band.text}</text>
  </svg>`;
  const icon = new T.Icon({
    iconUrl: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(svg)}`,
    iconSize: new T.Point(36, 46),
    iconAnchor: new T.Point(18, 45),
  });
  iconCache.set(band.label, icon);
  return icon;
}

function renderScoreLegend() {
  const legend = $('scoreLegend');
  if (!legend) return;
  legend.innerHTML = [...SCORE_BANDS, UNKNOWN_SCORE_BAND].map((band) => (
    `<span class="legend-item"><i style="--pin-color: ${band.color}"></i>${escapeHtml(band.label)}</span>`
  )).join('');
}

function getCommentKeywords(spot) {
  const seen = new Set();
  return (spot.comment_keywords || [])
    .map((item) => String(item?.keyword || '').trim())
    .filter((keyword) => {
      if (!keyword || seen.has(keyword)) return false;
      seen.add(keyword);
      return true;
    });
}

function initMap() {
  state.map = new T.Map('map');
  state.map.centerAndZoom(new T.LngLat(DEFAULT_CENTER[0], DEFAULT_CENTER[1]), 10);
  state.map.enableScrollWheelZoom();
  try {
    state.map.addControl(new T.Control.Zoom());
    state.map.addControl(new T.Control.Scale());
  } catch (_) {}
  emptyState.style.display = 'none';
  renderMarkers();
  fitAll();
}

function markerHtml(spot) {
  const band = getScoreBand(spot.quality_score);
  const commentKeywords = getCommentKeywords(spot);
  const commentKeywordsHtml = commentKeywords.length
    ? `<p><b>评论关键词：</b></p><div class="tags">${commentKeywords.map((keyword) => `<span class="tag gray">${escapeHtml(keyword)}</span>`).join('')}</div>`
    : '';
  const fish = (spot.fish_species || []).map((f) => `<span class="tag">${escapeHtml(f)}</span>`).join('') || '<span class="tag gray">鱼种待补充</span>';
  const url = spot.url ? `<p><a href="${escapeAttr(spot.url)}" target="_blank" rel="noreferrer">打开抖音来源</a></p>` : '';
  return `<div class="info">
    <h3>${escapeHtml(spot.place_name)}</h3>
    <p><b>钓点评分：</b><span class="score"><i style="--pin-color: ${band.color}"></i>${spot.quality_score ?? '-'} / ${escapeHtml(band.label)}</span> ${spot.quality_score_source ? `(${escapeHtml(spot.quality_score_source)})` : ''}</p>
    ${commentKeywordsHtml}
    <p><b>鱼种：</b></p><div class="tags">${fish}</div>
    <p><b>视频：</b>${escapeHtml(spot.title || '-')}</p>
    <p><b>作者：</b>${escapeHtml(spot.author || '-')}　<b>发布：</b>${escapeHtml(spot.publish_time || '-')}</p>
    ${url}
  </div>`;
}

function renderMarkers() {
  if (!state.map || !window.T) return;
  state.markers.forEach((m) => state.map.removeOverLay(m));
  state.markers = [];
  state.filtered.forEach((spot) => {
    const point = new T.LngLat(spot.lng, spot.lat);
    let marker;
    try {
      marker = new T.Marker(point, { icon: getScoreIcon(spot) });
    } catch (_) {
      marker = new T.Marker(point);
    }
    marker.addEventListener('click', () => openSpot(spot));
    state.map.addOverLay(marker);
    state.markers.push(marker);
  });
}

function openSpot(spot) {
  if (!state.map || !window.T) return;
  const point = new T.LngLat(spot.lng, spot.lat);
  state.map.panTo(point);
  state.map.openInfoWindow(new T.InfoWindow(markerHtml(spot), { closeOnClick: true }), point);
}

function fitAll() {
  if (!state.map || !state.filtered.length || !window.T) return;
  const points = state.filtered.map((s) => new T.LngLat(s.lng, s.lat));
  try {
    state.map.setViewport(points);
  } catch (_) {
    state.map.centerAndZoom(points[0], 11);
  }
}

function renderList() {
  list.innerHTML = '';
  if (!state.filtered.length) {
    list.innerHTML = '<li class="meta">没有符合筛选条件的钓点。</li>';
    return;
  }
  state.filtered.forEach((spot) => {
    const band = getScoreBand(spot.quality_score);
    const li = document.createElement('li');
    li.className = 'spot-card';
    li.innerHTML = `<div class="spot-title"><span>${escapeHtml(spot.place_name)}</span><span class="score"><i style="--pin-color: ${band.color}"></i>评分 ${spot.quality_score ?? '-'} / 可信度 ${spot.confidence ?? '-'}</span></div>
      <div class="meta">${escapeHtml(spot.title || '无标题')}</div>
      <div class="tags">${(spot.fish_species || []).map((f) => `<span class="tag">${escapeHtml(f)}</span>`).join('') || '<span class="tag gray">鱼种待补充</span>'}</div>`;
    li.addEventListener('click', () => openSpot(spot));
    list.appendChild(li);
  });
}

function applyFilters() {
  const q = $('searchInput').value.trim().toLowerCase();
  const fish = $('fishFilter').value;
  const month = $('monthFilter')?.value || '';
  const scoreFilter = $('scoreFilter') || $('confidenceFilter');
  const scoreValue = $('scoreValue') || $('confidenceValue');
  const minScore = Number(scoreFilter?.value || 0);
  if (scoreValue) scoreValue.textContent = minScore.toFixed(1);

  state.filtered = state.spots.filter((spot) => {
    const text = [spot.place_name, spot.query_name, spot.title, spot.author, ...(spot.fish_species || [])].join('\n').toLowerCase();
    const okQ = !q || text.includes(q);
    const okFish = !fish || (spot.fish_species || []).includes(fish);
    const spotMonth = getPublishMonth(spot) || '__unknown__';
    const okMonth = !month || spotMonth === month;
    const okScore = Number(spot.quality_score || 0) >= minScore;
    return okQ && okFish && okMonth && okScore;
  });
  updateSummary();
  renderList();
  renderMarkers();
  fitAll();
}

function toggleMapType() {
  if (!state.map || !window.T) return;
  state.mapType = state.mapType === 'normal' ? 'satellite' : 'normal';
  try {
    state.map.setMapType(state.mapType === 'normal' ? TMAP_NORMAL_MAP : TMAP_HYBRID_MAP);
  } catch (_) {
    console.warn('当前天地图 API 未提供地图类型切换常量');
  }
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}
function escapeAttr(value) { return escapeHtml(value).replace(/'/g, '&#39;'); }

async function bootMap() {
  const tk = $('tkInput').value.trim();
  if (!tk) {
    emptyState.textContent = '请输入天地图 TK 后加载地图';
    return;
  }
  localStorage.setItem('TIANDITU_TK', tk);
  emptyState.textContent = '正在加载天地图…';
  await loadTianditu(tk);
  initMap();
}

$('loadMapBtn').addEventListener('click', () => bootMap().catch((err) => { emptyState.textContent = err.message; }));
$('fitBtn').addEventListener('click', fitAll);
$('mapTypeBtn').addEventListener('click', toggleMapType);
['searchInput', 'fishFilter', 'monthFilter', 'scoreFilter', 'confidenceFilter'].forEach((id) => {
  const el = $(id);
  if (el) el.addEventListener('input', applyFilters);
});

(async function main() {
  try {
    await loadData();
    renderScoreLegend();
    const tk = getTk();
    $('tkInput').value = tk;
    if (tk) await bootMap();
    else emptyState.textContent = '数据已加载，请输入天地图 TK 显示底图';
  } catch (err) {
    summary.textContent = err.message;
    emptyState.textContent = err.message;
  }
})();
