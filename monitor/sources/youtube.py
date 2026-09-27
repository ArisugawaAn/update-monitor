"""YouTube 源：官方频道 RSS 主通道（标准 Atom feed），feedparser 解析，无账号。

备用通道：RSS 返回 404/500、超时或连接异常时，自动改走 YouTube Data API v3
（uploads playlist 路线，见 youtube_api.py），结果与 RSS 一样转为 Update，
走同一套去重/通知。RSS 正常时绝不调用 API；RSS 失败 + 未配置 YOUTUBE_API_KEY
时直接报 RSS 的原始错误（行为与改动前一致）。

注：官方 feed 仅含最近 15 条视频，正常节奏（5 分钟轮询）下不会漏。
"""
from datetime import datetime
from email.utils import parsedate_to_datetime

import feedparser
import requests

from monitor import config
from monitor.models import SourceError, Update


def _rss_error_retryable(status: int | None, exc: Exception | None) -> bool:
    """RSS 失败是否值得 fallback：404/500/超时/连接异常 → True；其他照常报错。"""
    if exc is not None:  # requests 抛异常 = 超时或连接异常
        return isinstance(exc, (requests.Timeout, requests.ConnectionError))
    return status in (404, 500, 502, 503)


def check_channel(name: str, cid: str) -> list[Update]:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": config.BROWSER_UA})
    except Exception as e:
        if not _rss_error_retryable(None, e):
            raise SourceError(f"RSS 请求失败: {type(e).__name__}: {e}") from e
        return _fallback_or_raise(name, cid, f"RSS 请求失败: {type(e).__name__}: {e}", e)
    if r.status_code != 200:
        if not _rss_error_retryable(r.status_code, None):
            raise SourceError(f"HTTP {r.status_code}")
        return _fallback_or_raise(name, cid, f"HTTP {r.status_code}", None)
    feed = feedparser.parse(r.content)
    if feed.bozo and not feed.entries:
        raise SourceError(f"RSS 解析失败: {feed.bozo_exception}")
    return _to_updates(name, cid, feed.entries)


def _fallback_or_raise(name: str, cid: str, rss_err: str,
                       cause: Exception | None) -> list[Update]:
    """RSS 失败后的备用通道：有 Key → 打 Data API，结果转 Update 走正常通知。

    external_id 同为 videoId，与 RSS 路线互为连续去重；API 自身失败则把
    RSS 原始错误 + API 错误一起上报。
    """
    from monitor.sources import youtube_api
    if not youtube_api.available():
        if cause is not None:
            raise SourceError(rss_err) from cause
        raise SourceError(rss_err)
    try:
        items = youtube_api.fetch_uploads(cid)
    except Exception as e:
        msg = f"{rss_err}；Data API 备用通道也失败: {type(e).__name__}: {e}"
        raise SourceError(msg) from (cause or e)
    print(f"  [yt-fallback] {name}: RSS 失败({rss_err})，已改用 Data API（{len(items)} 条）")
    return _api_to_updates(name, cid, items)


def _parse_iso(pub: str) -> datetime | None:
    """Data API 的 publishedAt（ISO 8601 UTC，如 2026-09-11T15:00:31Z）→ datetime。"""
    try:
        return datetime.fromisoformat((pub or "").replace("Z", "+00:00"))
    except Exception:
        return None


def _api_to_updates(name: str, cid: str, items: list[dict]) -> list[Update]:
    """Data API 的 playlistItems 结果 → Update（与 RSS 路线同一数据模型）。"""
    out: list[Update] = []
    for it in items:
        vid = it.get("videoId") or ""
        if not vid:
            continue
        out.append(_mk_update(name, cid, vid, it.get("title") or "",
                              it.get("description") or "",
                              _parse_iso(it.get("publishedAt") or ""),
                              it.get("thumbnail") or ""))
    return out


def _mk_update(name: str, cid: str, vid: str, title: str, desc: str,
               pub: datetime | None, thumb: str) -> Update:
    return Update(
        source_key=f"youtube_{cid}", platform="youtube", account_name=name,
        content_type="video", external_id=vid,
        title=(title or "").strip(), text=(desc or "").strip()[:500],
        url=f"https://www.youtube.com/watch?v={vid}", published_at=pub,
        media_urls=[thumb] if thumb else [])


def _to_updates(name: str, cid: str, entries) -> list[Update]:
    out: list[Update] = []
    for e in entries:
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
        out.append(_mk_update(name, cid, vid, e.get("title") or "", desc, pub, thumb))
    return out


def source(name: str, cid: str):
    from types import SimpleNamespace
    return SimpleNamespace(key=f"youtube_{cid}", platform="youtube",
                           check=lambda: check_channel(name, cid))
