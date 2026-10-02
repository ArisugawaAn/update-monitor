"""Instagram Story 源（已 POC 验证）：reels_media XHR，重复参数形态。

会话与多通道容灾：
  生产：IG_COOKIE 可含**多个** cookie（换行或 "|||" 分隔），每 10 分钟窗口
        轮换起始号均摊压力；某个 cookie 被 IG 拒绝（匿名空响应/401）时本轮
        立即切换下一个，全部失效才报错——10 分钟节奏不因单号死亡而中断。
        报错一律带 cookie 标识（IG_COOKIE_LABELS 命名或 "序号/总数" +
        ds_user_id），报警邮件可定位到具体账号，不用逐个排查。
        单号失效但被其余号接管时也立刻单发一封提醒（按事件去重、恢复服务
        后自动重新武装），不留"看似正常"的静默死亡；test-email 健康快照
        同样标注被接管的号。
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
_LAST_COOKIE = ""               # 成功服务的 cookie 标识（tag），如 "viewer-a·uid111" 或 "2/3"
_LAST_DEAD: list[tuple[str, str]] = []  # 最近一次 check 中会话失效的 (标识, 原因)，供单号报警


def _ds_user_id(cookie: str) -> str:
    """从整串 Cookie 头提取 ds_user_id（该会话属主账号的 ID），提取不到返回空串。"""
    for part in cookie.replace("\n", ";").split(";"):
        k, _, v = part.strip().partition("=")
        if k == "ds_user_id":
            return v.strip()
    return ""


def _cookie_tag(cookies: list[str], idx: int) -> str:
    """报警邮件/健康检查用的 cookie 标识：IG_COOKIE_LABELS 命名 > "序号/总数"；
    ds_user_id 恒附上——它与 secret 里的排列顺序无关，删号重排后仍能对到账号。"""
    labels = config.IG_COOKIE_LABELS
    name = labels[idx] if idx < len(labels) else f"{idx + 1}/{len(cookies)}"
    uid = _ds_user_id(cookies[idx])
    return f"{name}·uid{uid}" if uid else name


def _with_tag(e: Exception, tag: str) -> Exception:
    """给错误附上 cookie 标识后返回（保留异常类型，供 main 按类型分支）。

    SourceError/CheckpointError 直接改消息；其余异常（requests 网络错误、
    JSON 解析错误等）包成 SourceError 并保留原类型名，报警邮件不再只有裸类型。
    """
    msg = f"[{tag}] {e}"
    if isinstance(e, (SourceError, CheckpointError)):
        e.args = (msg,)
        return e
    return SourceError(f"{type(e).__name__}: {msg}")


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
    global _LAST_AUTH, _LAST_COOKIE, _LAST_DEAD
    cookies = config.ig_cookies()
    if not cookies:  # 本地 POC：instaloader session 文件
        data = _fetch(_session_instaloader())
        _LAST_AUTH, _LAST_COOKIE, _LAST_DEAD = True, "session-file", []
        return _parse(data)
    # 生产：多 cookie 轮换 + 失效切换。"会话失效"的 cookie 立即跳过换下一个；
    # 其他错误（网络/限流/HTTP）与账号无关，直接上抛不尝试剩余 cookie。
    # 所有抛出的错误都带 cookie 标识（命名/序号 + ds_user_id），
    # 报警邮件（连续失败/限流/checkpoint）据此可定位到具体账号。
    start = int(time.time() // _ROTATE_WINDOW) % len(cookies)
    attempts: list[str] = []          # 每个 cookie 的失效原因；全部失效时并入最终错误
    dead: list[tuple[str, str]] = []  # (标识, 原因)：本轮会话失效的号，供 main 单号报警
    for offset in range(len(cookies)):
        idx = (start + offset) % len(cookies)
        tag = _cookie_tag(cookies, idx)
        try:
            data = _fetch(_session_cookie(cookies[idx]))
        except CheckpointError as e:
            raise _with_tag(e, tag)  # 账号级验证要求，交给 main 的 checkpoint 报警处理
        except SourceError as e:
            if "会话失效" not in str(e):
                raise _with_tag(e, tag)
            attempts.append(f"[{tag}] {e}")
            dead.append((tag, str(e)))
            continue
        except Exception as e:  # 网络等意外错误：语义不变（上抛），只补上当时在用哪个 cookie
            raise _with_tag(e, tag)
        _LAST_AUTH, _LAST_COOKIE, _LAST_DEAD = True, tag, dead
        return _parse(data)
    _LAST_AUTH, _LAST_COOKIE, _LAST_DEAD = False, f"0/{len(cookies)}", dead
    raise SourceError(f"会话失效：全部 {len(cookies)} 个 cookie 均被 IG 拒绝 —— "
                      f"{'；'.join(attempts)}")


def cookie_status() -> tuple[str, list[tuple[str, str]]]:
    """最近一次 check 的 cookie 状态：(成功服务的标识, 会话失效的 (标识, 原因) 列表)。

    供 main 对"轮换顶上但单号已死"发单号报警（按事件去重、恢复后重新武装）。
    """
    return _LAST_COOKIE, list(_LAST_DEAD)


def health_note() -> str:
    """test-email 健康检查的会话状态说明（基于最近一次 check 的响应判据）。"""
    if _LAST_AUTH:
        note = f"｜已登录✓ 会话有效（cookie {_LAST_COOKIE}，响应含 reels_media 标记）"
        if _LAST_DEAD:  # 轮换顶上了 ≠ 没事：被接管的失效号在健康快照里也要看得见
            note += f"｜⚠ 会话失效已由其余号接管: {'、'.join(t for t, _ in _LAST_DEAD)}"
        return note
    if _LAST_AUTH is False:
        return f"｜⚠ 未登录，Cookie 已失效（{_LAST_COOKIE}）"
    return ""  # 本进程尚未跑过 check


def source():
    from types import SimpleNamespace
    return SimpleNamespace(key="ig_story", platform="instagram", check=check,
                           health_note=health_note, cookie_status=cookie_status)
