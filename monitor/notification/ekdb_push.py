"""ekdb 分发推送（可选）：新动态批量 POST 到 ekdb 内部接口。

ekdb 侧按 (source_key, external_id) 唯一约束幂等去重 —— 本模块失败重推安全。
未配置 EKDB_DISPATCH_URL / EKDB_MONITOR_TOKEN 时 configured() 为 False，
调用方整体跳过（功能关闭，监测与邮件行为与改动前完全一致）。

源目录（sources_catalog）随推送顺带同步：ekdb 的订阅勾选列表与本仓库 config
的账号表自动保持一致，加账号无需改 ekdb。目录仅在哈希变化时才单独推送。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import requests

from monitor import config
from monitor.models import Update

# Render 免费层休眠冷启动可达 ~60s，给足余量（只在真正有新动态时才会撞上）
TIMEOUT = 90

_DT_FIELDS = ("published_at", "detected_at")


def configured() -> bool:
    return bool(config.EKDB_DISPATCH_URL and config.EKDB_MONITOR_TOKEN)


def update_to_dict(u: Update) -> dict:
    d = asdict(u)
    for f in _DT_FIELDS:
        if d.get(f) is not None:
            d[f] = d[f].isoformat()
    return d


def sources_catalog() -> list[dict]:
    """源目录快照：ekdb 订阅列表的数据源，与 config 账号表自动同步。

    account_name 必须与各源 check() 产出 Update 的 account_name 完全一致
    （ekdb 端按 source_key × account_name 匹配订阅）。"""
    out: list[dict] = []

    def add(key: str, platform: str, account: str, label: str) -> None:
        out.append({"source_key": key, "platform": platform, "account_name": account,
                    "label": label, "prefix": config.notify_prefix(key, account)})

    for name, _uid in config.IG_ACCOUNTS:            # 三账号共用 key，按账号区分
        add("ig_story", "instagram", name, "Instagram Story")
    for name, _uid in config.IG_POST_ACCOUNTS:
        add(f"ig_post_{name}", "instagram", name, "Instagram Post/Reel")
    for handle in config.X_USERS:                    # 主号 key 固定为 "x"
        add("x" if handle == config.X_USERS[0] else f"x_{handle}",
            "x", f"@{handle}", "X")
    for name, cid in config.YOUTUBE_CHANNELS:
        add(f"youtube_{cid}", "youtube", name, "YouTube")
    for handle, _sec in config.TIKTOK_ACCOUNTS:
        add(f"tiktok_{handle}", "tiktok", f"@{handle}", "TikTok")
    for key, label, _url, _parser in _website_sites():
        add(key, "website", label, "官网")
    return out


def _website_sites():
    from monitor.sources import websites
    return websites.SITES


def catalog_hash(catalog: list[dict]) -> str:
    return hashlib.sha256(
        json.dumps(catalog, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def push(updates: list[Update], catalog: list[dict] | None = None,
         sources_only: bool = False) -> tuple[bool, str]:
    """批量推送（updates=[] 且 sources_only=True = 仅同步源目录）。
    返回 (是否成功, 说明)。"""
    if not configured():
        return True, "未配置 ekdb 分发（跳过）"
    payload = {
        "updates": [] if sources_only else [update_to_dict(u) for u in updates],
        "sources": catalog if catalog is not None else sources_catalog(),
    }
    try:
        r = requests.post(
            f"{config.EKDB_DISPATCH_URL}/api/internal/monitor/updates",
            json=payload,
            headers={"Authorization": f"Bearer {config.EKDB_MONITOR_TOKEN}"},
            timeout=TIMEOUT)
        if r.status_code != 200:
            return False, f"HTTP {r.status_code}: {r.text[:120]}"
        data = r.json()
        return True, (f"接受 {data.get('accepted', 0)} / 重复 {data.get('duplicates', 0)}"
                      f" / 入队 {data.get('enqueued', 0)}")
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
