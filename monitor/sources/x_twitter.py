"""X 源（已 POC 验证）：nitter 公共镜像 RSS，完全无账号。

候选链（按序轮试，全部失败才报错）：
  1. 直接镜像（RSS 阅读器 UA）
  2. 经 r.jina.ai 免费代抓同一批镜像 —— Actions 的数据中心 IP 被镜像
     Cloudflare 拦截时的兜底（jina 从自己的服务器出口访问）
"""
import re
from email.utils import parsedate_to_datetime

import feedparser
import requests

from monitor import config
from monitor.models import SourceError, Update


def _candidates(user: str | None = None):
    handle = user or config.X_USER
    out = []
    for mirror in config.X_MIRRORS:
        out.append((f"{mirror}/{handle}/rss", f"direct:{mirror}",
                    {"User-Agent": config.X_UA}, 15))
    for mirror in config.X_MIRRORS:
        out.append((f"{config.X_PROXY}/{mirror}/{handle}/rss", f"jina:{mirror}",
                    {"User-Agent": config.BROWSER_UA}, 45))
    return out


def _fetch_entries(user: str | None = None) -> tuple[list, list[str]]:
    errors: list[str] = []
    for url, label, headers, timeout in _candidates(user):
        try:
            r = requests.get(url, timeout=timeout, headers=headers)
        except Exception as e:
            errors.append(f"{label}: {type(e).__name__}")
            continue
        if r.status_code != 200:
            errors.append(f"{label}: HTTP {r.status_code}")
            continue
        text = r.text
        idx = text.find("<?xml")  # jina 返回值可能带前导说明，截取 XML 本体
        if idx > 0:
            text = text[idx:]
        feed = feedparser.parse(text)
        if feed.bozo and not feed.entries:
            errors.append(f"{label}: 解析失败")
            continue
        if "not yet whitelisted" in (feed.feed.get("title") or ""):
            errors.append(f"{label}: RSS 白名单未批")
            continue
        if not feed.entries:
            errors.append(f"{label}: 空 feed")
            continue
        return list(feed.entries), errors
    raise SourceError("全部候选失败: " + "; ".join(errors))


def check(user: str | None = None) -> list[Update]:
    handle = user or config.X_USER
    key = "x" if handle == config.X_USERS[0] else f"x_{handle}"
    entries, _ = _fetch_entries(handle)
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
            source_key=key, platform="x", account_name=f"@{handle}",
            content_type="tweet", external_id=ext, title=title, text=title,
            url=f"https://x.com/{handle}/status/{ext}", published_at=pub))
    return out


def source(user: str | None = None):
    from types import SimpleNamespace
    handle = user or config.X_USER
    key = "x" if handle == config.X_USERS[0] else f"x_{handle}"
    return SimpleNamespace(key=key, platform="x",
                           check=lambda: check(handle))
