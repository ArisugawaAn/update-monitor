"""Instagram Post/Reel 源：instagrapi 私有 API（与 Story 源分离，互不影响）。

- Story 源（ig_story.py）用网页 XHR + 会话 Cookie（ekmiya612）
- 本源用 instagrapi 私有 API（另一个小号 miyaek612）拉取最近帖子

instagrapi 的 user_medias() 走的是手机 App 的 API 版本 + 设备指纹，
绕开了网页端点的 feedback_required 限流。

会话来源：
  - Actions：IG_POST_SESSION_JSON 环境变量（session.json 的完整内容）
  - 本地：project 根目录 session-ig-post.json
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from monitor import config
from monitor.models import SourceError, Update


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


def _kind(media) -> str:
    """product_type → content_type。"""
    pt = media.product_type
    if pt == "clips":
        return "reel"
    return "post"


def check() -> list[Update]:
    cl = _load_client()
    out: list[Update] = []
    for username, user_id in config.IG_POST_ACCOUNTS:
        try:
            medias = cl.user_medias(user_id, 12)
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


def source():
    from types import SimpleNamespace
    return SimpleNamespace(key="ig_post", platform="instagram", check=check)
