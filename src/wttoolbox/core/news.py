"""Official War Thunder news feed.

The news page is plain server-rendered HTML, so it can be parsed reliably
without an API key or a browser engine.  Locales verified working:
``zh`` (Chinese) and ``en``, ``ru``.

Results are cached on disk so the home page has content instantly, works
offline, and never hammers Gaijin's site.
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

from . import appdirs
from .applog import log

__all__ = ["NewsItem", "NewsResult", "fetch_news", "clear_cache", "LOCALES", "BASE_URL"]

BASE_URL = "https://warthunder.com"
LOCALES = [
    ("zh", "中文"),
    ("en", "English"),
    ("ru", "Русский"),
]
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 WTToolbox/1.0"
)

_BLOCK_RE = re.compile(
    r'<div class="showcase__item widget.*?(?=<div class="showcase__item widget|</section>)',
    re.S,
)
_LINK_RE = re.compile(r'<a[^>]*class="widget__link"[^>]*href="([^"]+)"')
_TITLE_RE = re.compile(r'<div class="widget__title">\s*(.*?)\s*</div>', re.S)
_COMMENT_RE = re.compile(r'<div class="widget__comment">\s*(.*?)\s*</div>', re.S)
_TAG_RE = re.compile(r'<div class="widget__tag[^"]*">\s*(.*?)\s*</div>', re.S)
_DATE_RE = re.compile(r'<div class="widget__date[^"]*">\s*(.*?)\s*</div>', re.S)
_IMG_RE = re.compile(r'data-src="([^"]+)"')
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass
class NewsItem:
    title: str
    url: str
    comment: str = ""
    image: str = ""
    tag: str = ""
    date: str = ""
    pinned: bool = False

    @property
    def short_comment(self) -> str:
        text = self.comment.strip()
        return text if len(text) <= 120 else text[:117] + "…"


@dataclass
class NewsResult:
    items: list[NewsItem] = field(default_factory=list)
    locale: str = "zh"
    fetched_at: float = 0.0
    from_cache: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.items)

    @property
    def age_text(self) -> str:
        if not self.fetched_at:
            return ""
        delta = max(0, time.time() - self.fetched_at)
        if delta < 90:
            return "刚刚更新"
        if delta < 3600:
            return f"{int(delta // 60)} 分钟前更新"
        if delta < 86400:
            return f"{int(delta // 3600)} 小时前更新"
        return time.strftime("%m-%d %H:%M", time.localtime(self.fetched_at))


def _cache_path(locale: str) -> str:
    return os.path.join(appdirs.cache_dir(), f"news_{locale}.json")


def _clean_text(raw: str) -> str:
    text = _TAG_STRIP_RE.sub(" ", raw or "")
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _absolute(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return BASE_URL + url
    return url


def parse_news_html(html_text: str, locale: str = "zh") -> list[NewsItem]:
    """Parse the news listing page into items (order preserved)."""
    items: list[NewsItem] = []
    for block in _BLOCK_RE.findall(html_text):
        link = _LINK_RE.search(block)
        title_match = _TITLE_RE.search(block)
        if not title_match:
            continue
        title = _clean_text(title_match.group(1))
        if not title:
            continue
        comment = _COMMENT_RE.search(block)
        tag = _TAG_RE.search(block)
        date = _DATE_RE.search(block)
        image = _IMG_RE.search(block)
        items.append(
            NewsItem(
                title=title,
                url=_absolute(link.group(1) if link else ""),
                comment=_clean_text(comment.group(1)) if comment else "",
                image=_absolute(image.group(1) if image else ""),
                tag=_clean_text(tag.group(1)) if tag else "",
                date=_clean_text(date.group(1)) if date else "",
                pinned="widget__pin" in block,
            )
        )
    return items


def _load_cache(locale: str) -> NewsResult | None:
    path = _cache_path(locale)
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        items = [NewsItem(**item) for item in data.get("items", [])]
        if not items:
            return None
        return NewsResult(
            items=items,
            locale=data.get("locale", locale),
            fetched_at=float(data.get("fetched_at") or 0),
            from_cache=True,
        )
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _save_cache(result: NewsResult) -> None:
    path = _cache_path(result.locale)
    payload = {
        "locale": result.locale,
        "fetched_at": result.fetched_at,
        "items": [asdict(item) for item in result.items],
    }
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except OSError:
        pass


def fetch_news(
    locale: str = "zh",
    *,
    max_age_seconds: float = 1800,
    timeout: float = 20.0,
    force: bool = False,
) -> NewsResult:
    """Return official news, using the on-disk cache when it is fresh.

    Network failures degrade to stale cache rather than an empty panel.
    """
    cached = _load_cache(locale)
    if cached and not force:
        if time.time() - cached.fetched_at < max_age_seconds:
            cached.error = ""
            return cached

    url = f"{BASE_URL}/{locale}/news/"
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept-Language": locale})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
        text = raw.decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        message = f"获取官方资讯失败：{exc}"
        log.warn(message, "资讯")
        if cached:
            cached.error = message
            return cached
        return NewsResult(locale=locale, error=message)

    items = parse_news_html(text, locale)
    if not items:
        message = "官方资讯页面结构已变化，未能解析出内容"
        log.warn(message, "资讯")
        if cached:
            cached.error = message
            return cached
        return NewsResult(locale=locale, error=message)

    result = NewsResult(
        items=items, locale=locale, fetched_at=time.time(), from_cache=False
    )
    _save_cache(result)
    log.info(f"已获取 {len(items)} 条官方资讯（{locale}）", "资讯")
    return result


def clear_cache() -> int:
    removed = 0
    for locale, _label in LOCALES:
        path = _cache_path(locale)
        if os.path.exists(path):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    return removed
