"""YouTube 源：官方频道 RSS 主通道（标准 Atom feed），feedparser 解析，无账号。

备用通道（POC）：RSS 返回 404/500、超时或连接异常时，自动尝试 YouTube Data API
v3（uploads playlist 路线，见 youtube_api.py）。RSS 正常时绝不调用 API；
RSS 失败 + 未配置 YOUTUBE_API_KEY 时直接报 RSS 的原始错误（行为与改动前一致）。

注：官方 feed 仅含最近 15 条视频，正常节奏（5 分钟轮询）下不会漏。
"""
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
    """RSS 失败后的备用通道：有 Key → 打一次 Data API（结果仅打印，不转 Update）。

    POC 约束：暂时不把 API 数据接入正式 Update/通知——API 成功也只返回 []
    （= 本轮无新动态），让调用方按“成功、零新”正常走完；API 自身失败则把
    RSS 原始错误 + API 错误一起上报。
    """
    from monitor.sources import youtube_api
    if not youtube_api.available():
        if cause is not None:
            raise SourceError(rss_err) from cause
        raise SourceError(rss_err)
    try:
        items = youtube_api.fetch_uploads(cid)  # 内部打印 videoId/title/publishedAt
    except Exception as e:
        msg = f"{rss_err}；Data API 备用通道也失败: {type(e).__name__}: {e}"
        raise SourceError(msg) from (cause or e)
    print(f"  [yt-fallback] {name}({cid}): RSS 失败({rss_err})，"
          f"Data API 返回 {len(items)} 条（POC 仅记录，不通知）")
    return []


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
