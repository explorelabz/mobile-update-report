"""程序入口：抓取 -> 去重 -> 评估 -> Lark 推送。

用法:
    python main.py                     # 本地规则评估后推送（默认，不调 OpenAI）
    python main.py --dry-run           # 不真的推 Lark，只在控制台打印
    python main.py --no-dedupe         # 忽略历史去重
    python main.py --platform ios      # 只跑 iOS（或 android）

Cursor Automations 两阶段（由 Agent 写影响/建议，不调 OpenAI）:
    python main.py --dump-pending      # 抓取+去重，写出 data/pending.json
    python main.py --push-report PATH  # 读取 Agent 写好的报告 JSON 并推送

双平台均无新条目时（默认 --platform all）：推送一条「当日无新信息」汇总卡片。
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
from lark_notifier import send, send_all_quiet_digest
from sources import UpdateItem, fetch_android, fetch_ios
from storage import filter_new, load_seen, save_seen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")

PENDING_FILE = os.path.join(DATA_DIR, "pending.json")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="移动端每日更新监控")
    p.add_argument("--dry-run", action="store_true", help="不实际推送 Lark，仅打印")
    p.add_argument("--no-dedupe", action="store_true", help="忽略历史去重")
    p.add_argument(
        "--platform",
        choices=["ios", "android", "all"],
        default="all",
        help="只处理某个平台",
    )
    p.add_argument(
        "--dump-pending",
        action="store_true",
        help="仅抓取+去重，写出 data/pending.json（供 Cursor Agent 评估）",
    )
    p.add_argument(
        "--push-report",
        metavar="PATH",
        help="读取 Agent 产出的报告 JSON 并推送 Lark，同时更新去重状态",
    )
    return p.parse_args()


def _collect_new(
    name: str, fetch_fn, seen: set[str], no_dedupe: bool,
) -> tuple[list[UpdateItem], set[str]]:
    logger.info("===== %s =====", name)
    items = fetch_fn(SETTINGS.lookback_days)
    logger.info("[%s] 抓到 %d 条原始更新", name, len(items))
    if no_dedupe:
        new_items, updated = items, set(seen)
        for it in items:
            updated.add(it.fingerprint())
    else:
        new_items, updated = filter_new(items, seen)
    logger.info("[%s] 新条目 %d 条（去重后）", name, len(new_items))
    return new_items, updated


def run_platform(
    name: str, fetch_fn, seen: set[str], no_dedupe: bool, dry_run: bool,
) -> tuple[set[str], bool, dict[str, Any]]:
    """处理单个平台。返回 (seen, 是否无新条目, 评估报告)。"""
    new_items, updated = _collect_new(name, fetch_fn, seen, no_dedupe)

    report = analyze(name, new_items)
    logger.info(
        "[%s] 评估完成 provider=%s headline=%s items=%d",
        name,
        report.get("_provider", SETTINGS.ai_provider),
        report.get("headline"),
        len(report.get("items", [])),
    )

    if report.get("no_update") and not new_items:
        logger.info("[%s] 无新条目，稍后决定是否推送卡片", name)
        if not no_dedupe and not dry_run:
            save_seen(updated)
        return updated, True, report

    send(name, report, dry_run=dry_run)
    if not no_dedupe and not dry_run:
        save_seen(updated)
    return updated, False, report


def dump_pending(args: argparse.Namespace) -> int:
    """抓取+去重，写出 pending.json，不推送、不写 seen（等 push-report）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    seen = set() if args.no_dedupe else load_seen()
    platforms: dict[str, list[dict[str, Any]]] = {}
    fingerprints: list[str] = []

    if args.platform in ("ios", "all"):
        items, _ = _collect_new("iOS", fetch_ios, seen, args.no_dedupe)
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
        items, _ = _collect_new("Android", fetch_android, seen, args.no_dedupe)
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
    logger.info("已写出 %s（iOS=%d Android=%d）", PENDING_FILE,
                len(platforms.get("iOS", [])), len(platforms.get("Android", [])))
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

    quiet_flags: dict[str, bool] = {}
    for name, report in platforms.items():
        if not isinstance(report, dict):
            continue
        items = report.get("items") or []
        no_update = bool(report.get("no_update", not items))
        quiet_flags[name] = no_update and not items
        if quiet_flags[name]:
            logger.info("[%s] 报告标记无新内容", name)
            continue
        send(name, report, dry_run=dry_run)

    # 双平台都安静 → 推汇总
    names = list(platforms.keys())
    if len(names) >= 2 and all(quiet_flags.get(n, True) for n in names):
        logger.info("各平台均无新条目，推送「当日无新信息」摘要")
        send_all_quiet_digest(dry_run=dry_run)
    else:
        for name, quiet in quiet_flags.items():
            if quiet and name in platforms:
                send(name, platforms[name], dry_run=dry_run)

    fps = set(data.get("fingerprints") or [])
    for report in platforms.values():
        if isinstance(report, dict):
            fps.update(report.get("fingerprints") or [])
    # pending 里的 fingerprint 也接受从同级字段合并
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

    seen = set() if args.no_dedupe else load_seen()

    ios_quiet: bool | None = None
    android_quiet: bool | None = None
    ios_report: dict[str, Any] | None = None
    android_report: dict[str, Any] | None = None

    if args.platform in ("ios", "all"):
        seen, ios_quiet, ios_report = run_platform("iOS", fetch_ios, seen, args.no_dedupe, args.dry_run)
    if args.platform in ("android", "all"):
        seen, android_quiet, android_report = run_platform(
            "Android", fetch_android, seen, args.no_dedupe, args.dry_run,
        )

    if args.platform == "all" and ios_quiet and android_quiet:
        logger.info("iOS 与 Android 均无新条目，推送一条「当日无新信息」摘要")
        send_all_quiet_digest(dry_run=args.dry_run)
    else:
        if ios_quiet and ios_report is not None and args.platform in ("ios", "all"):
            logger.info("[iOS] 无新条目，推送「无重要更新」分平台卡片")
            send("iOS", ios_report, dry_run=args.dry_run)
        if android_quiet and android_report is not None and args.platform in ("android", "all"):
            logger.info("[Android] 无新条目，推送「无重要更新」分平台卡片")
            send("Android", android_report, dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
