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
BAIDU_PLACE_SEARCH_URL = "https://api.map.baidu.com/place/v2/search"
BAIDU_ENV_KEY = "BAIDU_AK"

# 天地图
TIANDITU_BASE_URL = "http://api.tianditu.gov.cn/geocoder"
TIANDITU_SEARCH_URL = "http://api.tianditu.gov.cn/v2/search"
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


def baidu_place_search(query_text: str, ak: str, region: str = "", limit: int = 10) -> dict:
    """百度 POI 搜索，用于地理编码低置信度时的候选纠错。"""
    params = {
        "query": query_text,
        "output": "json",
        "ak": ak,
        "page_size": max(1, min(int(limit), 20)),
    }
    if region:
        params["region"] = region
    query = urllib.parse.urlencode(params)
    return request_json(f"{BAIDU_PLACE_SEARCH_URL}?{query}", "百度地图POI搜索")


def _baidu_geocode_needs_autocorrect(result: dict, min_confidence: int = 80) -> bool:
    if result.get("status") != 0:
        return True
    payload = result.get("result") or {}
    if "location" not in payload:
        return True
    level = str(payload.get("level", ""))
    confidence = int(payload.get("confidence") or 0)
    return confidence < min_confidence


def _simplify_baidu_place_candidate(item: dict) -> dict:
    loc = item.get("location") or {}
    return {
        "name": item.get("name", ""),
        "address": item.get("address", ""),
        "province": item.get("province", ""),
        "city": item.get("city", ""),
        "area": item.get("area", ""),
        "uid": item.get("uid", ""),
        "location": {"lng": loc.get("lng"), "lat": loc.get("lat")},
    }


def baidu_geocode_with_autocorrect(
    address: str,
    ak: str,
    region: str = "",
    min_confidence: int = 80,
    candidate_limit: int = 8,
    corrector: str = "baidu",
    tianditu_tk: str = "",
) -> dict:
    """先地理编码；低置信度时用 POI 搜索返回更可能的候选（corrector 可选
    百度或天地图）。

    不静默纠错：返回体会包含 `_autocorrect`，标明原查询、纠正后的 POI 名称、
    是否应用纠错，以及原始地理编码结果。
    """
    original = baidu_geocode(address, ak)
    if not _baidu_geocode_needs_autocorrect(original, min_confidence=min_confidence):
        original.setdefault("_autocorrect", {
            "applied": False,
            "original_query": address,
            "reason": "geocode_confidence_ok",
        })
        return original

    if corrector == "tianditu":
        return _autocorrect_via_tianditu(original, address, tianditu_tk, region=region, candidate_limit=candidate_limit)

    try:
        search = baidu_place_search(address, ak, region=region, limit=candidate_limit)
    except Exception as exc:
        original["_autocorrect"] = {
            "applied": False,
            "original_query": address,
            "reason": "poi_search_failed",
            "error": str(exc),
            "original_result": original.get("result"),
        }
        return original

    candidates = [_simplify_baidu_place_candidate(item) for item in search.get("results", [])]
    best = candidates[0] if candidates else None
    if not best or not best.get("location", {}).get("lng") or not best.get("location", {}).get("lat"):
        original["_autocorrect"] = {
            "applied": False,
            "original_query": address,
            "reason": "no_poi_candidate",
            "original_result": original.get("result"),
        }
        original["candidates"] = candidates
        return original

    # 百度 Place Search 未返回置信度。能排到首位且 query_type=precise 时给高置信度，
    # 否则给中高置信度，便于下游区分“POI纠错结果”和普通低置信度 geocode。
    query_type = str(search.get("query_type", ""))
    confidence = 90 if query_type == "precise" else 85
    corrected = {
        "status": 0,
        "result": {
            "location": {
                "lng": float(best["location"]["lng"]),
                "lat": float(best["location"]["lat"]),
            },
            "precise": 1,
            "confidence": confidence,
            "comprehension": 100,
            "level": "POI",
            "name": best.get("name", ""),
            "address": best.get("address", ""),
            "province": best.get("province", ""),
            "city": best.get("city", ""),
            "area": best.get("area", ""),
        },
        "_autocorrect": {
            "applied": True,
            "original_query": address,
            "corrected_query": best.get("name", ""),
            "region": region,
            "reason": "geocode_low_confidence_poi_candidate",
            "original_result": original.get("result"),
            "poi_query_type": query_type,
            "poi_result_type": search.get("result_type", ""),
        },
        "candidates": candidates,
    }
    return corrected


def tianditu_poi_search(keyword: str, tk: str, region: str = "", limit: int = 10) -> dict:
    """天地图地名搜索 V2（行政区划区域搜索），用于地理编码低置信度时的候选纠错。"""
    post = {
        "keyWord": keyword,
        "queryType": "12",
        "start": "0",
        "count": str(max(1, min(int(limit), 300))),
        "show": "2",
    }
    if region:
        post["specify"] = region
    query = urllib.parse.urlencode({"postStr": json.dumps(post, ensure_ascii=False), "type": "query", "tk": tk})
    return request_json(f"{TIANDITU_SEARCH_URL}?{query}", "天地图POI搜索")


def _simplify_tianditu_poi(item: dict) -> dict:
    lonlat = str(item.get("lonlat", "") or "")
    parts = lonlat.split(",")
    lng = parts[0].strip() if len(parts) == 2 else ""
    lat = parts[1].strip() if len(parts) == 2 else ""
    return {
        "name": item.get("name", ""),
        "address": item.get("address", ""),
        "province": item.get("province", ""),
        "city": item.get("city", ""),
        "area": item.get("county", ""),
        "uid": item.get("hotPointID", ""),
        "location": {"lng": lng, "lat": lat},
    }


def _autocorrect_via_tianditu(original: dict, address: str, tk: str, region: str = "", candidate_limit: int = 8) -> dict:
    """低置信度时改用天地图行政区划搜索纠错。天地图坐标为 CGCS2000（≈WGS84），
    结果标记 _coord_system=wgs84，下游坐标转换必须跳过。"""
    # 搜索词去掉城市前缀（specify 已限定行政区），提高命中率
    keyword = address[len(region):] if region and address.startswith(region) else address
    try:
        search = tianditu_poi_search(keyword, tk, region=region, limit=candidate_limit)
    except Exception as exc:
        original["_autocorrect"] = {
            "applied": False,
            "corrector": "tianditu",
            "original_query": address,
            "reason": "poi_search_failed",
            "error": str(exc),
            "original_result": original.get("result"),
        }
        return original

    payload = search.get("result", search) or {}
    candidates = [_simplify_tianditu_poi(item) for item in payload.get("pois") or []]
    best = next((c for c in candidates if c["name"] == keyword), candidates[0] if candidates else None)
    if not best or not best["location"]["lng"] or not best["location"]["lat"]:
        original["_autocorrect"] = {
            "applied": False,
            "corrector": "tianditu",
            "original_query": address,
            "reason": "no_poi_candidate",
            "original_result": original.get("result"),
        }
        original["candidates"] = candidates
        return original

    exact = best["name"] == keyword
    corrected = {
        "status": 0,
        "result": {
            "location": {
                "lng": float(best["location"]["lng"]),
                "lat": float(best["location"]["lat"]),
            },
            "precise": 1,
            "confidence": 90 if exact else 85,
            "comprehension": 100,
            "level": "POI",
            "name": best.get("name", ""),
            "address": best.get("address", ""),
            "province": best.get("province", ""),
            "city": best.get("city", ""),
            "area": best.get("area", ""),
        },
        "_coord_system": "wgs84",
        "_autocorrect": {
            "applied": True,
            "corrector": "tianditu",
            "original_query": address,
            "corrected_query": best.get("name", ""),
            "region": region,
            "reason": "geocode_low_confidence_poi_candidate",
            "original_result": original.get("result"),
        },
        "candidates": candidates,
    }
    return corrected


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
    p_geocode.add_argument(
        "--autocorrect",
        action="store_true",
        help="百度地图低置信度时启用 POI 搜索纠错；返回 _autocorrect 和 candidates",
    )
    p_geocode.add_argument(
        "--region",
        default="",
        help="POI 纠错限定区域/城市，例如 武汉；仅 --provider baidu --autocorrect 使用",
    )
    p_geocode.add_argument(
        "--min-confidence",
        type=int,
        default=80,
        help="触发 POI 纠错的百度 geocode 最低置信度（默认 80）",
    )
    p_geocode.add_argument(
        "--autocorrect-provider",
        choices=["baidu", "tianditu"],
        default="baidu",
        help="POI 纠错搜索引擎（默认 baidu）；tianditu 需 TIANDITU_TK（服务端 key）",
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
            if provider == "baidu" and getattr(args, "autocorrect", False):
                corrector = getattr(args, "autocorrect_provider", "baidu")
                tianditu_tk = get_env(TIANDITU_ENV_KEY, "天地图") if corrector == "tianditu" else ""
                result = baidu_geocode_with_autocorrect(
                    args.address,
                    key,
                    region=getattr(args, "region", ""),
                    min_confidence=getattr(args, "min_confidence", 80),
                    corrector=corrector,
                    tianditu_tk=tianditu_tk,
                )
            else:
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
    if result.get("_coord_system", "").lower() == to:
        return  # 纠错结果已是目标坐标系（如天地图纠错返回 CGCS2000）
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
