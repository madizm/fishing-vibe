# 湖北 OpenStreetMap 数据导入

本项目使用 Geofabrik 的湖北全域 PBF，为钓点提供行政区归属、水体名称匹配和邻水距离校验。数据不按武汉市边界裁剪。

## 数据源与许可

- 湖北下载页：https://download.geofabrik.de/asia/china/hubei.html
- PBF：https://download.geofabrik.de/asia/china/hubei-latest.osm.pbf
- OpenStreetMap 数据采用 ODbL。对外展示时应标注 `© OpenStreetMap contributors`，发布衍生数据库前应评估 ODbL 的共享要求。

PBF 保存在被 Git 忽略的 `data/osm/hubei-latest.osm.pbf`，数据库数据保存在 Docker volume。

## 首次导入

确保 PostGIS 和 osm2pgsql 镜像可用：

```bash
docker compose up -d postgis
docker pull iboates/osm2pgsql:2.2.0
bash scripts/import_osm_hubei.sh
```

文件不存在时脚本会自动下载。若已手工下载，也可以指定文件名（文件必须直接位于 `data/osm/`）：

```bash
bash scripts/import_osm_hubei.sh data/osm/hubei-latest.osm.pbf
```

导入采用 staging schema：

1. 将新快照导入 `osm_next`。
2. 创建索引和统一查询视图。
3. 校验所有表非空、geometry 有效且 SRID 为 4326。
4. 在事务内将 `osm_next` 发布为 `osm`。

失败时线上 `osm` schema 不变。

## 更新数据

下载当天最新快照并全量替换：

```bash
bash scripts/import_osm_hubei.sh --refresh
```

这是快照式全量更新，不保留 osm2pgsql slim 中间表，也不依赖增量 replication 状态。

可调整导入资源：

```bash
OSM2PGSQL_CACHE_MB=1024 OSM2PGSQL_PROCESSES=8 \
  bash scripts/import_osm_hubei.sh --refresh
```

## 数据模型

| 表或视图 | 内容 |
|---|---|
| `osm.admin_boundaries` | 完整的行政区划面，含 `admin_level`、名称和原始 tags |
| `osm.water_bodies` | 湖泊、河面、水库、池塘、湿地等面状水体 |
| `osm.waterways` | 河流、溪流、沟渠、运河、水坝等线状要素 |
| `osm.water_features` | 水闸、堰、瀑布等点状设施 |
| `osm.named_waters` | 面状水体和线状水道的统一只读视图 |
| `osm.import_metadata` | 来源 URL、文件大小、SHA-256 和导入时间 |

所有 geometry 均为 WGS84（SRID 4326）。osm2pgsql 自动创建 geometry GiST 索引；导入脚本额外创建 geography GiST 索引用于以米为单位的邻近查询，以及中文名称 trigram 索引。

## 验证

```bash
docker compose exec -T postgis psql -U fishing_vibe -d fishing_vibe -c "
SELECT 'admin_boundaries', count(*) FROM osm.admin_boundaries
UNION ALL SELECT 'water_bodies', count(*) FROM osm.water_bodies
UNION ALL SELECT 'waterways', count(*) FROM osm.waterways
UNION ALL SELECT 'water_features', count(*) FROM osm.water_features;
SELECT * FROM osm.import_metadata;
"
```

## 查询示例

查找钓点 1 公里内最近的水体：

```sql
SELECT
  s.id AS spot_id,
  w.source_kind,
  COALESCE(w.name_zh, w.name) AS water_name,
  w.feature_class,
  ST_Distance(s.location::geography, w.geom::geography) AS distance_m
FROM fishing_spots s
JOIN LATERAL (
  SELECT *
  FROM osm.named_waters w
  WHERE ST_DWithin(s.location::geography, w.geom::geography, 1000)
  ORDER BY ST_Distance(s.location::geography, w.geom::geography)
  LIMIT 1
) w ON true;
```

查询钓点所属行政区：

```sql
SELECT s.id, a.admin_level, COALESCE(a.name_zh, a.name) AS admin_name
FROM fishing_spots s
JOIN osm.admin_boundaries a ON ST_Covers(a.geom, s.location)
ORDER BY s.id, a.admin_level;
```
