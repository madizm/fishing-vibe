"""Fixture loaders: saved search items / page extracts / comments, used by the
extract-only debug path and by tests."""

from __future__ import annotations

import json
from pathlib import Path


def _read_json_file(path: str | Path) -> object:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def _coerce_search_item(data: object, url: str = "") -> dict:
    if isinstance(data, list):
        items = [x for x in data if isinstance(x, dict)]
        if url:
            for item in items:
                if item.get("url") == url:
                    return item
        return items[0] if items else {"url": url, "desc": "", "author": ""}
    if isinstance(data, dict):
        for key in ("item", "search_item", "video"):
            value = data.get(key)
            if isinstance(value, dict):
                return _coerce_search_item(value, url=url)
        for key in ("items", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return _coerce_search_item(value, url=url)
        item = dict(data)
        if url and not item.get("url"):
            item["url"] = url
        item.setdefault("desc", "")
        item.setdefault("author", "")
        return item
    return {"url": url, "desc": "", "author": ""}


def load_search_item_fixture(path: str, url: str = "") -> dict:
    return _coerce_search_item(_read_json_file(path), url=url) if path else {"url": url, "desc": "", "author": ""}


def load_extracted_fixture(json_path: str = "", text_path: str = "") -> dict:
    if json_path and text_path:
        raise ValueError("--extracted-json and --extracted-text cannot be used together")
    if json_path:
        data = _read_json_file(json_path)
        if not isinstance(data, dict):
            raise ValueError("--extracted-json must contain a JSON object")
        if isinstance(data.get("extracted"), dict):
            data = data["extracted"]
        extracted = dict(data)
        if "content" not in extracted:
            for key in ("markdown", "text", "body"):
                if key in extracted:
                    extracted["content"] = extracted[key]
                    break
        return extracted
    if text_path:
        text = Path(text_path).read_text(encoding="utf-8")
        return {"title": Path(text_path).stem, "content": text}
    raise ValueError("missing extracted fixture path")


def load_comments_fixture(path: str) -> list[dict]:
    data = _read_json_file(path)
    if isinstance(data, dict):
        for key in ("comments", "items", "results", "data"):
            value = data.get(key)
            if isinstance(value, list):
                data = value
                break
    if not isinstance(data, list):
        raise ValueError("--comments-json must contain a list or an object with a comments list")
    return [dict(x) for x in data if isinstance(x, dict)]
