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
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Optional

REQUEST_TIMEOUT = 15

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
        description="地理编码 / 逆地理编码。支持百度地图（baidu）与天地图（tianditu）。"
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

    p_reverse = subparsers.add_parser("reverse", help="经纬度 -> 地址")
    p_reverse.add_argument("lon", type=float, help="经度")
    p_reverse.add_argument("lat", type=float, help="纬度")

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

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
        elif args.command == "reverse":
            result = reverse_fn(args.lon, args.lat, key)
        else:
            parser.error(f"未知命令：{args.command}")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
