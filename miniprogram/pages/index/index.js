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
    spots: [],
    // 选项数据
    allFish: [],
    fishPickerRange: [],
    monthPickerRange: [],
    scoreBands: [],       // 图例
    // 筛选条件
    searchQuery: '',
    fishFilter: '',
    monthFilter: '',       // 空=全部月份
    monthLabel: '全部月份',
    scoreFilter: 0,
    scoreDisplay: '0.0',
    confidenceFilter: 0,
    confidenceDisplay: '0.0',
    // 当前选中钓点（底部详情）
    selectedSpot: null,
    detailVisible: false,
    // 面板折叠
    panelCollapsed: false,
    // 卫星图
    enableSatellite: false,
    mapTypeBtnText: '切换卫星图',
  },

  onLoad() {
    const allFish = dataUtils.getAllFishSpecies()
    const fishPickerRange = ['全部鱼种', ...allFish]
    const monthPickerRange = dataUtils.getMonthPickerRange()
    const filtered = dataUtils.filterSpots()
    const spots = filtered.map((s) => {
      const score = dataUtils.getActiveScore(s, '')
      const band = dataUtils.getScoreBand(score)
      return {
        ...s,
        _bandColor: band.color,
        _bandKey: band.key,
        _bandLabel: band.label,
        _score: dataUtils.formatScore(score),
        _scoreMin: dataUtils.formatScore(s.score_min),
        _scoreMax: dataUtils.formatScore(s.score_max),
      }
    })
    const markers = dataUtils.spotsToMarkers(filtered)

    this.setData({
      total: dataUtils.TOTAL,
      visibleCount: spots.length,
      generatedAt: dataUtils.GENERATED_AT,
      spots,
      allFish,
      fishPickerRange,
      monthPickerRange,
      scoreBands: [...dataUtils.SCORE_BANDS, dataUtils.UNKNOWN_BAND],
      markers,
    })
    this._fitToMarkers(spots)
  },

  // ─── 筛选事件 ────────────────────────────────────────────────

  onSearchInput(e) {
    this.setData({ searchQuery: e.detail.value }, () => this._applyFilters())
  },

  onFishChange(e) {
    const idx = Number(e.detail.value)
    const fish = idx === 0 ? '' : this.data.fishPickerRange[idx]
    this.setData({ fishFilter: fish }, () => this._applyFilters())
  },

  onMonthChange(e) {
    const idx = Number(e.detail.value)
    const allMonths = dataUtils.getAllMonths()
    const month = idx === 0 ? '' : allMonths[idx - 1]
    this.setData({
      monthFilter: month,
      monthLabel: month ? allMonths[idx - 1] + '月' : '全部月份',
    }, () => this._applyFilters())
  },

  onScoreChange(e) {
    const val = Number(e.detail.value)
    this.setData(
      { scoreFilter: val, scoreDisplay: val.toFixed(1) },
      () => this._applyFilters(),
    )
  },

  onConfidenceChange(e) {
    const val = Number(e.detail.value)
    this.setData(
      { confidenceFilter: val, confidenceDisplay: val.toFixed(1) },
      () => this._applyFilters(),
    )
  },

  _applyFilters() {
    const month = this.data.monthFilter
    const filtered = dataUtils.filterSpots({
      query: this.data.searchQuery,
      fish: this.data.fishFilter,
      month: month,
      minScore: this.data.scoreFilter,
      minConf: this.data.confidenceFilter,
    })
    // 预计算每个 spot 的评分带和显示用评分，供 WXML 模板直接使用
    const spots = filtered.map((s) => {
      const score = dataUtils.getActiveScore(s, month)
      const band = dataUtils.getScoreBand(score)
      return {
        ...s,
        _bandColor: band.color,
        _bandKey: band.key,
        _bandLabel: band.label,
        _score: dataUtils.formatScore(score),
        _scoreMin: dataUtils.formatScore(s.score_min),
        _scoreMax: dataUtils.formatScore(s.score_max),
      }
    })
    const markers = dataUtils.spotsToMarkers(filtered, month)
    this.setData({ spots, visibleCount: spots.length, markers })
    this._fitToMarkers(spots)
  },

  // ─── 地图事件 ────────────────────────────────────────────────

  onMarkerTap(e) {
    const markerId = Number(e.detail.markerId)
    const spot = this.data.spots.find((s) => Number(String(s.id).replace(/[^0-9]/g, '')) === markerId)
    if (!spot) return
    this._openSpot(spot)
  },

  onFitAll() {
    this._fitToMarkers(this.data.spots)
  },

  onToggleMapType() {
    const next = !this.data.enableSatellite
    this.setData({
      enableSatellite: next,
      mapTypeBtnText: next ? '切换矢量图' : '切换卫星图',
    })
  },

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
    this._openSpot(spot)
  },

  _openSpot(spot) {
    const score = dataUtils.getActiveScore(spot, this.data.monthFilter)
    const band = dataUtils.getScoreBand(score)
    const sources = dataUtils.getActiveSources(spot, this.data.monthFilter)
    const keywords = (spot.keywords || []).slice(0, 10)
    const monthlyKeys = Object.keys(spot.monthly_scores || {}).sort((a, b) => Number(a) - Number(b))
    const _monthlyScores = monthlyKeys.map((m) => ({
      month: m,
      label: m + '月',
      score: dataUtils.formatScore(spot.monthly_scores[m].quality_score),
      count: spot.monthly_scores[m].source_count || 0,
    }))

    this.setData({
      selectedSpot: {
        ...spot,
        _score: dataUtils.formatScore(score),
        _bandLabel: band.label,
        _bandColor: band.color,
        _bandKey: band.key,
        _sources: (sources || []).slice(0, 8).map((s) => ({
          ...s,
          _score: dataUtils.formatScore(s.quality_score),
        })),
        _sourcesMore: (sources || []).length > 8 ? (sources.length - 8) : 0,
        _sourceCount: (sources || []).length,
        _keywords: keywords,
        _monthlyKeys: monthlyKeys,
        _monthlyScores,
      },
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
