"""对抓取条目做评估，输出结构化结果。

默认不调用 OpenAI：用本地规则生成「影响 / 建议」。
可选 AI_PROVIDER=openai 时才走 OpenAI 兼容接口。
Cursor Automations 场景下，由 Agent 按 prompts/system.md 自行评估后
通过 main.py --push-report 推送，本模块仅作本地兜底。
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from config import SETTINGS
from sources import UpdateItem

logger = logging.getLogger(__name__)

_PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")


def _load_prompt(name: str) -> str:
    with open(os.path.join(_PROMPT_DIR, name), "r", encoding="utf-8") as f:
        return f.read()


SYSTEM_PROMPT = _load_prompt("system.md")

# 关键词 → (category, priority)
_SECURITY = re.compile(
    r"privacy|security|合规|隐私|加密|attest|play integrity|target.?sdk|"
    r"permission|权限|拒审|guideline|child.?safety|age.?rating|gambling|"
    r"malware|漏洞|CVE|signing|签名|keystore|biometric|生物识别",
    re.I,
)
_STABILITY = re.compile(
    r"behavior.?change|breaking|deprecat|废弃|lifecycle|scene|webview|"
    r"background|后台|push|notification|推送|crash|anr|compat|"
    r"migration|迁移|workmanager|job.?scheduler|network.?security",
    re.I,
)
_FEATURE = re.compile(
    r"introduc|new api|new feature|app intent|live activit|predictive back|"
    r"themed icon|compose|swiftui|widget|shortcut|siri|health|wallet|"
    r"新功能|新 API|增强",
    re.I,
)
_VERSION = re.compile(
    r"\b(beta|rc|release candidate|ga|canary|preview|stable)\b|"
    r"\bios\s*\d|\bandroid\s*\d|xcode\s*\d|android studio|"
    r"now available|已发布|正式版|发布",
    re.I,
)


def _empty_report() -> dict[str, Any]:
    return {"headline": "今日无重要更新", "timeline": [], "items": [], "no_update": True}


def _evidence(summary: str) -> str:
    text = (summary or "").strip()
    if not text or text.lower() in {"view downloads", "view release notes"}:
        return "原文未公开变更细节"
    return text[:120]


def _classify(title: str, summary: str) -> tuple[str, str]:
    blob = f"{title}\n{summary}"
    if _SECURITY.search(blob):
        return "安全合规", "P1"
    if _STABILITY.search(blob):
        return "稳定性影响", "P1"
    if _FEATURE.search(blob) and not _VERSION.search(title):
        return "新功能机会", "P2"
    return "版本时间线", "P2"


def _impact_for(category: str, title: str) -> str:
    ctx = SETTINGS.app_context or "移动 App"
    modules = "核心业务模块（登录 / 推送 / 网络 / 稳定性）"
    if "行情" in ctx or "金融" in ctx:
        modules = "行情 / 下单 / 资产 / 推送 / 生物识别"
    if category == "安全合规":
        return f"可能影响合规与上架审核，需对照 {modules} 相关实现尽快核对原文要求。"
    if category == "稳定性影响":
        return f"平台行为变化可能波及已发版功能（{modules}），建议回归验证关键路径。"
    if category == "新功能机会":
        return f"或可增强体验 / ASO，结合「{title[:40]}」评估是否纳入后续迭代。"
    return f"原文未公开变更细节，建议人工查阅 release notes 后再评估对 {modules} 的影响。"


def _action_for(category: str, platform: str) -> str:
    role = "iOS" if platform.lower().startswith("ios") else "Android"
    if category == "安全合规":
        return f"{role} + 安全负责人本周内对照官方要求出差距清单，明确是否需排期修复"
    if category == "稳定性影响":
        return f"{role} 负责人查阅原文后补充回归用例（冷启动 / 推送跳转 / 后台恢复）"
    if category == "新功能机会":
        return f"PM + {role} 评估 ROI，决定是否排入下个迭代 POC"
    return f"由 {role} 负责人查阅官方 release notes 后回填评估"


def analyze_local(platform: str, items: list[UpdateItem]) -> dict[str, Any]:
    """不调用外部 LLM：基于规则生成结构化报告。"""
    if not items:
        return _empty_report()

    capped = items[: SETTINGS.max_items_per_platform]
    out_items: list[dict[str, Any]] = []
    timeline: list[dict[str, str]] = []

    for it in capped:
        category, priority = _classify(it.title, it.summary)
        evidence = _evidence(it.summary)
        if evidence == "原文未公开变更细节" and category != "版本时间线":
            category, priority = "版本时间线", "P2"

        out_items.append({
            "title": it.title,
            "url": it.url,
            "category": category,
            "priority": priority,
            "evidence": evidence,
            "impact": _impact_for(category, it.title),
            "action": _action_for(category, platform),
            "deadline": "无",
        })

        if _VERSION.search(it.title) or category == "版本时间线":
            timeline.append({
                "label": it.title[:80],
                "date": it.published.strftime("%Y-%m-%d"),
                "note": (it.summary or "")[:80] or "版本 / 渠道更新",
            })

    # 选一条最高优先级做 headline
    order = {"P0": 0, "P1": 1, "P2": 2}
    top = sorted(out_items, key=lambda x: order.get(x["priority"], 9))[0]
    headline = f"{platform}：{top['title'][:36]}"
    if len(out_items) > 1:
        headline += f" 等 {len(out_items)} 条更新"

    return {
        "headline": headline,
        "timeline": timeline[:6],
        "items": out_items,
        "no_update": False,
        "_provider": "local",
    }


def analyze_openai(platform: str, items: list[UpdateItem]) -> dict[str, Any]:
    """可选：调用 OpenAI 兼容接口。失败时回退到本地规则评估。"""
    if not items:
        return _empty_report()
    if not SETTINGS.openai_api_key:
        raise RuntimeError("缺少 OPENAI_API_KEY，请在 .env 中配置，或改用 AI_PROVIDER=local")

    from openai import OpenAI

    client = OpenAI(api_key=SETTINGS.openai_api_key, base_url=SETTINGS.openai_base_url)
    item_lines = [
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
        for it in items
    ]
    user_content = json.dumps(
        {
            "platform": platform,
            "app_context": SETTINGS.app_context,
            "items": item_lines,
            "max_items_in_output": SETTINGS.max_items_per_platform,
        },
        ensure_ascii=False,
        indent=2,
    )
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
    report.setdefault("timeline", [])
    report.setdefault("items", [])
    report.setdefault("no_update", not report["items"])
    report["_provider"] = "openai"
    return report


def analyze(platform: str, items: list[UpdateItem]) -> dict[str, Any]:
    """按 AI_PROVIDER 选择评估路径。默认 local，不再因缺 key 写出「AI 评估失败」。"""
    if not items:
        return _empty_report()

    provider = (SETTINGS.ai_provider or "local").lower()
    if provider == "openai":
        try:
            return analyze_openai(platform, items)
        except Exception as exc:
            logger.exception("OpenAI 评估失败，回退本地规则 platform=%s: %s", platform, exc)
            report = analyze_local(platform, items)
            report["_error"] = f"OpenAI 失败已回退本地：{exc}"
            return report

    # local / cursor / agent / 其他 → 本地规则（Cursor Agent 应走 --dump-pending + --push-report）
    return analyze_local(platform, items)
