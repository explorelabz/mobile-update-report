"""数据源抓取：聚合 iOS / Android 官方公告与版本发布。

数据源说明：
- Apple Developer News & Updates RSS（含 SDK / Beta / Release 公告）
- Apple Developer Releases RSS（系统、Xcode、SDK 等版本发布）
- Android Developers Blog RSS（含 Android 主版本、Beta、Jetpack 等发布）
- Android Studio / Platform Releases（通过博客 tag 过滤）

为了最大化兼容性，全部使用公开 RSS。失败时不阻塞其他源。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable

import feedparser
import requests
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0 Safari/537.36 mobile-update-report/1.0"
)

IOS_FEEDS = [
    ("Apple Developer News", "https://developer.apple.com/news/rss/news.rss"),
    ("Apple Developer Releases", "https://developer.apple.com/news/releases/rss/releases.rss"),
]

ANDROID_FEEDS = [
    ("Android Developers Blog", "https://android-developers.googleblog.com/feeds/posts/default?alt=rss"),
    ("Android Studio Blog", "https://androidstudio.googleblog.com/feeds/posts/default?alt=rss"),
]


@dataclass
class UpdateItem:
    platform: str          # "iOS" | "Android"
    source: str            # 来源名称
    title: str
    url: str
    summary: str           # 纯文本摘要（已去 HTML）
    published: dt.datetime
    raw_id: str = ""       # 用于去重的稳定 ID

    def fingerprint(self) -> str:
        """稳定指纹：优先用 raw_id（如 RSS guid），回退到 url+title。"""
        base = self.raw_id or f"{self.url}|{self.title}"
        return hashlib.sha1(base.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published"] = self.published.isoformat()
        return d


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
def _fetch(url: str) -> bytes:
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=20)
    resp.raise_for_status()
    return resp.content


def _clean_html(html: str, max_len: int = 600) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "lxml").get_text(separator=" ", strip=True)
    return text[:max_len] + ("…" if len(text) > max_len else "")


def _parse_published(entry) -> dt.datetime:
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            return dt.datetime.fromtimestamp(time.mktime(val), tz=dt.timezone.utc)
    return dt.datetime.now(tz=dt.timezone.utc)


def _parse_feed(platform: str, source: str, url: str) -> list[UpdateItem]:
    try:
        raw = _fetch(url)
    except Exception as exc:
        logger.warning("[%s] 抓取失败 %s: %s", source, url, exc)
        return []

    feed = feedparser.parse(raw)
    items: list[UpdateItem] = []
    for entry in feed.entries:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        summary_html = (
            entry.get("summary")
            or entry.get("description")
            or (entry.get("content", [{}])[0].get("value") if entry.get("content") else "")
            or ""
        )
        items.append(
            UpdateItem(
                platform=platform,
                source=source,
                title=title,
                url=link,
                summary=_clean_html(summary_html),
                published=_parse_published(entry),
                raw_id=(entry.get("id") or entry.get("guid") or "").strip(),
            )
        )
    return items


def _filter_recent(items: Iterable[UpdateItem], lookback_days: int) -> list[UpdateItem]:
    cutoff = dt.datetime.now(tz=dt.timezone.utc) - dt.timedelta(days=lookback_days)
    return [it for it in items if it.published >= cutoff]


def _extract_article_text(html: str, max_len: int = 4000) -> str:
    """从详情页 HTML 里抽取主要正文，过滤导航/页脚/脚本。"""
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        tag.decompose()
    candidates = ["article", "main", ".article-content", ".post-body", ".section-content"]
    target = None
    for sel in candidates:
        el = soup.select_one(sel)
        if el and len(el.get_text(strip=True)) > 80:
            target = el
            break
    if target is None:
        target = soup.body or soup
    text = target.get_text(separator="\n", strip=True)
    # 折叠多余空行
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines)
    return text[:max_len]


def enrich(items: list[UpdateItem], min_summary_len: int = 200) -> list[UpdateItem]:
    """对 summary 太短的条目去抓详情页正文补充进 summary。

    - 如果抓到的详情页正文比 RSS summary 长很多，就把详情页正文拼到 summary 里
    - 抓不到 / 详情页内容也很短：保持原 summary 不变（让下游 prompt 知道信息有限）
    """
    enriched: list[UpdateItem] = []
    for it in items:
        summary = it.summary or ""
        if len(summary) >= min_summary_len:
            enriched.append(it)
            continue
        try:
            html = _fetch(it.url).decode("utf-8", errors="ignore")
            detail = _extract_article_text(html)
        except Exception as exc:
            logger.info("[enrich] 抓详情失败 %s: %s", it.url, exc)
            enriched.append(it)
            continue

        if len(detail) > len(summary) + 80:
            it.summary = (summary + "\n\n[详情页正文]\n" + detail).strip()
        enriched.append(it)
    return enriched


def fetch_ios(lookback_days: int) -> list[UpdateItem]:
    items: list[UpdateItem] = []
    for source, url in IOS_FEEDS:
        items.extend(_parse_feed("iOS", source, url))
    items = _filter_recent(items, lookback_days)
    items.sort(key=lambda x: x.published, reverse=True)
    return enrich(items)


def fetch_android(lookback_days: int) -> list[UpdateItem]:
    items: list[UpdateItem] = []
    for source, url in ANDROID_FEEDS:
        items.extend(_parse_feed("Android", source, url))
    items = _filter_recent(items, lookback_days)
    items.sort(key=lambda x: x.published, reverse=True)
    return enrich(items)
