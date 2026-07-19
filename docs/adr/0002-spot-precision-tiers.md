# 钓点精度分级：point / segment / reject

`fishing_spots` 增加 `precision` 列，三级语义（CONTEXT.md）：**精确点 (point)** 可直接导航的锚点（POI、桥、闸、地标）；**河段片区 (segment)** 有信息量但不能钉点的粗粒度（光秃支流名如 府河、村居/街道/片区）；**reject** 不是钓点（区/县行政区划、长江/汉江等干流、无锚点泛词如 凼子），持久化前拒绝，存量行保留但从 GeoJSON 导出剔除。

分类是确定性函数（`vocabulary.py` 词表 + `geocode_level`），在 `extract.py` 的 `classify_place_name` / `refine_precision`，三个落点循环（video_text / transcript / comment）和 store 的 `insert_record` 兜底共用。关键事实：`geocode_level ∈ {区县, 城市}` 意味着 geocoder 只解析到行政粒度，**坐标是行政中心点而非该地**，永远是垃圾数据——这是 reject 的依据，也是 57 行存量被清出的主要原因（多为 autocorrect 之前的 score≤20 旧行）。

## Considered Options

- **单表 + 分级列（采用）**：一列 + 纯函数 + 导出过滤，立即解决地图噪声；列未来可无损迁移为水系外键。
- **水系层级实体（issue #3，暂缓）**：`water_bodies ← fishing_spots` 是钓友心智的正统模型，但 schema/去重/geocode 语义全动，等数据量级和"按水系浏览"需求明确后再做。
- **LLM prompt 过滤**：只做源头减量（prompt 已同步收紧），不能作为闸门——LLM 遵从度不可靠，确定性分类才是闸门。

## Consequences

- 导出按 `precision != 'reject'` 过滤并在 feature properties 携带 `precision`，前端可按级渲染（segment 弱化为"大致范围"标记）。
- 泛词+地标（"某某停车场附近"）不受泛词表影响——导航地标 prompt 提取的是锚点本身。
- 存量 reject 行未删除；issue #2 的重编计划可将其中地名正确的行（如 月湖 的城市中心点行）重新 geocode 复活。
