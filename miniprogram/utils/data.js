/**
 * utils/data.js
 * 加载并处理钓点数据，提供筛选功能
 */
const RAW = require('./fishing-spots-data')

const ALL_SPOTS = RAW.features.map((f) => ({
  ...f.properties,
  lng: f.geometry.coordinates[0],
  lat: f.geometry.coordinates[1],
}))

const GENERATED_AT = RAW.generated_at || ''
const TOTAL = ALL_SPOTS.length

// ── 评分颜色带（与 Web 版一致） ──
const SCORE_BANDS = [
  { min: 0.75, key: '优', label: '优质 0.75–1.00', color: '#0f9f6e' },
  { min: 0.6,  key: '良', label: '良好 0.60–0.74', color: '#2f7ed8' },
  { min: 0.4,  key: '中', label: '一般 0.40–0.59', color: '#f59e0b' },
  { min: 0,    key: '低', label: '待观察 0.00–0.39', color: '#ef4444' },
]
const UNKNOWN_BAND = { key: 'na', label: '未评分', color: '#64748b' }

function getScoreBand(score) {
  const val = Number(score)
  if (!Number.isFinite(val)) return UNKNOWN_BAND
  return SCORE_BANDS.find((b) => val >= b.min) || UNKNOWN_BAND
}

function formatScore(val) {
  const s = Number(val)
  return Number.isFinite(s) ? s.toFixed(2) : '-'
}

function formatMonth(m) {
  if (!m) return '未注明'
  return Number(m) + '月'
}

// ── 辅助函数 ──
function getActiveSources(spot, month) {
  const sources = spot.sources || []
  return month ? sources.filter((s) => s.publish_month === month) : sources
}

function getActiveScore(spot, month) {
  const stats = month ? (spot.monthly_scores || {})[month] : spot
  const score = Number(stats ? stats.quality_score : spot.quality_score)
  return Number.isFinite(score) ? score : null
}

/**
 * 提取所有鱼种列表（去重排序）
 */
function getAllFishSpecies() {
  const set = new Set()
  ALL_SPOTS.forEach((s) => (s.fish_species || []).forEach((name) => set.add(name)))
  return [...set].sort()
}

/**
 * 提取所有月份（去重排序）
 */
function getAllMonths() {
  const set = new Set()
  ALL_SPOTS.forEach((spot) => {
    Object.keys(spot.monthly_scores || {}).forEach((m) => set.add(m))
  })
  return [...set].sort((a, b) => Number(a) - Number(b))
}

/**
 * 构建月份 picker range（含"全部月份"头）
 */
function getMonthPickerRange() {
  const months = getAllMonths()
  return ['全部月份', ...months.map((m) => formatMonth(m))]
}

/**
 * 筛选钓点
 * @param {string}  query   搜索关键词
 * @param {string}  fish    鱼种过滤（空字符串=全部）
 * @param {string}  month   月份过滤（空=全部）
 * @param {number}  minScore 最低钓点评分 (quality_score)
 * @param {number}  minConf 最低可信度
 */
function filterSpots({ query = '', fish = '', month = '', minScore = 0, minConf = 0 } = {}) {
  const q = query.trim().toLowerCase()
  return ALL_SPOTS.filter((spot) => {
    // 搜索：覆盖名称、别名、鱼种、关键词、来源标题/作者
    const activeSources = month ? getActiveSources(spot, month) : (spot.sources || [])
    const text = [
      spot.place_name,
      ...(spot.aliases || []),
      ...(spot.fish_species || []),
      ...(spot.keywords || []),
      ...activeSources.flatMap((s) => [s.title, s.author]),
    ]
      .join('\n')
      .toLowerCase()
    const okQ = !q || text.includes(q)

    // 鱼种过滤（选月份时只看该月来源的鱼种）
    const activeFish = month
      ? new Set(activeSources.flatMap((s) => s.fish_species || []))
      : new Set(spot.fish_species || [])
    const okFish = !fish || activeFish.has(fish)

    // 月份过滤
    const okMonth = !month || Boolean((spot.monthly_scores || {})[month])

    // 评分过滤
    const score = getActiveScore(spot, month) || 0
    const okScore = score >= minScore

    // 可信度过滤
    const okConf = Number(spot.confidence || 0) >= minConf

    return okQ && okFish && okMonth && okScore && okConf
  })
}

/**
 * 将钓点数组转换为 map 组件所需的 markers 格式（按评分带染色）
 */
function spotsToMarkers(spots, month = '') {
  return spots.map((spot, i) => {
    const score = getActiveScore(spot, month)
    const band = getScoreBand(score)
    const iconKey = band.key // '优' | '良' | '中' | '低' | 'na'
    const idNum = Number(String(spot.id).replace(/[^0-9]/g, '')) || i
    return {
      id: idNum,
      longitude: spot.lng,
      latitude: spot.lat,
      width: 28,
      height: 36,
      iconPath: `/images/marker_${iconKey}@2x.png`,
      anchor: { x: 0.5, y: 1 },
      callout: {
        content: spot.place_name,
        color: '#0a1929',
        fontSize: 13,
        borderRadius: 6,
        bgColor: '#ffffff',
        padding: 6,
        display: 'BYCLICK',
      },
    }
  })
}

module.exports = {
  ALL_SPOTS,
  TOTAL,
  GENERATED_AT,
  SCORE_BANDS,
  UNKNOWN_BAND,
  getScoreBand,
  formatScore,
  formatMonth,
  getActiveSources,
  getActiveScore,
  getAllFishSpecies,
  getAllMonths,
  getMonthPickerRange,
  filterSpots,
  spotsToMarkers,
}
