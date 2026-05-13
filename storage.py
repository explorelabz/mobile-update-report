"""轻量本地状态存储：用一个 JSON 文件记录已推送过的指纹，做去重。"""
from __future__ import annotations

import json
import os
from typing import Iterable

from config import DATA_DIR, SEEN_FILE


def _ensure_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def load_seen() -> set[str]:
    if not os.path.exists(SEEN_FILE):
        return set()
    try:
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(seen: Iterable[str]) -> None:
    _ensure_dir()
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(set(seen)), f, ensure_ascii=False, indent=2)


def filter_new(items, seen: set[str]):
    """返回 (新条目列表, 更新后的 seen 集合)。"""
    new_items = []
    updated = set(seen)
    for it in items:
        fp = it.fingerprint()
        if fp in updated:
            continue
        new_items.append(it)
        updated.add(fp)
    return new_items, updated
