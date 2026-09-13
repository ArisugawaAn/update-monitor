"""X 源（已 POC 验证）：nitter 公共镜像 RSS，完全无账号。

镜像按优先级轮试，全部失败才报错（不影响其他来源）。
"""
import re
from email.utils import parsedate_to_datetime

import feedparser
import requests

from monitor import config
from monitor.models import SourceError, Update


def _fetch_entries() -> tuple[list, list[str]]:
    errors: list[str] = []
    for mirror in config.X_MIRRORS:
        url = f"{mirror}/{config.X_USER}/rss"
        try:
            r = requests.get(url, timeout=15, headers={"User-Agent": config.X_UA})
        except Exception as e:
            errors.append(f"{mirror}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            errors.append(f"{mirror}: HTTP {r.status_code}")
            continue
        feed = feedparser.parse(r.content)
        if feed.bozo and not feed.entries:
            errors.append(f"{mirror}: 解析失败")
            continue
        if "not yet whitelisted" in (feed.feed.get("title") or ""):
            errors.append(f"{mirror}: RSS 白名单未批")
            continue
        if not feed.entries:
            errors.append(f"{mirror}: 空 feed")
            continue
        return list(feed.entries), errors
    raise SourceError("全部镜像失败: " + "; ".join(errors))


def check() -> list[Update]:
    entries, _ = _fetch_entries()
    out: list[Update] = []
    for e in entries:
        link = e.get("link") or ""
        m = re.search(r"/status/(\d+)", link)
        if not m:
            continue
        ext = m.group(1)
        title = (e.get("title") or "").strip()
        pub = None
        if e.get("published"):
            try:
                pub = parsedate_to_datetime(e.published)
            except Exception:
                pub = None
        out.append(Update(
            source_key="x", platform="x", account_name=f"@{config.X_USER}",
            content_type="tweet", external_id=ext, title=title, text=title,
            url=f"https://x.com/{config.X_USER}/status/{ext}", published_at=pub))
    return out


def source():
    from types import SimpleNamespace
    return SimpleNamespace(key="x", platform="x", check=check)
