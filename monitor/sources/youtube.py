"""YouTube 源：官方频道 RSS（标准 Atom feed），feedparser 解析，无账号。

注：官方 feed 仅含最近 15 条视频，正常节奏（5 分钟轮询）下不会漏。
"""
from email.utils import parsedate_to_datetime

import feedparser
import requests

from monitor import config
from monitor.models import SourceError, Update


def check_channel(name: str, cid: str) -> list[Update]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": config.BROWSER_UA})
    except Exception as e:
        raise SourceError(f"RSS 请求失败: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code}")
    feed = feedparser.parse(r.content)
    if feed.bozo and not feed.entries:
        raise SourceError(f"RSS 解析失败: {feed.bozo_exception}")
    out: list[Update] = []
    for e in feed.entries:
        vid = e.get("yt_videoid") or ""
        if not vid:
            continue
        pub = None
        if e.get("published"):
            try:
                pub = parsedate_to_datetime(e.published)
            except Exception:
                pub = None
        thumb = ""
        if e.get("media_thumbnail"):
            thumb = e["media_thumbnail"][0].get("url", "")
        desc = (e.get("media_description") or e.get("summary") or "").strip()
        out.append(Update(
            source_key=f"youtube_{cid}", platform="youtube", account_name=name,
            content_type="video", external_id=vid,
            title=(e.get("title") or "").strip(), text=desc[:500],
            url=f"https://www.youtube.com/watch?v={vid}", published_at=pub,
            media_urls=[thumb] if thumb else []))
    return out


def source(name: str, cid: str):
    from types import SimpleNamespace
    return SimpleNamespace(key=f"youtube_{cid}", platform="youtube",
                           check=lambda: check_channel(name, cid))
