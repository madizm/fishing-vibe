// pages/index/index.js
const dataUtils = require('../../utils/data')

const DEFAULT_LONGITUDE = 114.3055
const DEFAULT_LATITUDE = 30.5928
const DEFAULT_SCALE = 10

Page({
  data: {
    // 地图
    longitude: DEFAULT_LONGITUDE,
    latitude: DEFAULT_LATITUDE,
    scale: DEFAULT_SCALE,
    markers: [],
    // 数据
    total: 0,
    visibleCount: 0,
    generatedAt: '',
    spots: [],        // 当前筛选后列表
    allFish: [],      // 鱼种选项
    fishPickerRange: [], // picker 选项（含"全部鱼种"头）
    // 筛选条件
    searchQuery: '',
    fishFilter: '',
    confidenceFilter: 0,
    confidenceDisplay: '0.0',
    // 当前选中的钓点（底部弹出详情）
    selectedSpot: null,
    detailVisible: false,
    // 面板折叠（手机小屏时默认折叠列表）
    panelCollapsed: false,
    // 地图类型：roadmap / satellite
    mapType: 'roadmap',
    mapTypeBtnText: '切换卫星图',
  },

  onLoad() {
    const allFish = dataUtils.getAllFishSpecies()
    const fishPickerRange = ['全部鱼种', ...allFish]
    const filtered = dataUtils.filterSpots()
    const markers = dataUtils.spotsToMarkers(filtered)
    this.setData({
      total: dataUtils.TOTAL,
      visibleCount: filtered.length,
      generatedAt: dataUtils.GENERATED_AT,
      spots: filtered,
      allFish,
      fishPickerRange,
      markers,
    })
    // 自动缩放到所有钓点
    this._fitToMarkers(filtered)
  },

  // ─── 筛选事件 ────────────────────────────────────────────────

  onSearchInput(e) {
    this.setData({ searchQuery: e.detail.value }, () => this._applyFilters())
  },

  onFishChange(e) {
    // picker 返回 value 为 range 数组的 index
    const idx = Number(e.detail.value)
    // fishPickerRange[0] = '全部鱼种' → 不过滤
    const fish = idx === 0 ? '' : this.data.fishPickerRange[idx]
    this.setData({ fishFilter: fish }, () => this._applyFilters())
  },

  onConfidenceChange(e) {
    const val = Number(e.detail.value)
    this.setData(
      { confidenceFilter: val, confidenceDisplay: val.toFixed(1) },
      () => this._applyFilters(),
    )
  },

  _applyFilters() {
    const filtered = dataUtils.filterSpots({
      query: this.data.searchQuery,
      fish: this.data.fishFilter,
      minConf: this.data.confidenceFilter,
    })
    const markers = dataUtils.spotsToMarkers(filtered)
    this.setData({ spots: filtered, visibleCount: filtered.length, markers })
    this._fitToMarkers(filtered)
  },

  // ─── 地图事件 ────────────────────────────────────────────────

  onMarkerTap(e) {
    const markerId = e.detail.markerId
    const spot = this.data.spots.find((s) => s.id === markerId)
    if (!spot) return
    this.setData({ selectedSpot: spot, detailVisible: true })
    // 地图平移到该点
    this.setData({ longitude: spot.lng, latitude: spot.lat })
  },

  onFitAll() {
    this._fitToMarkers(this.data.spots)
  },

  onToggleMapType() {
    const next = this.data.mapType === 'roadmap' ? 'satellite' : 'roadmap'
    this.setData({
      mapType: next,
      mapTypeBtnText: next === 'roadmap' ? '切换卫星图' : '切换矢量图',
    })
  },

  /**
   * 自动缩放地图以包含所有钓点
   * 小程序 map 组件通过 MapContext.includePoints 实现
   */
  _fitToMarkers(spots) {
    if (!spots || spots.length === 0) return
    if (!this._mapCtx) {
      this._mapCtx = wx.createMapContext('fishingMap', this)
    }
    const points = spots.map((s) => ({ longitude: s.lng, latitude: s.lat }))
    this._mapCtx.includePoints({
      points,
      padding: [60, 40, 60, 40],
    })
  },

  // ─── 列表点击 ────────────────────────────────────────────────

  onSpotTap(e) {
    const idx = e.currentTarget.dataset.idx
    const spot = this.data.spots[idx]
    if (!spot) return
    this.setData({
      selectedSpot: spot,
      detailVisible: true,
      longitude: spot.lng,
      latitude: spot.lat,
      scale: 14,
    })
  },

  // ─── 详情面板 ────────────────────────────────────────────────

  onCloseDetail() {
    this.setData({ detailVisible: false, selectedSpot: null })
  },

  onOpenUrl(e) {
    const url = e.currentTarget.dataset.url
    if (!url) return
    wx.setClipboardData({
      data: url,
      success() {
        wx.showToast({ title: '链接已复制', icon: 'success' })
      },
    })
  },

  // ─── 面板折叠 ────────────────────────────────────────────────

  onTogglePanel() {
    this.setData({ panelCollapsed: !this.data.panelCollapsed })
  },
})
