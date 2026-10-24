"""Instagram Post/Reel 免账号源（ig_anon）：匿名主页 HTML + 免费代理猎取。

原理（2026-10-24 Actions 实测，数据见 poc/ig_anonymous_probe.py G 系列各轮日志）：
  - IG 对数据中心出口（Actions/Azure）在页面层就 302 登录墙；对住宅/运营商类
    出口仍返回带内嵌 JSON 的完整主页——未登录访客可见最新 ≤12 条帖子（含置顶），
    与 instaloader PR #2782 同款路线。
  - 免费公开代理池中约 1-5% 是住宅类出口且可用；验证过的好代理可跨轮存活
    （实测 82.152.138.127:999 连续 5 轮存活），缓存后多数周期零猎取代价。
  - 匿名单帖详情 graphql（doc_id 27128499623469141）匿名可用，但必须携带
    同一 Session 的 csrftoken cookie（先触一次页面再查询，实测 200）。

流程（每周期，纯匿名、零 cookie、零账号）：
  1. 出口候选：本地调试（设了 https_proxy 环境变量）→ 直连（实为经本地代理，
     即你的 VPN 出口）；否则 state 缓存的已知好代理逐个验证；全灭才进免费
     池子猎取（3 个公开列表抽样并发验证）。
  2. 验证即取数：候选出口抓第一个账号的主页并解析出目标 pk（防代理污染）
     才算可用，同一 Session 继续服务本轮其余请求。
  3. 逐账号抓主页 → 每账号最新 ≤12 条；每账号最新 _DETAIL_N 条经同一
     Session 补详情（caption/taken_at），失败不影响页面级数据。
  4. 代理中途死亡 → 自动换下一个；可用出口全部耗尽才报错。本源不进
     COOLDOWN_SOURCES：匿名无会话可保护，冷却语义不适用。

边界：只能看到最新 12 条（对 20 分钟级监控绰绰有余）；页面节点无时间戳，
由详情补齐，补不到则 published_at=None。story 由 ig_story 的 cookie 通道
负责，绝不经过免费代理（会话不可交给第三方）。

缓存：已知好代理存 state["ig_anon_proxies"]（经 main 的 set_state 钩子持有
state 引用，随 monitor_state.json 一起跨轮存活、随 Actions 自动 commit）。
"""
from __future__ import annotations

import json
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from monitor import config
from monitor.models import SourceError, Update

_PAGE_URL = "https://www.instagram.com/{name}/"
_GRAPH_URL = "https://www.instagram.com/graphql/query"
_GRAPH_DOC_ID = "27128499623469141"  # 单帖 web_info 查询（匿名可用，需 csrftoken）
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36")
_NAV = {"Accept": "text/html", "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none"}
_SCRIPT_RE = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.DOTALL)

_PAGE_TIMEOUT = (5, 12)
_HUNT_TIMEOUT = (4, 6)
_DETAIL_TIMEOUT = (5, 12)
_DETAIL_N = 2          # 每账号补详情的条数（新帖只出现在最前）
_MAX_ROTATIONS = 4     # 单周期内出口死亡后的最大换手次数

_POOL_CAP = 300
_POOL_WORKERS = 50
_POOL_SOURCES = [
    ("TheSpeedX", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("monosans", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("proxifly", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt"),
]
# 实测多轮存活的种子（2026-10-24 快照，仅供首周期加速；此后由 state 缓存接管）
_SEED_PROXIES = [
    "82.152.138.127:999", "202.58.77.239:8080",
    "186.167.113.103:999", "5.129.254.243:8888",
]
_CACHE_KEY = "ig_anon_proxies"
_CACHE_CAP = 10
_CACHE_FAIL_DROP = 3  # 连续失败 N 次从缓存剔除

_STATE: dict | None = None   # main 经 set_state 注入的 state 引用（读写即持久化）
_MEM_CACHE: list = []        # 无 state 时（本地直连调试）的内存缓存
_LAST: dict = {}             # 最近一次 check 的摘要（health_note 展示）


class _Dead(Exception):
    """当前出口不可用（网络错误/302 登录墙/数据污染），换下一个。"""


class _NoProxy(Exception):
    """所有候选出口耗尽。"""


def set_state(state: dict) -> None:
    """main.run_once 在 check 前注入 state 引用：对本 dict 的写入随其保存持久化。"""
    global _STATE
    _STATE = state


def _env_proxy() -> bool:
    """本地调试标记：设了代理环境变量 = 直连候选（实为经本地代理出口）。"""
    return bool(os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY"))


def _label(proxy: str | None) -> str:
    return "直连" if proxy is None else proxy


def _session(proxy: str | None) -> requests.Session:
    s = requests.Session()
    if proxy is not None:
        s.proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
    s.headers.update({"User-Agent": _UA, "X-IG-App-ID": config.IG_WEB_APP_ID})
    return s


def _cache_list() -> list[dict]:
    src = _STATE if _STATE is not None else None
    if src is not None:
        return src.setdefault(_CACHE_KEY, [])
    return _MEM_CACHE


def _cache_sorted() -> list[str]:
    """缓存代理按验证成功时间新→旧。"""
    return [c["proxy"] for c in sorted(_cache_list(), key=lambda c: -c.get("ok_at", 0))]


def _cache_touch(proxy: str) -> None:
    for c in _cache_list():
        if c["proxy"] == proxy:
            c["ok_at"], c["fails"] = time.time(), 0
            return
    cache = _cache_list()
    cache.append({"proxy": proxy, "ok_at": time.time(), "fails": 0})
    del cache[:-_CACHE_CAP]


def _cache_fail(proxy: str | None) -> None:
    if proxy is None:  # 直连候选不计缓存
        return
    for c in _cache_list():
        if c["proxy"] == proxy:
            c["fails"] = c.get("fails", 0) + 1
            if c["fails"] >= _CACHE_FAIL_DROP:
                _cache_list().remove(c)
            return


def _embedded_query_data(obj):
    """递归收集页面内嵌 relay JSON 的 __bbox.result.data（与 instaloader master 一致）。"""
    if isinstance(obj, dict):
        result = obj["__bbox"].get("result") if isinstance(obj.get("__bbox"), dict) else None
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            yield result["data"]
        for value in obj.values():
            yield from _embedded_query_data(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _embedded_query_data(value)


def _parse_page(text: str, uid: int | str) -> tuple[dict, list[dict]]:
    """解析主页 HTML 内嵌 JSON → (用户 dict, 帖子 node 列表)。pk 与 uid 不符视为污染。"""
    user: dict = {}
    for script in _SCRIPT_RE.finditer(text):
        try:
            for data in _embedded_query_data(json.loads(script.group(1))):
                u = data.get("xig_user_by_username") or {}
                if u.get("pk"):
                    user.update(u)
        except json.decoder.JSONDecodeError:
            continue
    if user.get("pk") and uid and str(user["pk"]) != str(uid):
        raise _Dead(f"pk 不匹配（{user.get('pk')} != {uid}，出口疑似污染）")
    tl = user.pop("polaris_ordered_timeline_connection", None) or {}
    edges = [e["node"] for e in (tl.get("edges") or [])
             if isinstance(e, dict) and e.get("node")]
    if not user.get("pk"):
        raise _Dead("页面无内嵌数据（出口被 IG 拦截或代理返回了壳）")
    return user, edges


def _fetch_account(s: requests.Session, name: str, uid: int | str) -> tuple[dict, list]:
    r = s.get(_PAGE_URL.format(name=name.lower()), headers=_NAV,
              timeout=_PAGE_TIMEOUT, allow_redirects=False)
    if r.status_code != 200:
        raise _Dead(f"HTTP {r.status_code}")
    return _parse_page(r.text, uid)


def _fetch_detail(s: requests.Session, code: str) -> dict | None:
    """单帖详情（caption/taken_at）。任何失败返回 None——详情是锦上添花，不影响页面级数据。"""
    csrf = s.cookies.get("csrftoken")
    if not csrf:
        return None
    try:
        r = s.post(_GRAPH_URL, data={
            "doc_id": _GRAPH_DOC_ID,
            "variables": json.dumps({
                "shortcode": code,
                "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False}),
        }, headers={"X-CSRFToken": csrf, "X-Requested-With": "XMLHttpRequest",
                    "Referer": "https://www.instagram.com/", "Accept": "*/*"},
            timeout=_DETAIL_TIMEOUT)
        if r.status_code != 200:
            return None
        items = (((r.json().get("data") or {})
                  .get("xdt_api__v1__media__shortcode__web_info") or {}).get("items")) or []
        return items[0] if items else None
    except Exception:
        return None


def _caption_text(cap) -> str:
    if isinstance(cap, dict):
        cap = cap.get("text") or ""
    return (cap or "").strip()


def _validate_proxy(proxy: str | None, uid: int | str):
    """并发验证 worker：返回 (proxy, session, 首账号数据) 或 (proxy, None, None)。"""
    try:
        s = _session(proxy)
        first = _fetch_account(s, config.IG_ACCOUNTS[0][0], uid)
        return proxy, s, first
    except Exception:
        return proxy, None, None


def _fetch_pool() -> list[str]:
    import requests as rq
    found: list[str] = []
    for name, url in _POOL_SOURCES:
        try:
            r = rq.get(url, timeout=15)
            if r.status_code != 200:
                continue
            for ln in r.text.splitlines():
                ln = ln.strip().split(",")[0]
                if ln.startswith("http://"):
                    ln = ln[7:]
                if re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+", ln):
                    found.append(ln)
        except Exception:
            continue
    uniq = list(dict.fromkeys(found))
    random.shuffle(uniq)
    return uniq[:_POOL_CAP]


def _hunt(uid: int | str):
    """免费池子并发猎取：返回第一个验证通过的 (proxy, session, 首账号数据)。"""
    pool = _fetch_pool()
    if not pool:
        raise _NoProxy("免费代理列表全部不可达")
    winner = None
    extras: list[str] = []
    ex = ThreadPoolExecutor(max_workers=_POOL_WORKERS)
    try:
        futs = [ex.submit(_validate_proxy, p, uid) for p in pool]
        for fut in as_completed(futs):
            proxy, s, first = fut.result()
            if s is not None:
                winner = (proxy, s, first)
                extras.append(proxy)
                if len(extras) >= 3:
                    break
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    if winner is None:
        raise _NoProxy(f"池子 {len(pool)} 个候选无一可用")
    for p in extras:
        _cache_touch(p)
    return winner


def _acquire(exclude: set | None = None):
    """拿到一个可用出口：直连（仅本地调试）→ 缓存已知好代理 → 池子猎取。

    返回 (label, session, 首账号 (user, edges))——验证请求本身即第一个账号的取数。
    """
    exclude = exclude or set()
    errors: list[str] = []
    candidates: list[str | None] = []
    if _env_proxy() and "直连" not in exclude:
        candidates.append(None)
    candidates += [p for p in _cache_sorted() if p not in exclude]
    if not candidates:
        candidates += [p for p in _SEED_PROXIES if p not in exclude]
    for cand in candidates:
        try:
            s = _session(cand)
            first = _fetch_account(s, config.IG_ACCOUNTS[0][0], config.IG_ACCOUNTS[0][1])
            if cand is not None:
                _cache_touch(cand)
            return _label(cand), s, first
        except _Dead as e:
            errors.append(f"{_label(cand)}: {e}")
            _cache_fail(cand)
        except Exception as e:  # 网络层异常同样视为该候选死亡
            errors.append(f"{_label(cand)}: {type(e).__name__}")
            _cache_fail(cand)
    try:
        proxy, s, first = _hunt(config.IG_ACCOUNTS[0][1])
        return _label(proxy), s, first
    except _NoProxy as e:
        raise _NoProxy(f"无可用出口（{'; '.join(errors[:3])}…；{e}）") from None


def _emit(name: str, uid: int | str, user_edges: tuple[dict, list],
          s: requests.Session) -> list[Update]:
    user, edges = user_edges
    details: dict[str, dict] = {}
    for node in edges[:_DETAIL_N]:
        code = node.get("code")
        if code:
            d = _fetch_detail(s, code)
            if d:
                details[code] = d
    out: list[Update] = []
    # ---- story 在场检测（零账号）：xig_user_by_username.latest_reel_media = 最新
    # story 的 epoch 时间戳，0 = 无。匿名页面就带（@nba 实测 1792812141 vs 无 story
    # 账号为 0），彩色圈的数据源就在这。external_id 用时间戳 → 每个 story 批次只
    # 报一次，追加新条目会更新时间戳 → 再报。cookie 通道恢复后，story 内容仍由
    # ig_story 提供，这里是在场告警的保底层。
    try:
        reel_ts = int(user.get("latest_reel_media") or 0)
    except (TypeError, ValueError):
        reel_ts = 0
    if reel_ts > 0:
        started = datetime.fromtimestamp(reel_ts, timezone.utc)
        out.append(Update(
            source_key="ig_anon", platform="instagram", account_name=f"@{name}",
            content_type="story", external_id=f"story-{reel_ts}",
            title=f"Story 在场（自 {started:%m-%d %H:%M} UTC 起，内容经 cookie 通道获取）",
            url=f"https://www.instagram.com/stories/{name}/",
            published_at=started))
    for node in edges:
        code = node.get("code")
        if not code:
            continue
        kind = "reel" if node.get("product_type") == "clips" else "post"
        d = details.get(code) or {}
        cap = _caption_text(d.get("caption") or node.get("caption"))
        taken = d.get("taken_at")
        out.append(Update(
            source_key="ig_anon", platform="instagram", account_name=f"@{name}",
            content_type=kind, external_id=code,
            title=cap[:120] or None, text=cap[:300] or None,
            url=f"https://www.instagram.com/{'reel' if kind == 'reel' else 'p'}/{code}/",
            published_at=(datetime.fromtimestamp(taken, timezone.utc)
                          if isinstance(taken, (int, float)) else None),
            media_urls=[node["display_uri"]] if node.get("display_uri") else []))
    return out


def check() -> list[Update]:
    global _LAST
    try:
        label, s, first = _acquire()
    except _NoProxy as e:
        _LAST = {"error": str(e)[:120]}
        raise SourceError(f"ig_anon：{e}") from None
    updates = _emit(*config.IG_ACCOUNTS[0], first, s)
    fetched, rotations = 1, 0
    for name, uid in config.IG_ACCOUNTS[1:]:
        got = None
        while got is None:
            try:
                got = _fetch_account(s, name, uid)
                _cache_touch(label.split("（")[0] if "（" in label else label)
            except _Dead:
                rotations += 1
                if rotations > _MAX_ROTATIONS:
                    break
                try:
                    label, s, _ = _acquire(exclude={label})
                except _NoProxy:
                    break  # 无出口可换：用已拿到的部分收尾
        if got:
            fetched += 1
            updates.extend(_emit(name, uid, got, s))
    _LAST = {"label": label, "fetched": fetched, "updates": len(updates),
             "rotations": rotations}
    if fetched == 0:
        raise SourceError(f"ig_anon：出口中途全部死亡（换手 {rotations} 次后耗尽）")
    return updates


def health_note() -> str:
    """test-email 健康快照：最近一次 check 用的出口与覆盖情况。"""
    if not _LAST:
        return ""
    if _LAST.get("error"):
        return f"｜⚠ 上轮无可用出口（{_LAST['error']}）"
    return (f"｜出口 {_LAST.get('label')}，账号 {_LAST.get('fetched')}/{len(config.IG_ACCOUNTS)}"
            f"，本次取 {_LAST.get('updates')} 条"
            + (f"，换手 {_LAST.get('rotations')} 次" if _LAST.get("rotations") else ""))


def source():
    from types import SimpleNamespace
    return SimpleNamespace(key="ig_anon", platform="instagram", check=check,
                           set_state=set_state, health_note=health_note)
