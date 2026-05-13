"""集中配置加载，所有外部参数都来自 .env / 环境变量。"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


def _get_int(key: str, default: int) -> int:
    raw = _get(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_base_url: str
    openai_model: str

    lark_webhook_url: str
    lark_secret: str

    lookback_days: int
    max_items_per_platform: int
    app_context: str


SETTINGS = Settings(
    openai_api_key=_get("OPENAI_API_KEY"),
    openai_base_url=_get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    openai_model=_get("OPENAI_MODEL", "gpt-4o-mini"),
    lark_webhook_url=_get("LARK_WEBHOOK_URL"),
    lark_secret=_get("LARK_SECRET"),
    lookback_days=_get_int("LOOKBACK_DAYS", 7),
    max_items_per_platform=_get_int("MAX_ITEMS_PER_PLATFORM", 8),
    app_context=_get(
        "APP_CONTEXT",
        "一款通用移动 App，关注合规、稳定性、性能、登录与推送相关变化。",
    ),
)


DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
SEEN_FILE = os.path.join(DATA_DIR, "seen.json")
