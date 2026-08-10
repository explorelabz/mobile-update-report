"""程序入口：抓取 -> 评估 -> Lark 推送。

用法:
    python main.py                     # 本地规则评估后推送（默认，不调 OpenAI）
    python main.py --dry-run           # 不真的推 Lark，只在控制台打印
    python main.py --no-dedupe         # 不读写 seen.json（仍分析回溯窗口内全部条目）
    python main.py --platform ios      # 只跑 iOS（或 android）

Cursor Automations 两阶段（由 Agent 写影响/建议，不调 OpenAI）:
    python main.py --dump-pending      # 抓取，写出 data/pending.json
    python main.py --push-report PATH  # 读取 Agent 写好的报告 JSON 并推送

默认对回溯窗口内抓到的全部条目做评估并分平台推送（不再因「无新」发汇总卡或跳过）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from typing import Any

from analyzer import analyze
from config import DATA_DIR, SETTINGS
from lark_notifier import send
from sources import UpdateItem, fetch_android, fetch_ios
from storage import load_seen, save_seen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

PENDING_FILE = os.path.join(DATA_DIR, "pending.json")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="移动端每日更新监控")
    p.add_argument("--dry-run", action="store_true", help="不实际推送 Lark，仅打印")
    p.add_argument(
        "--no-dedupe",
        action="store_true",
        help="不读写 seen.json（评估仍覆盖回溯窗口内全部条目）",
    )
    p.add_argument(
        "--platform",
        choices=["ios", "android", "all"],
        default="all",
        help="只处理某个平台",
    )
    p.add_argument(
        "--dump-pending",
        action="store_true",
        help="仅抓取，写出 data/pending.json（供 Cursor Agent 评估）",
    )
    p.add_argument(
        "--push-report",
        metavar="PATH",
        help="读取 Agent 产出的报告 JSON 并推送 Lark，同时更新去重状态",
    )
    return p.parse_args()


def _collect_all(name: str, fetch_fn) -> list[UpdateItem]:
    """抓取回溯窗口内全部条目（不做「仅新条目」过滤）。"""
    logger.info("===== %s =====", name)
    items = fetch_fn(SETTINGS.lookback_days)
    logger.info("[%s] 抓到 %d 条（回溯 %d 天，全部纳入评估）", name, len(items), SETTINGS.lookback_days)
    return items


def _fingerprints(items: list[UpdateItem]) -> set[str]:
    return {it.fingerprint() for it in items}


def run_platform(
    name: str, fetch_fn, dry_run: bool, no_dedupe: bool,
) -> None:
    """抓取回溯窗口内全部条目 → 评估 → 推送。"""
    items = _collect_all(name, fetch_fn)
    report = analyze(name, items)
    logger.info(
        "[%s] 评估完成 provider=%s headline=%s items=%d",
        name,
        report.get("_provider", SETTINGS.ai_provider),
        report.get("headline"),
        len(report.get("items", [])),
    )
    send(name, report, dry_run=dry_run)
    if not no_dedupe and not dry_run:
        seen = load_seen()
        seen.update(_fingerprints(items))
        save_seen(seen)


def dump_pending(args: argparse.Namespace) -> int:
    """抓取回溯窗口内全部条目，写出 pending.json，不推送、不写 seen（等 push-report）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    platforms: dict[str, list[dict[str, Any]]] = {}
    fingerprints: list[str] = []

    if args.platform in ("ios", "all"):
        items = _collect_all("iOS", fetch_ios)
        platforms["iOS"] = [
            {
                "source": it.source,
                "title": it.title,
                "url": it.url,
                "published": it.published.strftime("%Y-%m-%d"),
                "summary": it.summary,
                "fingerprint": it.fingerprint(),
            }
            for it in items
        ]
        fingerprints.extend(x["fingerprint"] for x in platforms["iOS"])

    if args.platform in ("android", "all"):
        items = _collect_all("Android", fetch_android)
        platforms["Android"] = [
            {
                "source": it.source,
                "title": it.title,
                "url": it.url,
                "published": it.published.strftime("%Y-%m-%d"),
                "summary": it.summary,
                "fingerprint": it.fingerprint(),
            }
            for it in items
        ]
        fingerprints.extend(x["fingerprint"] for x in platforms["Android"])

    payload = {
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "lookback_days": SETTINGS.lookback_days,
        "app_context": SETTINGS.app_context,
        "max_items_per_platform": SETTINGS.max_items_per_platform,
        "platforms": platforms,
        "fingerprints": fingerprints,
        "evaluation_hint": (
            "请严格按 prompts/system.md 对每个平台输出报告。"
            "每个平台输出 headline / timeline / items / no_update；"
            "items 必须含 title,url,category,priority,evidence,impact,action,deadline。"
            "写完后调用: python main.py --push-report <你写的报告路径>"
        ),
    }
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    logger.info(
        "已写出 %s（iOS=%d Android=%d）",
        PENDING_FILE,
        len(platforms.get("iOS", [])),
        len(platforms.get("Android", [])),
    )
    print(PENDING_FILE)
    return 0


def push_report(path: str, dry_run: bool) -> int:
    """读取 Agent 报告并推送；合并 fingerprints 到 seen.json。"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    platforms = data.get("platforms") or {}
    if not platforms and any(k in data for k in ("headline", "items")):
        # 兼容单平台扁平报告：{"platform":"iOS", ...}
        plat = data.get("platform") or "iOS"
        platforms = {plat: data}

    for name, report in platforms.items():
        if not isinstance(report, dict):
            continue
        send(name, report, dry_run=dry_run)

    fps = set(data.get("fingerprints") or [])
    for report in platforms.values():
        if isinstance(report, dict):
            fps.update(report.get("fingerprints") or [])
    if not dry_run and fps:
        seen = load_seen()
        seen.update(fps)
        save_seen(seen)
        logger.info("已更新 seen.json，新增指纹 %d 个", len(fps))
    return 0


def main() -> int:
    args = parse_args()

    if args.dump_pending:
        return dump_pending(args)
    if args.push_report:
        return push_report(args.push_report, dry_run=args.dry_run)

    if args.platform in ("ios", "all"):
        run_platform("iOS", fetch_ios, args.dry_run, args.no_dedupe)
    if args.platform in ("android", "all"):
        run_platform("Android", fetch_android, args.dry_run, args.no_dedupe)

    return 0


if __name__ == "__main__":
    sys.exit(main())
