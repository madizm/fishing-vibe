#!/usr/bin/env python3
"""Probe Douyin video comments for fishing spot clues.

This is a low-volume test tool, not a batch crawler. It opens one video URL via
OpenCLI browser, reads visible comment text, extracts likely place clues, and
optionally geocodes them with Tianditu.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEOCODE_SCRIPT = ROOT / ".agents" / "skills" / "tianditu-geocode" / "tianditu_geocode.py"

PLACE_HINTS = [
    "江", "河", "湖", "水库", "江滩", "闸", "桥", "泵站", "水厂", "码头", "村", "湾", "港", "沟", "渠", "公园",
]
NOISE = {
    "全部评论", "留下你的精彩评论吧", "大家都在搜：", "分享", "回复", "作者", "加载中", "关注", "推荐视频",
}


def run(cmd: list[str], timeout: int = 120) -> str:
    resolved = cmd[:]
    exe = shutil.which(resolved[0])
    if exe:
        resolved[0] = exe
    p = subprocess.run(resolved, cwd=ROOT, text=True, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout


def browser_eval(session: str, js: str) -> object:
    out = run(["opencli", "browser", session, "eval", js], timeout=90)
    return json.loads(out)


def extract_visible_comment_lines(url: str, session: str, scrolls: int, wait_seconds: float) -> list[str]:
    run(["opencli", "browser", session, "open", url], timeout=120)
    if wait_seconds:
        time.sleep(wait_seconds)
    for _ in range(scrolls):
        run(["opencli", "browser", session, "scroll", "down", "--amount", "1200"], timeout=60)
        if wait_seconds:
            time.sleep(wait_seconds)

    js = "(() => { const text = document.body.innerText || ''; const start = text.indexOf('全部评论'); const endMarks = ['下载客户端，桌面快捷访问', '广告投放', '用户服务协议']; let end = text.length; for (const mark of endMarks) { const idx = text.indexOf(mark, start >= 0 ? start : 0); if (idx > 0) end = Math.min(end, idx); } const slice = start >= 0 ? text.slice(start, end) : ''; return slice.split(/\\n+/).map(s => s.trim()).filter(Boolean); })()"
    data = browser_eval(session, js)
    return [str(x) for x in data] if isinstance(data, list) else []


def is_comment_candidate(line: str) -> bool:
    if line in NOISE or line == "...":
        return False
    if re.fullmatch(r"\d+", line) or re.fullmatch(r"\d{1,2}:\d{2}", line):
        return False
    if re.search(r"\d+天前|小时前|分钟前|·湖北|·武汉", line):
        return False
    if len(line) < 2 or len(line) > 80:
        return False
    return any(hint in line for hint in PLACE_HINTS) or "钓点" in line or "钓位" in line or "哪里" in line or "位置" in line


def extract_place_names(line: str, city: str) -> list[str]:
    # Keep this conservative: enough for the probe, final extraction can use LLM.
    candidates: list[str] = []
    related = re.sub(r"^(大家都在搜：)?", "", line).strip()
    for pattern in [
        r"([\u4e00-\u9fa5]{2,12}(?:江滩|水库|水厂|泵站|公园|闸|桥|码头|江|河|湖|村|湾|港|沟|渠))",
        r"([\u4e00-\u9fa5]{2,8}钓点)",
        r"([\u4e00-\u9fa5]{2,8}钓位)",
    ]:
        for m in re.finditer(pattern, related):
            name = m.group(1).strip(" ，,。.!！?？")
            name = re.sub(r"^(湖北省|武汉市|武汉|湖北|去|到|在)", "", name)
            name = re.sub(r"(黄尾钓点|钓点|钓位)$", "", name)
            if name and len(name) >= 2 and name not in candidates and name not in {city, "同款", "哪里"}:
                candidates.append(name)
    return candidates


def geocode(place: str, city: str) -> dict | None:
    query = place if place.startswith(city) else f"{city}{place}"
    out = run(["python", str(GEOCODE_SCRIPT), "geocode", query], timeout=60)
    data = json.loads(out)
    if data.get("status") != "0" or "location" not in data:
        return None
    loc = data["location"]
    return {
        "query": query,
        "lon": float(loc["lon"]),
        "lat": float(loc["lat"]),
        "score": int(loc.get("score", 0)),
        "level": loc.get("level", ""),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--session", default="douyin-comment-probe")
    ap.add_argument("--city", default="武汉")
    ap.add_argument("--scrolls", type=int, default=0)
    ap.add_argument("--wait", type=float, default=2.0)
    ap.add_argument("--no-geocode", action="store_true")
    args = ap.parse_args()

    lines = extract_visible_comment_lines(args.url, args.session, args.scrolls, args.wait)
    clue_lines = [line for line in lines if is_comment_candidate(line)]

    clues = []
    seen_places: set[str] = set()
    for line in clue_lines:
        places = extract_place_names(line, args.city)
        geocodes = []
        for place in places:
            if not args.no_geocode:
                geo = geocode(place, args.city)
                if geo:
                    geocodes.append({"place": place, **geo, "usable": geo["score"] >= 80})
            seen_places.add(place)
        clues.append({"text": line, "place_candidates": places, "geocodes": geocodes})

    print(json.dumps({
        "url": args.url,
        "visible_comment_lines": len(lines),
        "clue_lines": len(clues),
        "unique_places": sorted(seen_places),
        "clues": clues,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
