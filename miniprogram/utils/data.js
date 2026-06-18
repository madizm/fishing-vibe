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

/**
 * 提取所有鱼种列表（去重排序）
 */
function getAllFishSpecies() {
  const set = new Set()
  ALL_SPOTS.forEach((s) => (s.fish_species || []).forEach((name) => set.add(name)))
  return [...set].sort()
}

/**
 * 筛选钓点
 * @param {string} query   搜索关键词
 * @param {string} fish    鱼种过滤（空字符串=全部）
 * @param {number} minConf 最低可信度
 * @returns {Array}
 */
function filterSpots({ query = '', fish = '', minConf = 0 } = {}) {
  const q = query.trim().toLowerCase()
  return ALL_SPOTS.filter((spot) => {
    const text = [
      spot.place_name,
      spot.query_name,
      spot.title,
      spot.author,
      ...(spot.fish_species || []),
    ]
      .join('\n')
      .toLowerCase()
    const okQ = !q || text.includes(q)
    const okFish = !fish || (spot.fish_species || []).includes(fish)
    const okConf = Number(spot.confidence || 0) >= minConf
    return okQ && okFish && okConf
  })
}

/**
 * 将钓点数组转换为 map 组件所需的 markers 格式
 */
function spotsToMarkers(spots) {
  return spots.map((spot) => ({
    id: spot.id,
    longitude: spot.lng,
    latitude: spot.lat,
    width: 32,
    height: 32,
    callout: {
      content: spot.place_name,
      color: '#14231f',
      fontSize: 13,
      borderRadius: 6,
      bgColor: '#ffffff',
      padding: 6,
      display: 'BYCLICK',
    },
    // 自定义图标（可选，注释掉使用默认红点）
    // iconPath: '/images/marker.png',
  }))
}

module.exports = {
  ALL_SPOTS,
  TOTAL,
  GENERATED_AT,
  getAllFishSpecies,
  filterSpots,
  spotsToMarkers,
}
