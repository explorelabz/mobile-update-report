"""程序入口：抓取 -> 去重 -> AI 评估 -> Lark 推送。

用法:
    python main.py                # 正常跑一次
    python main.py --dry-run      # 不真的推 Lark，只在控制台打印
    python main.py --no-dedupe    # 忽略历史去重（首次跑/调试时有用）
    python main.py --platform ios # 只跑 iOS（或 android）

双平台均无新条目时（默认 --platform all）：推送一条「当日无新信息」汇总卡片，不再静默。
"""
from __future__ import annotations

import argparse
import logging
import sys

from analyzer import analyze
from config import SETTINGS
from lark_notifier import send, send_all_quiet_digest
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


def run_platform(name: str, fetch_fn, seen: set[str], no_dedupe: bool, dry_run: bool) -> tuple[set[str], bool]:
    """处理单个平台。返回 (更新后的 seen, 是否因「无新条目」未发分平台卡片)。"""
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
        logger.info("[%s] 无新内容，不发分平台卡片", name)
        if not no_dedupe and not dry_run:
            save_seen(updated)
        return updated, True

    send(name, report, dry_run=dry_run)
    if not no_dedupe and not dry_run:
        save_seen(updated)
    return updated, False


def main() -> int:
    args = parse_args()
    seen = set() if args.no_dedupe else load_seen()

    ios_quiet: bool | None = None
    android_quiet: bool | None = None

    if args.platform in ("ios", "all"):
        seen, ios_quiet = run_platform("iOS", fetch_ios, seen, args.no_dedupe, args.dry_run)
    if args.platform in ("android", "all"):
        seen, android_quiet = run_platform("Android", fetch_android, seen, args.no_dedupe, args.dry_run)

    if args.platform == "all" and ios_quiet and android_quiet:
        logger.info("iOS 与 Android 均无新条目，推送一条「当日无新信息」摘要")
        send_all_quiet_digest(dry_run=args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
