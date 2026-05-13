"""调用 LLM 对抓取到的条目做评估，输出结构化结果。"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from openai import OpenAI

from config import SETTINGS
from sources import UpdateItem

logger = logging.getLogger(__name__)

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def _load_prompt(name: str) -> str:
    with open(os.path.join(_PROMPT_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


SYSTEM_PROMPT = _load_prompt("system.md")


def _client() -> OpenAI:
    if not SETTINGS.openai_api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY，请在 .env 中配置")
    return OpenAI(api_key=SETTINGS.openai_api_key, base_url=SETTINGS.openai_base_url)


def _build_user_payload(platform: str, items: list[UpdateItem]) -> str:
    item_lines = []
    for it in items:
        item_lines.append(
            json.dumps(
                {
                    "source": it.source,
                    "title": it.title,
                    "url": it.url,
                    "published": it.published.strftime("%Y-%m-%d"),
                    "summary": it.summary,
                },
                ensure_ascii=False,
            )
        )
    payload = {
        "platform": platform,
        "app_context": SETTINGS.app_context,
        "items": item_lines,
        "max_items_in_output": SETTINGS.max_items_per_platform,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _empty_report() -> dict[str, Any]:
    return {"headline": "今日无重要更新", "timeline": [], "items": [], "no_update": True}


def analyze(platform: str, items: list[UpdateItem]) -> dict[str, Any]:
    """对单个平台的更新条目调用 LLM，返回结构化报告 dict。"""
    if not items:
        return _empty_report()

    user_content = _build_user_payload(platform, items)

    try:
        client = _client()
        resp = client.chat.completions.create(
            model=SETTINGS.openai_model,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        )
        content = resp.choices[0].message.content or "{}"
        report = json.loads(content)
    except Exception as exc:
        logger.exception("AI 评估失败 platform=%s: %s", platform, exc)
        # 兜底：把抓到的原始条目透传，避免完全没有消息可推
        return {
            "headline": f"{platform} AI 评估失败，已透传原始条目",
            "timeline": [],
            "items": [
                {
                    "title": it.title,
                    "url": it.url,
                    "category": "版本时间线",
                    "priority": "P2",
                    "evidence": (it.summary or "原文未公开变更细节")[:120],
                    "impact": "AI 评估失败，请人工查阅原文后回填",
                    "action": "由 iOS/Android 负责人查阅原文后回填",
                    "deadline": "无",
                }
                for it in items[: SETTINGS.max_items_per_platform]
            ],
            "no_update": False,
            "_error": str(exc),
        }

    report.setdefault("timeline", [])
    report.setdefault("items", [])
    report.setdefault("no_update", not report["items"])
    return report
