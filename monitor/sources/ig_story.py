"""Instagram Story 源（已 POC 验证）：reels_media XHR，重复参数形态，每轮 1 请求。

会话：环境变量 IG_COOKIE（生产）或本地 instaloader session 文件（POC）。
"""
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from monitor import config
from monitor.models import CheckpointError, SourceError, Update

_API = "https://www.instagram.com/api/v1/feed/reels_media/"


def _headers() -> dict:
    return {
        "User-Agent": config.BROWSER_UA,
        "X-IG-App-ID": config.IG_WEB_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/",
    }


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(_headers())
    cookie = (config.IG_COOKIE or "").strip()
    if cookie:  # 生产路径：整串 Cookie 头
        for part in cookie.replace("\n", ";").split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k and v:
                    s.cookies.set(k.strip(), v.strip(), domain=".instagram.com")
        csrf = s.cookies.get("csrftoken")
        if csrf:
            s.headers["X-CSRFToken"] = csrf
        return s
    # 本地 POC 路径：instaloader session 文件（惰性导入，生产无需安装）
    import instaloader
    L = instaloader.Instaloader(
        download_pictures=False, download_videos=False,
        download_video_thumbnails=False, save_metadata=False,
        compress_json=False, max_connection_attempts=1, quiet=True)
    sf = Path(__file__).resolve().parents[2] / f"session-{config.IG_SESSION_USER}"
    L.load_session_from_file(config.IG_SESSION_USER, str(sf))
    s2 = L.context._session
    s2.headers.update(_headers())
    return s2


def check() -> list[Update]:
    s = _session()
    ids = [uid for _, uid in config.IG_ACCOUNTS]
    try:
        r = s.get(_API, params=[("user_ids", str(i)) for i in ids], timeout=20)
    except Exception as e:
        raise SourceError(f"请求异常: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        if "checkpoint_required" in r.text:
            raise CheckpointError("IG 账号被要求 checkpoint 验证，已停止本源")
        if r.status_code in (401, 403) or "login_required" in r.text:
            raise SourceError("会话失效(401/403)，需重新导入 Cookie")
        if "feedback_required" in r.text:
            raise SourceError("账号行为标记仍在(feedback_required)")
        raise SourceError(f"HTTP {r.status_code}")
    data = r.json()
    reels = (data.get("data") or {}).get("reels") or data.get("reels") or {}
    name_by_id = {str(uid): name for name, uid in config.IG_ACCOUNTS}
    out: list[Update] = []
    for uid, reel in reels.items():
        for it in reel.get("items") or []:
            pk = str(it.get("pk") or "")
            if not pk:
                continue
            mt = it.get("media_type")
            media: list[str] = []
            if mt == 2:
                try:
                    media.append(it["video_versions"][0]["url"])
                except Exception:
                    pass
            try:
                media.append(it["image_versions2"]["candidates"][0]["url"])
            except Exception:
                pass
            taken = it.get("taken_at")
            name = name_by_id.get(str(uid), str(uid))
            out.append(Update(
                source_key="ig_story", platform="instagram", account_name=name,
                content_type="story", external_id=pk, title=None, text=None,
                url=f"https://www.instagram.com/stories/{name}/",
                published_at=(datetime.fromtimestamp(taken, timezone.utc)
                              if isinstance(taken, (int, float)) else None),
                media_urls=media))
    return out


def source():
    from types import SimpleNamespace
    return SimpleNamespace(key="ig_story", platform="instagram", check=check)
