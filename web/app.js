const DATA_URL = './fishing-spots.json';
const DEFAULT_CENTER = [114.3055, 30.5928];

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
  updateSummary();
  renderList();
}

function buildFishFilter() {
  const select = $('fishFilter');
  const fish = new Set();
  state.spots.forEach((s) => (s.fish_species || []).forEach((name) => fish.add(name)));
  [...fish].sort().forEach((name) => {
    const option = document.createElement('option');
    option.value = name;
    option.textContent = name;
    select.appendChild(option);
  });
}

function updateSummary() {
  const total = state.spots.length;
  const visible = state.filtered.length;
  const generated = state.data?.generated_at ? `，数据生成：${state.data.generated_at}` : '';
  summary.textContent = `共 ${total} 个入库钓点，当前显示 ${visible} 个${generated}`;
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
  const fish = (spot.fish_species || []).map((f) => `<span class="tag">${escapeHtml(f)}</span>`).join('') || '<span class="tag gray">鱼种待补充</span>';
  const url = spot.url ? `<p><a href="${escapeAttr(spot.url)}" target="_blank" rel="noreferrer">打开抖音来源</a></p>` : '';
  return `<div class="info">
    <h3>${escapeHtml(spot.place_name)}</h3>
    <p><b>经纬度：</b>${spot.lng.toFixed(6)}, ${spot.lat.toFixed(6)}</p>
    <p><b>地理编码：</b>${escapeHtml(spot.geocode_level || '-')} / score ${spot.geocode_score ?? '-'}</p>
    <p><b>可信度：</b>${spot.confidence ?? '-'}</p>
    <p><b>钓点评分：</b>${spot.quality_score ?? '-'} ${spot.quality_score_source ? `(${escapeHtml(spot.quality_score_source)})` : ''}</p>
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
    const marker = new T.Marker(new T.LngLat(spot.lng, spot.lat));
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
    const li = document.createElement('li');
    li.className = 'spot-card';
    li.innerHTML = `<div class="spot-title"><span>${escapeHtml(spot.place_name)}</span><span class="score">评分 ${spot.quality_score ?? '-'} / 可信度 ${spot.confidence ?? '-'}</span></div>
      <div class="meta">${escapeHtml(spot.title || '无标题')}</div>
      <div class="tags">${(spot.fish_species || []).map((f) => `<span class="tag">${escapeHtml(f)}</span>`).join('') || '<span class="tag gray">鱼种待补充</span>'}</div>`;
    li.addEventListener('click', () => openSpot(spot));
    list.appendChild(li);
  });
}

function applyFilters() {
  const q = $('searchInput').value.trim().toLowerCase();
  const fish = $('fishFilter').value;
  const minConfidence = Number($('confidenceFilter').value);
  $('confidenceValue').textContent = minConfidence.toFixed(1);

  state.filtered = state.spots.filter((spot) => {
    const text = [spot.place_name, spot.query_name, spot.title, spot.author, ...(spot.fish_species || [])].join('\n').toLowerCase();
    const okQ = !q || text.includes(q);
    const okFish = !fish || (spot.fish_species || []).includes(fish);
    const okConfidence = Number(spot.confidence || 0) >= minConfidence;
    return okQ && okFish && okConfidence;
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
['searchInput', 'fishFilter', 'confidenceFilter'].forEach((id) => $(id).addEventListener('input', applyFilters));

(async function main() {
  try {
    await loadData();
    const tk = getTk();
    $('tkInput').value = tk;
    if (tk) await bootMap();
    else emptyState.textContent = '数据已加载，请输入天地图 TK 显示底图';
  } catch (err) {
    summary.textContent = err.message;
    emptyState.textContent = err.message;
  }
})();
