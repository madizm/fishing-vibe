# 武汉钓鱼钓点地图

基于项目已有 SQLite 数据 `data/fishing_spots.sqlite` 导出的天地图 Web 应用。

## 更新地图数据

```bash
python scripts/export_fishing_spots_map_data.py
```

默认输出：`web/fishing-spots.json`。

## 本地运行

由于浏览器通常不允许 `file://` 直接 `fetch` JSON，请在项目根目录启动一个静态服务：

```bash
python -m http.server 8000
```

然后访问：

```text
http://localhost:8000/web/?tk=你的天地图TK
```

也可以不带 `tk` 打开，在页面左侧输入天地图 TK 后点击「加载」。

## 功能

- 天地图底图展示钓点 marker，并按 `fishing_spots.quality_score` 评分区间显示不同颜色图标
- 点击 marker / 左侧列表查看视频来源、鱼种、钓点评分和基于 `comment_keywords` 的评论关键词标签
- 按关键词、鱼种、视频发布月份、最低钓点评分过滤
- 一键定位到全部钓点
- 矢量 / 影像底图切换
