"""Instagram Story 源（已 POC 验证）：reels_media XHR，重复参数形态。

会话与多通道容灾：
  生产：IG_COOKIE 可含**多个** cookie（换行或 "|||" 分隔），每 10 分钟窗口
        轮换起始号均摊压力；某个 cookie 被 IG 拒绝（匿名空响应/401）时本轮
        立即切换下一个，全部失效才报错——10 分钟节奏不因单号死亡而中断。
  本地 POC：instaloader session 文件（惰性导入，生产无需安装）。
"""
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from monitor import config
from monitor.models import CheckpointError, SourceError, Update

_API = "https://www.instagram.com/api/v1/feed/reels_media/"
_ROTATE_WINDOW = 600  # 秒；轮换窗口 ≈ 检查间隔，相邻窗口用不同起始 cookie

_LAST_AUTH: bool | None = None  # 最近一次 check 的会话判定（test-email 健康检查展示用）
_LAST_COOKIE = ""               # 成功服务的 cookie 序号，如 "2/3"


def _headers() -> dict:
    return {
        "User-Agent": config.BROWSER_UA,
        "X-IG-App-ID": config.IG_WEB_APP_ID,
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://www.instagram.com/",
    }


def _session_cookie(cookie: str) -> requests.Session:
    """生产路径：用一整串 Cookie 头构建会话。"""
    s = requests.Session()
    s.headers.update(_headers())
    for part in cookie.replace("\n", ";").split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            if k and v:
                s.cookies.set(k.strip(), v.strip(), domain=".instagram.com")
    csrf = s.cookies.get("csrftoken")
    if csrf:
        s.headers["X-CSRFToken"] = csrf
    return s


def _session_instaloader() -> requests.Session:
    """本地 POC 路径：instaloader session 文件（生产不安装 instaloader）。"""
    import instaloader
    L = instaloader.Instaloader(
        download_pictures=False, download_videos=False,
        download_video_thumbnails=False, save_metadata=False,
        compress_json=False, max_connection_attempts=1, quiet=True)
    sf = Path(__file__).resolve().parents[2] / f"session-{config.IG_SESSION_USER}"
    L.load_session_from_file(config.IG_SESSION_USER, str(sf))
    s = L.context._session
    s.headers.update(_headers())
    return s


def _fetch(s: requests.Session) -> dict:
    """单次 reels_media 请求 + 响应校验。返回原始 JSON。

    会话有效性判据（2026-09-27 A/B 实测）：IG 对未登录请求返回"干净的空
    响应"（顶层只有 reels/status），登录会话的响应必带 reels_media 键。
    缺失 = 本 Cookie 已不被承认 → 报错（触发报警），绝不静默返回"成功 0 条"
    （2026-09-24~27 曾因此失明 3 天而看似正常）。
    """
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
    authed = "reels_media" in data or "reels_media" in (data.get("data") or {})
    if not authed:
        raise SourceError("会话失效：IG 返回未登录的匿名空响应（无 reels_media 标记）"
                          "——请重新导出 Cookie 并更新 IG_COOKIE")
    return data


def _parse(data: dict) -> list[Update]:
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


def check() -> list[Update]:
    global _LAST_AUTH, _LAST_COOKIE
    cookies = config.ig_cookies()
    if not cookies:  # 本地 POC：instaloader session 文件
        data = _fetch(_session_instaloader())
        _LAST_AUTH, _LAST_COOKIE = True, "session-file"
        return _parse(data)
    # 生产：多 cookie 轮换 + 失效切换。"会话失效"的 cookie 立即跳过换下一个；
    # 其他错误（网络/限流/HTTP）与账号无关，直接上抛不尝试剩余 cookie。
    start = int(time.time() // _ROTATE_WINDOW) % len(cookies)
    last_err: Exception | None = None
    for offset in range(len(cookies)):
        idx = (start + offset) % len(cookies)
        try:
            data = _fetch(_session_cookie(cookies[idx]))
        except CheckpointError:
            raise  # 账号级验证要求，交给 main 的 checkpoint 报警处理
        except SourceError as e:
            if "会话失效" not in str(e):
                raise
            last_err = e
            continue
        _LAST_AUTH, _LAST_COOKIE = True, f"{idx + 1}/{len(cookies)}"
        return _parse(data)
    _LAST_AUTH, _LAST_COOKIE = False, f"0/{len(cookies)}"
    raise last_err or SourceError("会话失效：全部 IG_COOKIE 均被 IG 拒绝")


def health_note() -> str:
    """test-email 健康检查的会话状态说明（基于最近一次 check 的响应判据）。"""
    if _LAST_AUTH:
        return f"｜已登录✓ 会话有效（cookie {_LAST_COOKIE}，响应含 reels_media 标记）"
    if _LAST_AUTH is False:
        return f"｜⚠ 未登录，Cookie 已失效（{_LAST_COOKIE}）"
    return ""  # 本进程尚未跑过 check


def source():
    from types import SimpleNamespace
    return SimpleNamespace(key="ig_story", platform="instagram", check=check,
                           health_note=health_note)
