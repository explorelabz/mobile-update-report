"""程序入口：抓取 -> 去重 -> AI 评估 -> Lark 推送。

用法:
    python main.py                # 正常跑一次
    python main.py --dry-run      # 不真的推 Lark，只在控制台打印
    python main.py --no-dedupe    # 忽略历史去重（首次跑/调试时有用）
    python main.py --platform ios # 只跑 iOS（或 android）
"""
from __future__ import annotations

import argparse
import logging
import sys

from analyzer import analyze
from config import SETTINGS
from lark_notifier import send
from sources import fetch_android, fetch_ios
from storage import filter_new, load_seen, save_seen

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


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
    return p.parse_args()


def run_platform(name: str, fetch_fn, seen: set[str], no_dedupe: bool, dry_run: bool) -> set[str]:
    logger.info("===== %s =====", name)
    items = fetch_fn(SETTINGS.lookback_days)
    logger.info("[%s] 抓到 %d 条原始更新", name, len(items))

    if no_dedupe:
        new_items, updated = items, seen
    else:
        new_items, updated = filter_new(items, seen)
    logger.info("[%s] 新条目 %d 条（去重后）", name, len(new_items))

    report = analyze(name, new_items)
    logger.info(
        "[%s] AI 评估完成：headline=%s, items=%d",
        name, report.get("headline"), len(report.get("items", [])),
    )

    if report.get("no_update") and not new_items:
        logger.info("[%s] 无新内容，跳过推送", name)
        return updated

    send(name, report, dry_run=dry_run)
    return updated


def main() -> int:
    args = parse_args()
    seen = set() if args.no_dedupe else load_seen()

    if args.platform in ("ios", "all"):
        seen = run_platform("iOS", fetch_ios, seen, args.no_dedupe, args.dry_run)
    if args.platform in ("android", "all"):
        seen = run_platform("Android", fetch_android, seen, args.no_dedupe, args.dry_run)

    if not args.no_dedupe and not args.dry_run:
        save_seen(seen)

    return 0


if __name__ == "__main__":
    sys.exit(main())
