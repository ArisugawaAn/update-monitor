#!/usr/bin/env python3
"""POC（独立于生产代码）：在 GitHub Actions 中经代理运行 instagrapi，验证能否绕过 IG 的 401。

一次性验证脚本，**不参与生产流程**（monitor/sources/ig_post.py 不做任何改动）：

  1. 代理来源：环境变量 ``IG_PROXY``（``http://…`` 或 ``socks5://…``）优先；未设置时
     从公开免费代理源随机取 **1 个**，只测这一个 —— 不建代理池、不重试、不轮换。
  2. 只调用一次 ``user_medias(10584438821, 5)``；失败即结束本次测试。
  3. 不打印代理地址 / session / cookie（只打印来源、协议与脱敏标记）。
  4. 输出：代理是否连通、出口 IP、Instagram HTTP/异常结果、返回媒体数量。

用法::

    python poc/ig_proxy_poc.py             # 真实测试（向 Instagram 发 1 次请求）
    python poc/ig_proxy_poc.py --dry-run   # 只验证「取代理 + 连通性」，不碰 Instagram
"""
from __future__ import annotations

import os
import random
import sys
import tempfile

import requests

TARGET_UID = 10584438821   # @miyamoto_doppo（与生产 config.IG_POST_ACCOUNTS 一致）
FETCH_COUNT = 5
# 出口 IP 探测端点（仅为探测本身的端点备选，**不是**换代理）：
#   http 明文优先 —— http 代理无需 TLS 隧道，避免免费代理常见的证书/MITM 报错
IP_ECHO_URLS = [
    "http://api.ipify.org/?format=json",
    "https://api.ipify.org?format=json",
]
IP_ECHO_TIMEOUT = 15
SCHEMES = ("http://", "https://", "socks5://", "socks5h://")
# 公开免费代理源（按顺序取**第一个**能返回列表的源，再从中随机取 1 个）
FREE_SOURCES = [
    ("http", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
    ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
]
DRY_RUN = "--dry-run" in sys.argv


def scheme_of(proxy: str) -> str:
    """取协议名（用于输出，不含地址）。"""
    return proxy.split("://", 1)[0]


def fetch_free_proxy() -> tuple[str, str]:
    """从公开免费源随机取 1 个代理，返回 (proxy_url, 源域名)。

    只取第一个可用源的一次结果、只随机选 1 个；不建池、不预筛、不重试。
    """
    for scheme, url in FREE_SOURCES:
        host = url.split("/")[2]
        try:
            r = requests.get(url, timeout=15)
        except Exception as e:  # 源不可达 → 换下一个源（不是换代理）
            print(f"  free source unreachable: {host} ({type(e).__name__})")
            continue
        if r.status_code != 200:
            print(f"  free source HTTP {r.status_code}: {host}")
            continue
        candidates = [ln.strip() for ln in r.text.splitlines()
                      if ln.strip().count(":") == 1 and ln.strip()[0].isdigit()]
        if not candidates:
            print(f"  free source empty: {host}")
            continue
        pick = random.choice(candidates)
        print(f"  free source used: {host} | candidates={len(candidates)} | picked=1 (random)")
        return f"{scheme}://{pick}", host
    raise SystemExit("所有公开免费代理源均不可用 —— 本次 POC 结束（按要求不重试）")


def resolve_proxy() -> tuple[str, str]:
    """确定本次唯一要测的代理：返回 (proxy_url, 来源标记)。"""
    env = (os.environ.get("IG_PROXY") or "").strip()
    if env:
        if not env.startswith(SCHEMES):
            raise SystemExit("IG_PROXY 必须以 http:// 或 socks5:// 开头")
        return env, "env:IG_PROXY"
    proxy, host = fetch_free_proxy()
    return proxy, f"free:{host}"


def probe_proxy(proxy: str) -> str | None:
    """经代理取出口 IP；失败返回 None（**代理只测这一次**，不重试、不换）。

    仅在探测端点之间回退（http → https 各一次），代理本身不重试。
    """
    last_err = None
    for url in IP_ECHO_URLS:
        try:
            r = requests.get(url, proxies={"http": proxy, "https": proxy},
                             timeout=IP_ECHO_TIMEOUT)
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:120]}"
            continue
        if r.status_code != 200:
            last_err = f"HTTP {r.status_code}"
            continue
        try:
            ip = r.json().get("ip")
        except Exception:
            ip = r.text.strip()[:45]
        if ip:
            return ip
        last_err = "empty response"
    print(f"  proxy probe failed: {last_err}")
    return None


def ig_user_medias(proxy: str) -> dict:
    """加载现有 session（不重新登录）→ 经代理调用一次 user_medias()。"""
    raw = (os.environ.get("IG_POST_SESSION_JSON") or "").strip()
    if not raw:
        return {"ok": False, "error": "IG_POST_SESSION_JSON 未配置（需要现有 session.json 内容）"}
    from instagrapi import Client

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        f.write(raw)
        tmp = f.name
    try:
        cl = Client()
        cl.load_settings(tmp)
    finally:
        os.unlink(tmp)
    cl.set_proxy(proxy)          # instagrapi 内部同样经代理发起全部请求
    cl.delay_range = [1, 3]      # 与生产一致：请求间随机延迟

    try:
        medias = cl.user_medias(TARGET_UID, FETCH_COUNT)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:240]}"}
    return {"ok": True, "count": len(medias),
            "latest": (medias[0].code if medias else None)}


def main() -> int:
    print("=" * 74)
    print("POC: instagrapi over proxy on GitHub Actions" + (" [DRY-RUN]" if DRY_RUN else ""))
    print("=" * 74)

    print("[1] resolve proxy")
    proxy, origin = resolve_proxy()
    print(f"  origin : {origin}")
    print(f"  scheme : {scheme_of(proxy)}://<masked>")     # 地址一律脱敏

    print("[2] proxy connectivity + egress IP")
    egress = probe_proxy(proxy)
    print(f"  connected: {'YES' if egress else 'NO'}")
    print(f"  egress IP: {egress or '-'}")

    if not egress:
        print("[3] instagram call: SKIPPED (proxy not usable) —— 按要求不重试、不换代理")
        print("=" * 74)
        return 1

    if DRY_RUN:
        print("[3] instagram call: SKIPPED (--dry-run)")
        print("=" * 74)
        return 0

    print(f"[3] instagram call: user_medias({TARGET_UID}, {FETCH_COUNT}) —— 只调用一次")
    res = ig_user_medias(proxy)
    if res.get("ok"):
        print(f"  result      : OK")
        print(f"  media count : {res['count']}")
        print(f"  latest code : {res['latest']}")
        print(f"  egress IP   : {egress}")
        print("=" * 74)
        return 0
    print("  result      : FAIL")
    print(f"  error       : {res.get('error')}")
    print(f"  egress IP   : {egress}")
    print("=" * 74)
    return 1


if __name__ == "__main__":
    sys.exit(main())
