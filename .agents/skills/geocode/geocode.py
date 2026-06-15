#!/usr/bin/env python3
"""
地理编码 / 逆地理编码统一脚本。
支持百度地图（baidu）与天地图（tianditu）两个提供商。
密钥从环境变量或 .env 文件读取，不依赖第三方库。
"""
import argparse
import json
import os
import sys
import math
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

REQUEST_TIMEOUT = 15

# 坐标系常量
PI = 3.1415926535897932384626
X_PI = 3.14159265358979324 * 3000.0 / 180.0
A = 6378245.0
EE = 0.00669342162296594323

def _transformlat(lng: float, lat: float) -> float:
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 * math.sin(2.0 * lng * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * PI) + 40.0 * math.sin(lat / 3.0 * PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * PI) + 320.0 * math.sin(lat * PI / 30.0)) * 2.0 / 3.0
    return ret


def _transformlng(lng: float, lat: float) -> float:
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * PI) + 20.0 * math.sin(2.0 * lng * PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * PI) + 40.0 * math.sin(lng / 3.0 * PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * PI) + 300.0 * math.sin(lng / 30.0 * PI)) * 2.0 / 3.0
    return ret


def bd09_to_gcj02(bd_lon: float, bd_lat: float) -> tuple[float, float]:
    """BD09 -> GCJ02（火星坐标系）"""
    x = bd_lon - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * X_PI)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * X_PI)
    gcj_lon = z * math.cos(theta)
    gcj_lat = z * math.sin(theta)
    return gcj_lon, gcj_lat


def gcj02_to_bd09(gcj_lon: float, gcj_lat: float) -> tuple[float, float]:
    """GCJ02 -> BD09"""
    z = math.sqrt(gcj_lon * gcj_lon + gcj_lat * gcj_lat) + 0.00002 * math.sin(gcj_lat * X_PI)
    theta = math.atan2(gcj_lat, gcj_lon) + 0.000003 * math.cos(gcj_lon * X_PI)
    bd_lon = z * math.cos(theta) + 0.0065
    bd_lat = z * math.sin(theta) + 0.006
    return bd_lon, bd_lat


def gcj02_to_wgs84(gcj_lon: float, gcj_lat: float) -> tuple[float, float]:
    """GCJ02 -> WGS84"""
    dlat = _transformlat(gcj_lon - 105.0, gcj_lat - 35.0)
    dlng = _transformlng(gcj_lon - 105.0, gcj_lat - 35.0)
    radlat = gcj_lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
    wgs_lat = gcj_lat - dlat
    wgs_lon = gcj_lon - dlng
    return wgs_lon, wgs_lat


def wgs84_to_gcj02(wgs_lon: float, wgs_lat: float) -> tuple[float, float]:
    """WGS84 -> GCJ02"""
    dlat = _transformlat(wgs_lon - 105.0, wgs_lat - 35.0)
    dlng = _transformlng(wgs_lon - 105.0, wgs_lat - 35.0)
    radlat = wgs_lat / 180.0 * PI
    magic = math.sin(radlat)
    magic = 1 - EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((A * (1 - EE)) / (magic * sqrtmagic) * PI)
    dlng = (dlng * 180.0) / (A / sqrtmagic * math.cos(radlat) * PI)
    gcj_lat = wgs_lat + dlat
    gcj_lon = wgs_lon + dlng
    return gcj_lon, gcj_lat


def bd09_to_wgs84(bd_lon: float, bd_lat: float) -> tuple[float, float]:
    """BD09 -> WGS84"""
    gcj_lon, gcj_lat = bd09_to_gcj02(bd_lon, bd_lat)
    return gcj02_to_wgs84(gcj_lon, gcj_lat)


def wgs84_to_bd09(wgs_lon: float, wgs_lat: float) -> tuple[float, float]:
    """WGS84 -> BD09"""
    gcj_lon, gcj_lat = wgs84_to_gcj02(wgs_lon, wgs_lat)
    return gcj02_to_bd09(gcj_lon, gcj_lat)


COORD_CONVERTERS = {
    ("bd09", "gcj02"): bd09_to_gcj02,
    ("gcj02", "bd09"): gcj02_to_bd09,
    ("gcj02", "wgs84"): gcj02_to_wgs84,
    ("wgs84", "gcj02"): wgs84_to_gcj02,
    ("bd09", "wgs84"): bd09_to_wgs84,
    ("wgs84", "bd09"): wgs84_to_bd09,
}


def convert_coords(lon: float, lat: float, from_sys: str, to_sys: str) -> tuple[float, float]:
    """通用坐标转换入口。"""
    f = from_sys.lower().strip()
    t = to_sys.lower().strip()
    if f == t:
        return lon, lat
    converter = COORD_CONVERTERS.get((f, t))
    if converter is None:
        raise ValueError(f"不支持的坐标转换：{from_sys} -> {to_sys}。支持：bd09, gcj02, wgs84")
    return converter(lon, lat)

# 百度地图
BAIDU_GEOCODE_URL = "https://api.map.baidu.com/geocoding/v3"
BAIDU_REVERSE_URL = "https://api.map.baidu.com/reverse_geocoding/v3"
BAIDU_ENV_KEY = "BAIDU_AK"

# 天地图
TIANDITU_BASE_URL = "http://api.tianditu.gov.cn/geocoder"
TIANDITU_ENV_KEY = "TIANDITU_TK"


def iter_env_candidates() -> Iterable[Path]:
    """按优先级查找 .env：当前目录及父目录，最后是脚本所在目录。"""
    seen = set()
    for directory in (Path.cwd(), *Path.cwd().parents, Path(__file__).resolve().parent):
        env_path = directory / ".env"
        if env_path in seen:
            continue
        seen.add(env_path)
        yield env_path


def load_env_file() -> Optional[Path]:
    """加载第一个存在的 .env；不覆盖已有环境变量。"""
    for env_path in iter_env_candidates():
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                os.environ.setdefault(key, value)
        return env_path
    return None


def get_env(key: str, name: str) -> str:
    """读取指定密钥。显式环境变量优先，其次 .env。"""
    load_env_file()
    val = os.environ.get(key, "").strip()
    if not val:
        raise RuntimeError(
            f"未找到 {name} 密钥。请设置环境变量 {key}，或在项目根目录 .env 中配置：{key}=..."
        )
    return val


def request_json(url: str, provider: str) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"请求 {provider} API 失败：{exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{provider} API 返回的不是合法 JSON") from exc


def baidu_geocode(address: str, ak: str) -> dict:
    query = urllib.parse.urlencode({"address": address, "output": "json", "ak": ak})
    return request_json(f"{BAIDU_GEOCODE_URL}?{query}", "百度地图")


def baidu_reverse_geocode(lon: float, lat: float, ak: str) -> dict:
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("经纬度超出范围：lon 应在 [-180,180]，lat 应在 [-90,90]")
    location = f"{lat},{lon}"
    query = urllib.parse.urlencode({"location": location, "output": "json", "ak": ak})
    return request_json(f"{BAIDU_REVERSE_URL}?{query}", "百度地图")


def tianditu_geocode(address: str, tk: str) -> dict:
    ds = json.dumps({"keyWord": address}, ensure_ascii=False)
    query = urllib.parse.urlencode({"ds": ds, "tk": tk})
    return request_json(f"{TIANDITU_BASE_URL}?{query}", "天地图")


def tianditu_reverse_geocode(lon: float, lat: float, tk: str) -> dict:
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("经纬度超出范围：lon 应在 [-180,180]，lat 应在 [-90,90]")
    post_str = json.dumps({"lon": lon, "lat": lat, "ver": 1}, ensure_ascii=False)
    query = urllib.parse.urlencode({"postStr": post_str, "type": "geocode", "tk": tk})
    return request_json(f"{TIANDITU_BASE_URL}?{query}", "天地图")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="地理编码 / 逆地理编码 / 坐标转换。支持百度地图（baidu）与天地图（tianditu）。"
    )
    parser.add_argument(
        "--provider", "-p",
        choices=["baidu", "tianditu"],
        default="tianditu",
        help="地图提供商（默认 tianditu）",
    )

    subparsers = parser.add_subparsers(dest="command")

    p_geocode = subparsers.add_parser("geocode", help="地址/地名 -> 经纬度")
    p_geocode.add_argument("address", help="要查询的地址、地名或 POI")
    p_geocode.add_argument(
        "--to", dest="to_coords",
        choices=["bd09", "gcj02", "wgs84"],
        help="输出坐标系（默认与提供商一致：baidu->bd09, tianditu->wgs84）",
    )

    p_reverse = subparsers.add_parser("reverse", help="经纬度 -> 地址")
    p_reverse.add_argument("lon", type=float, help="经度")
    p_reverse.add_argument("lat", type=float, help="纬度")

    p_convert = subparsers.add_parser("convert", help="坐标系转换（bd09 / gcj02 / wgs84）")
    p_convert.add_argument("--from", dest="from_sys", required=True, choices=["bd09", "gcj02", "wgs84"], help="源坐标系")
    p_convert.add_argument("--to", dest="to_sys", required=True, choices=["bd09", "gcj02", "wgs84"], help="目标坐标系")
    p_convert.add_argument("coords", nargs="+", type=float, help="经纬度对，例如 116.3 40.1 116.4 40.2")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "convert":
        coords = args.coords
        if len(coords) % 2 != 0:
            parser.error("convert 命令需要成对的经纬度参数，例如 116.3 40.1")
        results = []
        for i in range(0, len(coords), 2):
            lon, lat = coords[i], coords[i + 1]
            new_lon, new_lat = convert_coords(lon, lat, args.from_sys, args.to_sys)
            results.append({
                "from": {"lon": lon, "lat": lat, "system": args.from_sys},
                "to": {"lon": new_lon, "lat": new_lat, "system": args.to_sys},
            })
        print(json.dumps(results, ensure_ascii=False, indent=2))
        sys.exit(0)

    provider = args.provider
    if provider == "baidu":
        key = get_env(BAIDU_ENV_KEY, "百度地图")
        geocode_fn = baidu_geocode
        reverse_fn = baidu_reverse_geocode
    else:
        key = get_env(TIANDITU_ENV_KEY, "天地图")
        geocode_fn = tianditu_geocode
        reverse_fn = tianditu_reverse_geocode

    try:
        if args.command == "geocode":
            result = geocode_fn(args.address, key)
            # 自动坐标转换
            if hasattr(args, "to_coords") and args.to_coords:
                _apply_coords_transform(result, provider, args.to_coords)
        elif args.command == "reverse":
            result = reverse_fn(args.lon, args.lat, key)
        else:
            parser.error(f"未知命令：{args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _apply_coords_transform(result: dict, provider: str, to_sys: str) -> None:
    """对地理编码结果中的坐标进行转换。"""
    from_sys = "bd09" if provider == "baidu" else "wgs84"
    to = to_sys.lower().strip()
    if from_sys == to:
        return
    # 百度地图: result.location.lng / lat
    if provider == "baidu" and "result" in result and "location" in result["result"]:
        loc = result["result"]["location"]
        lon = float(loc["lng"])
        lat = float(loc["lat"])
        new_lon, new_lat = convert_coords(lon, lat, from_sys, to)
        loc["lng"] = new_lon
        loc["lat"] = new_lat
        result["_coord_system"] = to
    # 天地图: location.lon / lat
    elif provider == "tianditu" and "location" in result:
        loc = result["location"]
        lon = float(loc["lon"])
        lat = float(loc["lat"])
        new_lon, new_lat = convert_coords(lon, lat, from_sys, to)
        loc["lon"] = new_lon
        loc["lat"] = new_lat
        result["_coord_system"] = to


if __name__ == "__main__":
    main()
