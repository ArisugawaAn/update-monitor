"""YouTube Data API v3 备用通道（RSS 挂时的正式备用数据源，与 RSS 同构）。

调用链（quota 友好，禁用 search.list）：
  1. channels.list(part=contentDetails, id=<cid>) → uploads playlistId   [1 unit]
  2. playlistItems.list(part=snippet, playlistId=..., maxResults=5)      [1 unit]
合计每频道每次 ≈ 2 units（远低于 search.list 的 100 units）。

设计约束：
  - API Key 只从环境变量 YOUTUBE_API_KEY 读取，绝不写入代码
  - 结果交由 youtube.py 转为 Update，与 RSS 路线共用同一套去重/通知
  - RSS 正常时调用方不得调用本模块（由 youtube.py 的 fallback 条件保证）
"""
from __future__ import annotations

import requests

from monitor import config

_API = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = 20
_FETCH_COUNT = 5  # 备用通道：每频道最近 5 个视频（external_id 与 RSS 一致，去重无缝衔接）


def available() -> bool:
    """是否配置了 API Key（未配置 → fallback 自动跳过，不报错打扰主流程）。"""
    return bool(config.YOUTUBE_API_KEY)


def _get(path: str, params: dict) -> dict:
    """调一次 Data API；HTTP 错误/超时/连接异常统一抛给调用方判定。"""
    r = requests.get(f"{_API}/{path}", params=params,
                     timeout=_TIMEOUT, headers={"User-Agent": config.BROWSER_UA})
    if r.status_code != 200:
        raise RuntimeError(f"Data API {path} HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def fetch_uploads(cid: str) -> list[dict]:
    """返回最近 5 个视频 [{videoId, title, publishedAt, description, thumbnail}]。

    前置：调用方已确认 RSS 失败且本函数 available() 为 True。
    任何异常直接上抛，由 youtube.check_channel 转为 SourceError。
    """
    channels = _get("channels", {"part": "contentDetails", "id": cid,
                                 "key": config.YOUTUBE_API_KEY})
    items = channels.get("items") or []
    if not items:
        raise RuntimeError(f"channels.list 无此频道: {cid}")
    uploads = (((items[0].get("contentDetails") or {})
                .get("relatedPlaylists") or {}).get("uploads"))
    if not uploads:
        raise RuntimeError(f"频道 {cid} 无 uploads playlist")
    pl = _get("playlistItems", {"part": "snippet", "playlistId": uploads,
                                "maxResults": _FETCH_COUNT, "key": config.YOUTUBE_API_KEY})
    out: list[dict] = []
    for it in pl.get("items") or []:
        sn = it.get("snippet") or {}
        rid = (sn.get("resourceId") or {}).get("videoId") or ""
        if not rid:
            continue
        thumbs = sn.get("thumbnails") or {}
        thumb = ((thumbs.get("medium") or thumbs.get("default")) or {}).get("url", "")
        out.append({"videoId": rid,
                    "title": (sn.get("title") or "").strip(),
                    "publishedAt": sn.get("publishedAt") or "",
                    "description": (sn.get("description") or "").strip(),
                    "thumbnail": thumb})
    return out
