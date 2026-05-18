"""飞书 Lark 自定义机器人推送。

支持：
- 普通 Webhook
- 加签校验（在机器人安全设置里启用 Secret 后必填）
- interactive 卡片消息（更美观，支持颜色/分组）
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import logging
import time
from typing import Any

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

from config import SETTINGS

logger = logging.getLogger(__name__)

PLATFORM_THEME = {
    "iOS": {"emoji": "🍎", "color": "blue", "title_color": "blue"},
    "Android": {"emoji": "🤖", "color": "green", "title_color": "green"},
}

PRIORITY_TAG = {
    "P0": {"text": "P0 紧急", "color": "red"},
    "P1": {"text": "P1 重要", "color": "orange"},
    "P2": {"text": "P2 关注", "color": "grey"},
}

CATEGORY_EMOJI = {
    "新功能机会": "🆕",
    "安全合规": "🛡️",
    "稳定性影响": "⚠️",
    "版本时间线": "📅",
}

CATEGORY_COLOR = {
    "新功能机会": "blue",
    "安全合规": "red",
    "稳定性影响": "orange",
    "版本时间线": "grey",
}


def _sign(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(hmac_code).decode("utf-8")


def _build_card(platform: str, report: dict[str, Any]) -> dict[str, Any]:
    theme = PLATFORM_THEME.get(platform, {"emoji": "📱", "color": "grey", "title_color": "grey"})
    today = dt.datetime.now().strftime("%Y-%m-%d")
    headline = report.get("headline") or "今日无重要更新"

    elements: list[dict[str, Any]] = []

    elements.append({
        "tag": "div",
        "text": {
            "tag": "lark_md",
            "content": f"**📢 一句话摘要**\n{headline}",
        },
    })

    timeline = report.get("timeline") or []
    if timeline:
        lines = ["**📅 版本时间线**"]
        for tl in timeline:
            label = tl.get("label", "")
            date = tl.get("date", "")
            note = tl.get("note", "")
            lines.append(f"• `{date}` **{label}** — {note}")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}})

    items = report.get("items") or []
    if not items and report.get("no_update"):
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": "_今日没有需要立即关注的更新。_"},
        })
    else:
        # 按分类分组，业务方便扫读：安全合规 → 稳定性影响 → 新功能机会 → 版本时间线
        order = ["安全合规", "稳定性影响", "新功能机会", "版本时间线"]
        grouped: dict[str, list[dict[str, Any]]] = {k: [] for k in order}
        for it in items:
            cat = it.get("category", "版本时间线")
            grouped.setdefault(cat, []).append(it)

        section_titles = {
            "安全合规": "🛡️ 安全 / 合规（不做有风险）",
            "稳定性影响": "⚠️ 稳定性影响（可能影响已发版功能）",
            "新功能机会": "🆕 新功能机会（评估是否跟进）",
            "版本时间线": "📅 版本时间线（信息性）",
        }

        idx_global = 0
        for cat in order:
            bucket = grouped.get(cat) or []
            if not bucket:
                continue
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": f"**{section_titles[cat]}**"},
            })
            for it in bucket:
                idx_global += 1
                priority = it.get("priority", "P2")
                tag = PRIORITY_TAG.get(priority, PRIORITY_TAG["P2"])
                title = it.get("title", "(无标题)")
                url = it.get("url", "")
                evidence = (it.get("evidence") or "").strip()
                impact = it.get("impact", "")
                action = it.get("action", "")
                deadline = it.get("deadline") or "无"

                cat_color = CATEGORY_COLOR.get(cat, "grey")
                title_md = f"[{title}]({url})" if url else title
                evidence_line = ""
                if evidence:
                    if evidence == "原文未公开变更细节":
                        evidence_line = (
                            f"**📄 原文证据**：<font color='grey'>原文未公开变更细节，需人工查阅</font>\n"
                        )
                    else:
                        evidence_line = f"**📄 原文证据**：<font color='grey'>「{evidence}」</font>\n"

                content = (
                    f"**{idx_global}. {title_md}**\n"
                    f"<font color='{tag['color']}'>**{tag['text']}**</font> · "
                    f"<font color='{cat_color}'>**{cat}**</font> · 截止：{deadline}\n"
                    f"{evidence_line}"
                    f"**💥 影响**：{impact}\n"
                    f"**✅ 建议**：{action}"
                )
                elements.append({"tag": "div", "text": {"tag": "lark_md", "content": content}})

    if report.get("_error"):
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": f"⚠️ AI 评估异常：{report['_error']}"}],
        })

    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": f"数据更新于 {today} · mobile-update-report"}],
    })

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": theme["title_color"],
                "title": {
                    "tag": "plain_text",
                    "content": f"{theme['emoji']} {platform} 每日更新评估 · {today}",
                },
            },
            "elements": elements,
        },
    }


def _build_all_quiet_card() -> dict[str, Any]:
    """iOS / Android 均无新条目时的单日一条摘要卡片。"""
    today = dt.datetime.now().strftime("%Y-%m-%d")
    elements: list[dict[str, Any]] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    "**📭 当日无新信息**\n"
                    "在配置的回溯窗口内，**iOS** 与 **Android** 数据源均未出现需要去重的新条目，"
                    "因此没有需要分平台推送的评估内容。"
                ),
            },
        },
        {
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": f"数据检查于 {today} · mobile-update-report"}],
        },
    ]
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "grey",
                "title": {"tag": "plain_text", "content": f"📱 iOS / Android · 当日无新信息 · {today}"},
            },
            "elements": elements,
        },
    }


def _assert_lark_ok(result: dict[str, Any], *, context: str) -> None:
    code = result.get("code", result.get("StatusCode"))
    if code is not None:
        try:
            c = int(code)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"Lark 推送返回无法解析的 code {context} response={result!r}") from exc
        if c != 0:
            raise RuntimeError(f"Lark 推送返回错误 {context} response={result!r}")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    resp = requests.post(url, json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post_lark_interactive(body: dict[str, Any], *, dry_run: bool, success_log: str) -> None:
    if SETTINGS.lark_secret:
        ts = int(time.time())
        body["timestamp"] = str(ts)
        body["sign"] = _sign(ts, SETTINGS.lark_secret)

    if dry_run:
        import json
        logger.info("[dry-run] 将推送到 Lark：\n%s", json.dumps(body, ensure_ascii=False, indent=2))
        return

    if not SETTINGS.lark_webhook_url:
        raise RuntimeError("缺少 LARK_WEBHOOK_URL，请在 .env 中配置")

    result = _post(SETTINGS.lark_webhook_url, body)
    _assert_lark_ok(result, context=success_log)
    logger.info("Lark 推送成功 %s", success_log)


def send(platform: str, report: dict[str, Any], dry_run: bool = False) -> None:
    body = _build_card(platform, report)
    _post_lark_interactive(body, dry_run=dry_run, success_log=f"platform={platform}")


def send_all_quiet_digest(dry_run: bool = False) -> None:
    """双平台均无新条目时推送一条汇总卡片。"""
    body = _build_all_quiet_card()
    _post_lark_interactive(body, dry_run=dry_run, success_log="digest=iOS+Android 当日无新信息")
