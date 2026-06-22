#!/usr/bin/env python3
"""使用百度地图重新地理编码存量钓点数据，更新数据库中的经纬度。

用法：
    python scripts/update_fishing_spots_geocode.py
    python scripts/update_fishing_spots_geocode.py --dry-run
    python scripts/update_fishing_spots_geocode.py --batch 20 --delay 1.5
    python scripts/update_fishing_spots_geocode.py --where "geocode_score < 80"
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "fishing_spots.sqlite"
GEOCODE_SCRIPT = ROOT / ".agents" / "skills" / "geocode" / "geocode.py"
REQUEST_TIMEOUT = 60


def run(cmd: list[str], timeout: int = REQUEST_TIMEOUT) -> str:
    p = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nSTDERR:\n{p.stderr}")
    return p.stdout


def baidu_geocode(query: str) -> dict | None:
    """调用统一 geocode skill，使用百度地图。返回原始 BD09 结果。"""
    try:
        out = run(["python", str(GEOCODE_SCRIPT), "-p", "baidu", "geocode", query], timeout=REQUEST_TIMEOUT)
    except (subprocess.TimeoutExpired, RuntimeError) as exc:
        print(f"  [geocode error] {query}: {exc}", file=sys.stderr, flush=True)
        return None

    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        print(f"  [json error] {query}: {exc}", file=sys.stderr, flush=True)
        return None

    # 百度地图返回 status 为整数 0 表示成功
    if data.get("status") != 0 or "result" not in data:
        status = data.get("status")
        msg = data.get("msg", "")
        print(f"  [baidu fail] {query} status={status} msg={msg}", file=sys.stderr, flush=True)
        return None

    return data


def baidu_geocode_wgs84(query: str) -> dict | None:
    """调用百度地图 geocode，并将结果转换为 WGS84 坐标系。"""
    geo = baidu_geocode(query)
    if geo is None:
        return None
    result = geo["result"]
    loc = result["location"]
    bd_lon = float(loc["lng"])
    bd_lat = float(loc["lat"])
    wgs_lon, wgs_lat = convert_coords(bd_lon, bd_lat, "bd09", "wgs84")
    return {
        "longitude": wgs_lon,
        "latitude": wgs_lat,
        "geocode_score": int(result.get("confidence", 0)),
        "geocode_level": result.get("level", ""),
    }


def convert_coords(lon: float, lat: float, from_sys: str, to_sys: str) -> tuple[float, float]:
    """坐标系转换包装（bd09 / gcj02 / wgs84）。"""
    # 复用 geocode skill 中的转换逻辑（内嵌简化版，避免 import 依赖）
    import math
    PI = 3.1415926535897932384626
    X_PI = 3.14159265358979324 * 3000.0 / 180.0
    A = 6378245.0
    EE = 0.00669342162296594323

    def _transformlat(lng, lat):
        ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
        ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 * math.sin(2.0 * lng * PI)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lat * PI) + 40.0 * math.sin(lat / 3.0 * PI)) * 2.0 / 3.0
        ret += (160.0 * math.sin(lat / 12.0 * PI) + 320.0 * math.sin(lat * PI / 30.0)) * 2.0 / 3.0
        return ret

    def _transformlng(lng, lat):
        ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
        ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 * math.sin(2.0 * lng * PI)) * 2.0 / 3.0
        ret += (20.0 * math.sin(lng * PI) + 40.0 * math.sin(lng / 3.0 * PI)) * 2.0 / 3.0
        ret += (150.0 * math.sin(lng / 12.0 * PI) + 300.0 * math.sin(lng / 30.0 * PI)) * 2.0 / 3.0
        return ret

    def bd09_to_gcj02(bd_lon, bd_lat):
        x = bd_lon - 0.0065
        y = bd_lat - 0.006
        z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
        theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
        return z * math.cos(theta), z * math.sin(theta)

    def gcj02_to_wgs84(gcj_lon, gcj_lat):
        dlat = _transformlat(gcj_lon - 105.0, gcj_lat - 35.0)
        dlng = _transformlng(gcj_lon - 105.0, gcj_lat - 35.0)
        radlat = gcj_lat / 180.0 * PI
        magic = math.sin(radlat)
        magic = 1 - EE * magic * magic
        sqrtmagic = math.sqrt(magic)
        dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
        dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
        return gcj_lon - dlng, gcj_lat - dlat

    def wgs84_to_gcj02(wgs_lon, wgs_lat):
        dlat = _transformlat(wgs_lon - 105.0, wgs_lat - 35.0)
        dlng = _transformlng(wgs_lon - 105.0, wgs_lat - 35.0)
        radlat = wgs_lat / 180.0 * PI
        magic = math.sin(radlat)
        magic = 1 - EE * magic * magic
        sqrtmagic = math.sqrt(magic)
        dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
        dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
        return wgs_lon + dlng, wgs_lat + dlat

    def gcj02_to_bd09(gcj_lon, gcj_lat):
        z = math.sqrt(gcj_lon * gcj_lon + gcj_lat * gcj_lat) + 0.00002 * math.sin(gcj_lat * X_PI)
        theta = math.atan2(gcj_lat, gcj_lon) + 0.000003 * math.cos(gcj_lon * X_PI)
        return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006

    f = from_sys.lower().strip()
    t = to_sys.lower().strip()
    if f == t:
        return lon, lat
    if f == "bd09" and t == "wgs84":
        gcj_lon, gcj_lat = bd09_to_gcj02(lon, lat)
        return gcj02_to_wgs84(gcj_lon, gcj_lat)
    if f == "bd09" and t == "gcj02":
        return bd09_to_gcj02(lon, lat)
    if f == "gcj02" and t == "wgs84":
        return gcj02_to_wgs84(lon, lat)
    if f == "wgs84" and t == "gcj02":
        return wgs84_to_gcj02(lon, lat)
    if f == "gcj02" and t == "bd09":
        return gcj02_to_bd09(lon, lat)
    if f == "wgs84" and t == "bd09":
        # WGS84 -> GCJ02 -> BD09
        dlat = _transformlat(lon - 105.0, lat - 35.0)
        dlng = _transformlng(lon - 105.0, lat - 35.0)
        radlat = lat / 180.0 * PI
        magic = math.sin(radlat)
        magic = 1 - EE * magic * magic
        sqrtmagic = math.sqrt(magic)
        dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
        dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
        gcj_lon = lon + dlng
        gcj_lat = lat + dlat
        z = math.sqrt(gcj_lon * gcj_lon + gcj_lat * gcj_lat) + 0.00002 * math.sin(gcj_lat * X_PI)
        theta = math.atan2(gcj_lat, gcj_lon) + 0.000003 * math.cos(gcj_lon * X_PI)
        return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006
    raise ValueError(f"不支持的坐标转换：{from_sys} -> {to_sys}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="使用百度地图重新地理编码存量钓点数据。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  %(prog)s                       # 全部重新 geocode
  %(prog)s --dry-run             # 只查看，不更新数据库
  %(prog)s --batch 10 --delay 2  # 每次处理 10 条，间隔 2 秒
  %(prog)s --where "geocode_score < 80"   # 只更新低置信度记录
  %(prog)s --where "geocode_level = '区县及以上级行政区划'"  # 只更新行政区划级别记录
        """,
    )
    ap.add_argument("--db", default=str(DB_PATH), help="SQLite 数据库路径")
    ap.add_argument("--dry-run", action="store_true", help="只预览，不写入数据库")
    ap.add_argument("--batch", type=int, default=0, help="最多处理多少条；0 表示全部")
    ap.add_argument("--delay", type=float, default=1.0, help="每次请求间隔秒数（默认 1.0）")
    ap.add_argument("--delay-jitter", type=float, default=0.5, help="随机抖动秒数（默认 0.5）")
    ap.add_argument("--where", default="", help="附加 WHERE 条件，如 'geocode_score < 80'")
    ap.add_argument("--order-by", default="id", help="排序字段（默认 id）")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在: {db_path}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row

    # 构建查询
    sql = "SELECT id, place_name, query_name, longitude, latitude, geocode_score, geocode_level FROM fishing_spots"
    params: list = []
    if args.where:
        sql += f" WHERE {args.where}"
    sql += f" ORDER BY {args.order_by}"
    if args.batch > 0:
        sql += " LIMIT ?"
        params.append(args.batch)

    rows = conn.execute(sql, params).fetchall()
    total = len(rows)
    if total == 0:
        print("没有需要更新的记录。")
        sys.exit(0)

    print(f"共 {total} 条记录待处理（dry-run={args.dry_run}）")
    print(f"{'id':>6}  {'place_name':<20}  {'old_lon':>10}  {'old_lat':>10}  {'new_lon':>10}  {'new_lat':>10}  {'score':>5}  {'level':<15}")
    print("-" * 100)

    updated = 0
    failed = 0
    skipped = 0
    for i, row in enumerate(rows, 1):
        row_id = row["id"]
        place_name = row["place_name"] or ""
        query_name = row["query_name"] or place_name
        if not query_name:
            print(f"{row_id:>6}  [skip] 无 query_name/place_name")
            skipped += 1
            continue

        geo = baidu_geocode_wgs84(query_name)
        if geo is None:
            failed += 1
            continue

        old_lon = row["longitude"]
        old_lat = row["latitude"]
        new_lon = geo["longitude"]
        new_lat = geo["latitude"]
        score = geo["geocode_score"]
        level = geo["geocode_level"]

        print(
            f"{row_id:>6}  {place_name:<20}  "
            f"{old_lon if old_lon else '':>10.6f}  {old_lat if old_lat else '':>10.6f}  "
            f"{new_lon:>10.6f}  {new_lat:>10.6f}  {score:>5}  {level:<15}"
        )

        if not args.dry_run:
            conn.execute(
                """UPDATE fishing_spots
                   SET longitude = ?, latitude = ?, geocode_score = ?, geocode_level = ?
                   WHERE id = ?""",
                (new_lon, new_lat, score, level, row_id),
            )
            updated += 1
        else:
            updated += 1

        # 节流，最后一条不 sleep
        if i < total:
            jitter = random.uniform(0, args.delay_jitter)
            sleep = args.delay + jitter
            time.sleep(sleep)

    print("-" * 100)
    mode = "预览" if args.dry_run else "已更新"
    print(f"完成：{mode} {updated} 条，失败 {failed} 条，跳过 {skipped} 条")


if __name__ == "__main__":
    main()
