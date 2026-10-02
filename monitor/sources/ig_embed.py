"""Instagram 免账号通道 POC：公开 embed 页面抓最近帖子（Post/Reel 短码）。

背景：Graph API 官方通道查第三方账号需要企业认证（见 ig_graph.py，已实测被
(#10) 挡住）；instagrapi 私有 API 用真实账号轮询，易封号。本模块走 Meta 提供
给网站嵌帖用的公开页面 instagram.com/{用户名}/embed/，不需要任何账号、
cookie 或 token，零封号风险。

POC 范围（与 ig_graph.py 同套路，不接线）：
  - 只验证"能否稳定取到最近帖子的短码列表"：短码 = 帖子 URL 里的唯一 ID，
    可直接作将来的去重主键（与现有 seen 记录同样兼容）
  - 暂不解析时间/正文（embed 页不一定带，确认可行后再设计正式字段）
  - 设 IG_EMBED_SAMPLE=1 时把原始 HTML 落盘到 poc/ 供人工核对结构

已知风险：Meta 对数据中心 IP 的策略时有变化；embed 页面结构无官方保证。
被重定向到登录页 = 当前出口 IP 被要求登录（通道对该 IP 不可用）。

实测结论（2026-10-01，经真实代理出口验证）——免账号通道全面不可行：
  - profile embed：HTTP 200 但返回 ~636KB 纯前端渲染壳（XPolarisEmbedProfileController），
    HTML 内无任何帖子短码；web_profile_info（i.instagram.com 与 www）→ 429 限流 0 字节；
  - 匿名主页 /{user}/ → 同样是应用壳，无 og 标签；r.jina.ai 渲染后仍是登录墙；
  - 第三方查看站：imginn/imgsed 403、picuki 失联、greatfon 200 但内容 Turnstile 人机
    验证 + 前端渲染。
  → 结论：数据中心出口无法免账号发现新帖。post/reel 维持 instagrapi 通道并降频
    （30 → 60 分钟）降低封号压力；官方 Graph 通道待企业认证后经 ig_graph.py 接入。
  本模块保留作为结论记录与解析器样例，不再继续开发。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import requests

from monitor import config

_BASE = "https://www.instagram.com"
_TIMEOUT = 20
# 帖子短码：/p/{code}（图文）、/reel/{code}（短视频）、/tv/{code}（旧视频）
_SHORTCODE_RE = re.compile(r"/(?:p|reel|tv)/([A-Za-z0-9_-]{5,})")
_KIND = {"p": "post", "reel": "reel", "tv": "tv"}

_SAMPLE_DIR = Path(__file__).resolve().parent.parent / "poc"


def _url(username: str) -> str:
    return f"{_BASE}/{username}/embed/"


def fetch_profile_embed(username: str) -> list[dict]:
    """返回 embed 页面能看到的最近帖子 [{shortcode, kind}]（按页面出现顺序）。

    kind: post | reel | tv。HTTP 错误直接上抛；被重定向到登录页时抛
    RuntimeError（= 通道对当前出口 IP 不可用）。
    """
    r = requests.get(_url(username), timeout=_TIMEOUT,
                     headers={"User-Agent": config.BROWSER_UA})
    if r.status_code != 200:
        raise RuntimeError(f"embed HTTP {r.status_code}: {r.text[:200]}")
    if "accounts/login" in r.url or "Login • Instagram" in r.text[:2000]:
        raise RuntimeError("embed 被重定向到登录页（当前出口 IP 被要求登录）")
    sample = os.environ.get("IG_EMBED_SAMPLE")
    if sample:  # POC 调试：原始 HTML 落盘，供核对解析结构
        try:
            _SAMPLE_DIR.mkdir(exist_ok=True)
            (_SAMPLE_DIR / f"ig_embed_sample_{username}.html").write_text(
                r.text, encoding="utf-8")
            print(f"  原始 HTML 已存: {_SAMPLE_DIR / f'ig_embed_sample_{username}.html'}")
        except OSError:
            pass
    out: list[dict] = []
    seen: set[str] = set()
    for m in _SHORTCODE_RE.finditer(r.text):
        code = m.group(1)
        if code in seen:
            continue
        seen.add(code)
        kind = m.group(0).rstrip("/").split("/")[-2]
        out.append({"shortcode": code, "kind": _KIND.get(kind, kind)})
    return out


if __name__ == "__main__":
    for name, _ in config.IG_POST_ACCOUNTS:
        print(f"== {name} ==")
        try:
            items = fetch_profile_embed(name)
        except Exception as e:
            print(f"  失败: {type(e).__name__}: {e}")
            continue
        for it in items:
            path = "reel" if it["kind"] == "reel" else "p"
            print(f"    [ig-embed] {it['kind']:<5} | {it['shortcode']} | "
                  f"https://www.instagram.com/{path}/{it['shortcode']}/")
        print(f"  共 {len(items)} 条")
