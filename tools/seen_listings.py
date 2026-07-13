from __future__ import annotations

import json
import os
import re
from datetime import date

_CACHE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "seen_listings.json",
)


def _load() -> dict[str, str]:
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict[str, str]) -> None:
    with open(_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def extract_listing_id(url: str) -> str | None:
    """Extracts the numeric item ID from a Facebook Marketplace URL."""
    m = re.search(r"/item/(\d+)", url or "")
    return m.group(1) if m else None


def is_seen(url: str) -> bool:
    listing_id = extract_listing_id(url)
    if not listing_id:
        return False
    return listing_id in _load()


def mark_seen(url: str, title: str = "") -> None:
    listing_id = extract_listing_id(url)
    if not listing_id:
        return
    data = _load()
    data[listing_id] = {"date": str(date.today()), "title": title}
    _save(data)


def count_seen() -> int:
    return len(_load())


def clear_seen() -> None:
    _save({})
