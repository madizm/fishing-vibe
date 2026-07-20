# PostGIS 本地数据库

## 启动

```bash
docker compose up -d postgis
docker compose ps
```

默认连接串：

```text
postgresql://fishing_vibe:fishing_vibe@localhost:5432/fishing_vibe
```

可通过 `.env` 中的 `POSTGRES_DB`、`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_PORT` 修改容器配置；应用可通过 `FISHING_VIBE_DATABASE_URL` 使用完整连接串。

## 从 SQLite 迁移

迁移脚本保留所有主键和外键关系，将经纬度转换为 SRID 4326 的 PostGIS `Point`。目标库非空时默认拒绝执行：

```bash
uv run python scripts/migrate_sqlite_to_postgis.py
```

确认要覆盖目标库时：

```bash
uv run python scripts/migrate_sqlite_to_postgis.py --replace
```

脚本在同一事务内写入并校验各表行数、geometry 有效性；失败会整体回滚。原 SQLite 文件不会删除，可作为迁移备份。

## OpenStreetMap 湖北数据

行政区划、水体和水道导入到独立的 `osm` schema，详见 [`docs/osm-import.md`](osm-import.md)：

```bash
bash scripts/import_osm_hubei.sh
```

## 常用命令

```bash
# 查看数据量和 PostGIS 版本
docker compose exec postgis psql -U fishing_vibe -d fishing_vibe -c \
  "SELECT PostGIS_Version(); SELECT count(*) FROM fishing_spots;"

# 导出地图数据
uv run python scripts/export_fishing_spots_map_data.py

# 停止（保留 volume）
docker compose down

# 连同数据库 volume 删除（会丢失数据）
docker compose down -v
```
