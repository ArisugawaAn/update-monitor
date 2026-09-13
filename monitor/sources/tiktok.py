"""TikTok 源：公开主页 SSR JSON（无账号）。

实测结论：页面 JSON 的 stats.videoCount 可用，但视频列表（itemList）已被
TikTok 移出 SSR —— 因此采用「计数检测」：videoCount 增加 = 有新视频。
如实限制：能发现"有新视频"并给出账号链接，但拿不到单条视频 URL/标题；
若未来 itemList 恢复 SSR，自动切换为逐条模式（代码已内置）。
"""
import json
import re
from datetime import datetime, timezone

import requests

from monitor import config
from monitor.models import SourceError, Update


def _fetch_user(handle: str) -> dict:
    url = f"https://www.tiktok.com/@{handle}"
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": config.BROWSER_UA})
    except Exception as e:
        raise SourceError(f"请求异常: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise SourceError(f"{handle}: HTTP {r.status_code}（可能被风控）")
    m = re.search(
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(\{.*?\})</script>',
        r.text, re.S)
    if not m:
        raise SourceError(f"{handle}: 页面无可解析数据（风控/结构变更）")
    try:
        return json.loads(m.group(1))["__DEFAULT_SCOPE__"]["webapp.user-detail"]
    except Exception as e:
        raise SourceError(f"{handle}: JSON 结构不符合预期: {type(e).__name__}") from e


def _last_known_count(handle: str) -> int | None:
    """从状态文件读取上次见过的视频总数（count-N 形式）。"""
    try:
        b = json.loads(config.STATE_FILE.read_text(encoding="utf-8"))["sources"].get(
            f"tiktok_{handle}", {})
        counts = [int(x[6:]) for x in b.get("seen", []) if x.startswith("count-")]
        return max(counts) if counts else None
    except Exception:
        return None


def _video_update(handle: str, it: dict) -> Update | None:
    vid = str(it.get("id") or "")
    if not vid:
        return None
    ct = it.get("createTime")
    desc = (it.get("desc") or "").strip()
    cover = (it.get("video") or {}).get("cover") or ""
    return Update(
        source_key=f"tiktok_{handle}", platform="tiktok", account_name=f"@{handle}",
        content_type="video", external_id=vid, title=desc[:120] or None,
        text=desc or None, url=f"https://www.tiktok.com/@{handle}/video/{vid}",
        published_at=(datetime.fromtimestamp(int(ct), timezone.utc) if ct else None),
        media_urls=[cover] if cover else [])


def check_account(handle: str) -> list[Update]:
    ud = _fetch_user(handle)
    info = ud.get("userInfo") or {}
    user = info.get("user") or {}
    stats = info.get("stats") or {}
    items = ud.get("itemList") or []

    if items:  # 逐条模式（若 TikTok 恢复 SSR 列表则自动启用）
        out = [u for u in (_video_update(handle, it) for it in items) if u]
        return out

    count = stats.get("videoCount")
    if count is None:
        raise SourceError(f"{handle}: 页面无视频计数（结构变更）")
    prev = _last_known_count(handle)
    out: list[Update] = []
    if prev is None or count > prev:  # 计数下降（删视频）不通知
        out.append(Update(
            source_key=f"tiktok_{handle}", platform="tiktok", account_name=f"@{handle}",
            content_type="video", external_id=f"count-{count}",
            title=(f"TikTok 有新视频（{prev}→{count}）" if prev is not None
                   else f"TikTok 视频总数：{count}"),
            text="计数检测模式：具体视频请打开账号主页查看",
            url=f"https://www.tiktok.com/@{handle}"))
    return out


def source(handle: str, sec_uid: str = ""):
    from types import SimpleNamespace
    return SimpleNamespace(key=f"tiktok_{handle}", platform="tiktok",
                           check=lambda: check_account(handle))
