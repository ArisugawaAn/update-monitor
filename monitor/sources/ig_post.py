"""Instagram Post/Reel 源：双通道（网页 XHR 优先，instagrapi 私有 API 兜底）。

通道 1（主）网页 XHR —— 与 Story 源同款路线：
  IG_COOKIE + X-IG-App-ID 请求 https://www.instagram.com/api/v1/feed/user/<uid>/
  该账号/端点组合已在 GitHub Actions 的出口 IP 下长期稳定（Story 源同款），
  因此优先使用。
通道 2（备）instagrapi 私有 API（IG_POST_SESSION_JSON / session-ig-post.json）：
  实测同一 session 在家庭 IP 下正常，但在 Actions 数据中心 IP 上会被 IG 限流
  （PleaseWaitFewMinutes），故仅作兜底。

两条通道都用帖子 code 作为 external_id，来回切换不会重复通知。
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from monitor import config
from monitor.models import CheckpointError, SourceError, Update

_WEB_API = "https://www.instagram.com/api/v1/feed/user/{uid}/"


def _load_client():
    """加载 instagrapi 客户端（惰性导入，本地未装时也能通过测试收集）。"""
    from instagrapi import Client

    session_json = (config.IG_POST_SESSION_JSON or "").strip()
    cl = Client()
    cl.delay_range = [1, 3]  # 请求间随机延迟 1~3 秒

    if session_json:
        # Actions：环境变量携带 session.json 完整内容
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                          delete=False, encoding="utf-8") as f:
            f.write(session_json)
            tmp = f.name
        cl.load_settings(tmp)
        os.unlink(tmp)
    else:
        # 本地：project 根目录 session-ig-post.json
        sf = Path(__file__).resolve().parents[2] / "session-ig-post.json"
        if not sf.exists():
            raise SourceError(
                "找不到 instagrapi session 文件——请先运行 instagrapi login "
                "并保存为 session-ig-post.json，或设置 IG_POST_SESSION_JSON")
        cl.load_settings(str(sf))
    return cl


def _web_session() -> requests.Session:
    """网页会话：复用 Story 源的 cookie 装配（IG_COOKIE → requests.Session）。"""
    from monitor.sources import ig_story
    return ig_story._session()


def _web_item_to_update(it: dict, username: str) -> Update | None:
    """网页 feed/user 返回的单条 media → Update（字段与 instagrapi 路径保持一致）。"""
    code = str(it.get("code") or "")
    if not code:
        return None
    cap = ((it.get("caption") or {}).get("text") or "").strip()
    media: list[str] = []
    if it.get("media_type") == 2:  # 视频/Reel
        try:
            media.append(it["video_versions"][0]["url"])
        except Exception:
            pass
    try:
        media.append(it["image_versions2"]["candidates"][0]["url"])
    except Exception:
        pass
    taken = it.get("taken_at")
    return Update(
        source_key=f"ig_post_{username}",
        platform="instagram",
        account_name=f"@{username}",
        content_type=("reel" if it.get("product_type") == "clips" else "post"),
        external_id=code,
        title=cap[:120] or None,
        text=cap[:300] or None,
        url=f"https://www.instagram.com/p/{code}/",
        published_at=(datetime.fromtimestamp(taken, timezone.utc)
                      if isinstance(taken, (int, float)) else None),
        media_urls=media)


def _web_updates(session=None) -> list[Update]:
    """通道 1：网页 XHR（Story 源同款路线）。session 可注入以便离线测试。"""
    if session is None:
        if not (config.IG_COOKIE or "").strip():
            raise SourceError("IG_COOKIE 未配置，无法走网页路线")
        session = _web_session()
    out: list[Update] = []
    for username, user_id in config.IG_POST_ACCOUNTS:
        try:
            r = session.get(_WEB_API.format(uid=user_id),
                            params={"count": config.IG_POST_WEB_COUNT}, timeout=20)
        except Exception as e:
            raise SourceError(f"@{username}: 网页请求异常 {type(e).__name__}: {e}") from e
        if r.status_code != 200:
            body = r.text[:200]
            if "checkpoint_required" in body:
                raise CheckpointError("IG 账号被要求 checkpoint 验证，已停止本源")
            if "feedback_required" in body:
                raise SourceError(f"@{username}: 账号行为标记仍在(feedback_required)")
            raise SourceError(f"@{username}: 网页端点 HTTP {r.status_code}: {body}")
        for it in (r.json().get("items") or []):
            u = _web_item_to_update(it, username)
            if u:
                out.append(u)
    return out


def _kind(media) -> str:
    """product_type → content_type。"""
    pt = media.product_type
    if pt == "clips":
        return "reel"
    return "post"


def _private_updates(client=None) -> list[Update]:
    """通道 2：instagrapi 私有 API（Actions 数据中心 IP 上易被限流）。client 可注入。"""
    cl = client if client is not None else _load_client()
    out: list[Update] = []
    for username, user_id in config.IG_POST_ACCOUNTS:
        try:
            medias = cl.user_medias(user_id, config.IG_POST_WEB_COUNT)
        except Exception as e:
            raise SourceError(f"@{username}: {type(e).__name__}: {e}") from e
        for m in medias:
            code = m.code
            if not code:
                continue
            taken = m.taken_at
            cap = (m.caption_text or "").strip()
            media_urls = []
            if m.thumbnail_url:
                media_urls.append(str(m.thumbnail_url))
            out.append(Update(
                source_key=f"ig_post_{username}",
                platform="instagram",
                account_name=f"@{username}",
                content_type=_kind(m),
                external_id=code,
                title=cap[:120] or None,
                text=cap[:300] or None,
                url=f"https://www.instagram.com/p/{code}/",
                published_at=(taken.astimezone(timezone.utc) if taken else None),
                media_urls=media_urls))
    return out


def check() -> list[Update]:
    """双通道：网页路线优先，失败回退 instagrapi；两者都失败才报错（交由主流程冷却）。

    网页路线与 Story 源同账号同端点，实测在 Actions 出口 IP 下稳定；私有 API 在
    数据中心 IP 上会被 IG 限流，因此只作为兜底，尽量不放弃监控能力。
    """
    if not config.IG_POST_WEB_FIRST:
        return _private_updates()
    try:
        return _web_updates()
    except CheckpointError:
        raise  # 需要人工验证：直接停用本源并报警
    except Exception as web_err:
        try:
            return _private_updates()
        except CheckpointError:
            raise
        except Exception as api_err:
            raise SourceError(
                f"网页路线[{type(web_err).__name__}: {web_err}] "
                f"| 私有API[{type(api_err).__name__}: {api_err}]"[:300]) from api_err


def source():
    from types import SimpleNamespace
    return SimpleNamespace(key="ig_post", platform="instagram", check=check)
