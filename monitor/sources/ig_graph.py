"""Instagram Graph API 官方通道（POC）：business_discovery 查公开 Post/Reel。

动机：ig_post 走 instagrapi 私有 API，用真实账号高频轮询，极易触发限流/封号；
Graph API 的 business_discovery 是 Meta 官方允许的第三方公开数据查询（目标
账号必须是 Business/Creator 专业号——三个监测对象均为官方号，满足）。token
宿主号只发官方 API 调用，不参与任何爬取，零封号风险。Reels 自 2022 年起
包含在返回里（media_product_type=REELS）。

调用链（每账号每次 1 个请求，无高消耗接口）：
  0.（仅 IG_GRAPH_USER_ID 未配置时）/me/accounts → 自己的 instagram_business_account
  1. GET /{own_ig_id}?fields=business_discovery.username({name})
       {media.limit(5){id,caption,media_type,media_product_type,permalink,
                       timestamp,media_url,thumbnail_url}}
限流：IG Platform 按 token 宿主专业账号计（约 200 次/小时）；3 个账号每
30 分钟一轮 = 6 次/小时，远低于限额。

设计约束（POC 阶段，与 YouTube Data API POC 同套路）：
  - Token 只从环境变量 IG_GRAPH_TOKEN 读取，绝不写入代码
  - 未配置 → available() 为 False，调用方直接跳过（不影响现有 ig_post）
  - 先只打印/记录 id、类型、时间、caption，确认通道可用
  - 暂不接入 build_sources、不替代 ig_post；验证通过后再接为
    "Graph 主 + instagrapi 备"（同 YouTube RSS 主 + Data API 备的架构）
"""
from __future__ import annotations

import requests

from monitor import config

_API = "https://graph.facebook.com/v22.0"
_TIMEOUT = 20
_FETCH_COUNT = 5  # POC：每账号最近 5 条媒体（Post/Reel 混排）
_MEDIA_FIELDS = ("id,caption,media_type,media_product_type,permalink,"
                 "timestamp,media_url,thumbnail_url")


def available() -> bool:
    """是否配置了 Graph token（未配置 → 调用方直接跳过，不报错打扰主流程）。"""
    return bool(config.IG_GRAPH_TOKEN)


def _get(node: str, fields: str) -> dict:
    """调一次 Graph API；HTTP 错误/JSON error 统一转 RuntimeError 带上原因。"""
    r = requests.get(f"{_API}/{node}",
                     params={"fields": fields, "access_token": config.IG_GRAPH_TOKEN},
                     timeout=_TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"Graph API {node} HTTP {r.status_code}: {r.text[:200]}")
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Graph API {node} 返回非 JSON: {r.text[:200]}") from None
    if not isinstance(data, dict) or "error" in data:
        raise RuntimeError(f"Graph API {node} 错误: {str(data)[:200]}")
    return data


def resolve_own_ig_id() -> str:
    """返回 token 宿主的 IG 专业账号 ID（business_discovery 的查询节点）。

    显式配置 IG_GRAPH_USER_ID 可跳过解析；否则取 /me/accounts 下第一个
    关联了 instagram_business_account 的主页（一个主页只能关联一个 IG 号）。
    """
    if config.IG_GRAPH_USER_ID:
        return config.IG_GRAPH_USER_ID
    data = _get("me/accounts", "instagram_business_account{id}")
    for page in data.get("data") or []:
        ig = page.get("instagram_business_account") or {}
        if ig.get("id"):
            return ig["id"]
    raise RuntimeError("token 宿主下找不到 IG 专业账号：请确认自己的 IG 已切换为"
                       "专业账号并关联了 Facebook 主页，或显式配置 IG_GRAPH_USER_ID")


def fetch_user_media(username: str) -> list[dict]:
    """返回目标账号最近 5 条媒体 [{id, mediaType, productType, timestamp,
    caption, permalink}]（Post/Reel 混排，productType=REELS 区分 Reel）。

    前置：调用方已确认 available() 为 True。任何异常直接上抛。
    """
    bd = (f"business_discovery.username({username})"
          f"{{media.limit({_FETCH_COUNT}){{{_MEDIA_FIELDS}}}}}")
    data = _get(resolve_own_ig_id(), bd)
    disc = data.get("business_discovery") or {}
    media = disc.get("media") or {}
    out: list[dict] = []
    for m in media.get("data") or []:
        item = {"id": m.get("id") or "",
                "mediaType": m.get("media_type") or "",
                "productType": m.get("media_product_type") or "",
                "timestamp": m.get("timestamp") or "",
                "caption": (m.get("caption") or "").strip(),
                "permalink": m.get("permalink") or ""}
        if not item["id"]:
            continue
        out.append(item)
        # POC 可观察性：打印 id/类型/时间/caption，确认通道正常
        print(f"    [ig-graph] {item['id']} | "
              f"{item['productType'] or item['mediaType'] or '-'} | "
              f"{item['timestamp'] or '-'} | {item['caption'][:60]}")
    return out


if __name__ == "__main__":
    if not available():
        raise SystemExit("未配置 IG_GRAPH_TOKEN 环境变量，无法运行 POC")
    print(f"token 宿主 IG 专业账号 ID: {resolve_own_ig_id()}")
    for name, _ in config.IG_POST_ACCOUNTS:
        print(f"== {name} ==")
        try:
            items = fetch_user_media(name)
        except Exception as e:
            print(f"  失败: {type(e).__name__}: {e}")
            continue
        print(f"  共 {len(items)} 条")
