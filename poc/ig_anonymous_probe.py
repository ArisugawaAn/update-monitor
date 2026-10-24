"""在 GitHub Actions 真实出口验证免账号通道（一次性实验，与监控主流程无关）。

只读实验：不登录任何账号、不使用任何 cookie/token、不读写 monitor_state.json、
不发通知、不修改任何业务代码。结果用于决策"是否把免账号通道接为监控第一层"。

用法：
  python poc/ig_anonymous_probe.py <username> <userid> [attempts_per_channel]

默认 attempts=2；同一通道内每次尝试间隔 30 秒。

测试对象：
  A. instagrapi public_transport="curl" + chrome136 指纹（已知：Actions 上被拒）
  C. 匿名 graphql doc_id 7950326061742207 直查时间线 —— instaloader 4.15.x 内置的
     未登录 doc_id，只带 user ID，绕开 web_profile_info / users/info 两个被墙端点。
     这是本轮的核心验证：成功 = 免账号发现新帖可行。
  B. Instaloader 匿名全链路（Profile.from_id → get_posts，依赖 users/info，作对照）。
"""
import sys
import time

USERNAME = sys.argv[1] if len(sys.argv) > 1 else "miyamoto_doppo"
USERID = sys.argv[2] if len(sys.argv) > 2 else "10584438821"
ATTEMPTS = int(sys.argv[3]) if len(sys.argv) > 3 else 2
GAP = 30

TIMELINE_ANON_DOC_ID = "7950326061742207"  # instaloader 4.15.x 未登录时间线 doc_id


def probe_instagrapi() -> list:
    from instagrapi import Client
    results = []
    for i in range(ATTEMPTS):
        cl = Client(public_transport="curl", public_transport_impersonate="chrome136")
        try:
            info = cl.user_info_by_username(USERNAME)
            print(f"  [{i+1}] profile OK: pk={info.pk} media_count={info.media_count}",
                  flush=True)
            try:
                medias = cl.user_medias(info.pk, amount=5)
                print(f"      medias OK: {len(medias)} 条 "
                      f"{[m.shortcode for m in medias]}", flush=True)
                results.append("OK")
            except Exception as e:
                print(f"      medias FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
                results.append("profile_ok/medias_fail")
        except Exception as e:
            print(f"  [{i+1}] profile FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
            results.append(type(e).__name__)
        if i < ATTEMPTS - 1:
            time.sleep(GAP)
    return results


def probe_graphql_timeline() -> list:
    """方法 C：doc_id 直查时间线（不经过任何被墙的 profile 端点）。"""
    from instaloader import Instaloader
    results = []
    for i in range(ATTEMPTS):
        L = Instaloader(quiet=True, download_comments=False, save_metadata=False,
                        post_metadata_txt_pattern="")
        L.context.max_connection_attempts = 2
        try:
            resp = L.context.doc_id_graphql_query(
                TIMELINE_ANON_DOC_ID, {"id": USERID, "first": 5})
            user = (resp.get("data") or {}).get("user") or {}
            media = user.get("edge_owner_to_timeline_media") or {}
            edges = media.get("edges") or []
            codes = [e["node"].get("shortcode") for e in edges if e.get("node")]
            print(f"  [{i+1}] timeline OK: count={media.get('count')} "
                  f"edges={len(edges)} {codes}", flush=True)
            results.append("OK" if codes else "empty")
        except Exception as e:
            print(f"  [{i+1}] FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
            results.append(type(e).__name__)
        if i < ATTEMPTS - 1:
            time.sleep(GAP)
    return results


def probe_instaloader() -> list:
    from instaloader import Instaloader, Profile
    results = []
    for i in range(ATTEMPTS):
        L = Instaloader(quiet=True, download_comments=False, save_metadata=False,
                        post_metadata_txt_pattern="")
        L.context.max_connection_attempts = 2
        try:
            p = Profile.from_id(L.context, USERID)
            print(f"  [{i+1}] from_id OK", flush=True)
            posts = []
            for post in p.get_posts():
                posts.append(post.shortcode)
                if len(posts) >= 5:
                    break
            print(f"      posts OK: {posts}", flush=True)
            results.append("OK")
        except Exception as e:
            print(f"  [{i+1}] FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
            results.append(type(e).__name__)
        if i < ATTEMPTS - 1:
            time.sleep(GAP)
    return results


def probe_cookie_web() -> list:
    """方法 D（v2）：生产 story cookie 在 Actions 出口测 post 端点。

    历史结论（miyamoto-monitor-test/cooldown_retest.py，2026-09）：
      本地实测 web_profile_info / feed/user 每被触碰一次就会续期账号的
      feedback_required 标记（账号"冷却不了"的主因）→ 本探针绝不重试、
      每个端点只碰一次、彻底不碰 wpi（Actions 上已实测 429）。
    流程（共 2 个请求）：
      [1] reels_media 阳性对照 —— 生产每 10 分钟都在打，证明 cookie 在本出口有效
      [2] feed/user/{uid} —— 矩阵中唯一未测格子：post 端点 × Actions × cookie
    """
    import os
    cookie = os.environ.get("IG_COOKIE", "").strip()
    if not cookie:
        print("  IG_COOKIE 未设置 → 跳过", flush=True)
        return ["skipped"]
    import requests
    results = []
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"),
        "x-ig-app-id": "936619743392459",
        "x-requested-with": "XMLHttpRequest",
        "accept": "*/*",
        "referer": "https://www.instagram.com/",
    })
    first_cookie = cookie.replace("|||", "\n").split("\n")[0].strip()
    for part in first_cookie.replace("\n", ";").split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            s.cookies.set(k.strip(), v.strip(), domain=".instagram.com")
    csrf = s.cookies.get("csrftoken")
    if csrf:
        s.headers["X-CSRFToken"] = csrf

    print("  [1] reels_media 阳性对照（cookie 有效性）", flush=True)
    try:
        r = s.get("https://www.instagram.com/api/v1/feed/reels_media/",
                  params=[("user_ids", USERID)], timeout=25)
        authed = "reels_media" in r.text
        print(f"      HTTP {r.status_code} authed={authed}", flush=True)
        results.append(f"control_{r.status_code}")
    except Exception as e:
        print(f"      FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
        results.append("control_fail")
    time.sleep(GAP)

    print("  [2] feed/user —— post 端点（唯一未知项，单次触碰，不重试）", flush=True)
    try:
        r = s.get(f"https://www.instagram.com/api/v1/feed/user/{USERID}/",
                  params={"count": 5}, timeout=25)
        print(f"      HTTP {r.status_code}", flush=True)
        if r.status_code == 200:
            items = r.json().get("items") or []
            for it in items[:5]:
                pt = it.get("product_type", "")
                kind = "REEL" if pt == "clips" else "POST"
                print(f"        {it.get('code')} | {kind} | "
                      f"{(it.get('caption', {}).get('text') or '')[:30]!r}", flush=True)
            results.append(f"OK({len(items)})")
        else:
            print(f"      body: {r.text[:150]}", flush=True)
            if "feedback_required" in r.text:
                results.append("feedback_required!")
            else:
                results.append(f"http_{r.status_code}")
    except Exception as e:
        print(f"      FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
        results.append(type(e).__name__)
    return results


def probe_feed_anon() -> list:
    """方法 E（PR instaloader#2722 精确复刻）：匿名 feed/user/{id}/ 拉时间线。

    关键差异（相对此前失败的测试）：
      - 不带任何 cookie、不带 x-ig-app-id、不带 referer（纯净匿名）
      - 使用 instaloader 自带的 context.get_json（与 PR 完全相同的头与 UA）
      - 首页 params 只有 {'count': 12}；翻页用 {'count': 12, 'max_id': next_max_id}
    """
    from instaloader import Instaloader
    results = []
    for i in range(ATTEMPTS):
        L = Instaloader(quiet=True, download_comments=False, save_metadata=False,
                        post_metadata_txt_pattern="")
        L.context.max_connection_attempts = 2
        try:
            data = L.context.get_json(f"api/v1/feed/user/{USERID}/", params={"count": 12})
            items = data.get("items") or []
            print(f"  [{i+1}] feed OK: items={len(items)} "
                  f"more_available={data.get('more_available')} "
                  f"next_max_id={'有' if data.get('next_max_id') else '无'}", flush=True)
            for it in items[:5]:
                cap = ""
                cap_edges = (it.get("edge_media_to_caption") or {}).get("edges") or []
                if cap_edges:
                    cap = cap_edges[0].get("node", {}).get("text", "")
                print(f"        {it.get('code')} "
                      f"{'video' if it.get('media_type') == 2 else 'image'} "
                      f"| {cap[:36]}", flush=True)
            if items and data.get("next_max_id"):
                data2 = L.context.get_json(f"api/v1/feed/user/{USERID}/",
                                           params={"count": 12,
                                                   "max_id": data["next_max_id"]})
                print(f"      翻页 OK: 第二页 items={len(data2.get('items') or [])}",
                      flush=True)
            results.append("OK" if items else "empty")
        except Exception as e:
            print(f"  [{i+1}] FAIL: {type(e).__name__}: {str(e)[:130]}", flush=True)
            results.append(type(e).__name__)
        if i < ATTEMPTS - 1:
            time.sleep(GAP)
    return results


def _embedded_query_data(obj):
    """与 instaloader master _embedded_query_data 相同：递归收集 __bbox.result.data。"""
    if isinstance(obj, dict):
        result = obj["__bbox"].get("result") if isinstance(obj.get("__bbox"), dict) else None
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            yield result["data"]
        for value in obj.values():
            yield from _embedded_query_data(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _embedded_query_data(value)


def probe_profile_page(username: str = USERNAME, userid: str = USERID) -> list:
    """方法 F（PR instaloader#2782 精确复刻）：匿名主页 HTML 内嵌 JSON 抓最新帖子。

    背景（instaloader 社区 2026-10-19 结论，接替已死的匿名 feed/user）：
      匿名 graphql 时间线（doc_id 7950326061742207）与 api/v1/feed/user/ 均返回
      401，web_profile_info 401/429/400；唯一存活的是个人主页 HTML——未登录访客
      仍能看到最新 12 条（含置顶），内嵌在 <script type="application/json"> 的
      xig_user_by_username → polaris_ordered_timeline_connection.edges[*].node.code；
      随后匿名单帖查询（Post.from_shortcode，doc_id 27128499623469141）仍可用。
    关键实现细节（复刻 master 的 context.get_page_data，4.15.3 无此方法）：
      - 请求必须伪装成浏览器页面导航：Accept: text/html + Sec-Fetch-Dest/Mode/Site
      - 不跟随重定向（302 = 被要求登录）
      - 解析所有 application/json script 块，递归取 __bbox.result.data
    流程（纯匿名，不碰任何账号）：
      [1] GET /{username}/ → 用户 pk + 时间线短码列表（≤12 条）
      [2] 前两个短码各做一次匿名 from_shortcode 详情查询
    """
    import json
    import re as _re

    import requests
    from instaloader import Instaloader, Post

    results = []
    for i in range(ATTEMPTS):
        try:
            s = requests.Session()
            s.headers["User-Agent"] = ("Mozilla/5.0 (X11; Linux x86_64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/142.0.0.0 Safari/537.36")
            r = s.get(f"https://www.instagram.com/{username.lower()}/",
                      headers={"Accept": "text/html",
                               "Sec-Fetch-Dest": "document",
                               "Sec-Fetch-Mode": "navigate",
                               "Sec-Fetch-Site": "none"},
                      allow_redirects=False, timeout=25)
            print(f"  [{i+1}] page HTTP {r.status_code} bytes={len(r.text)}", flush=True)
            if r.status_code == 302:
                print(f"      重定向: {r.headers.get('Location', '')[:80]}（被要求登录）",
                      flush=True)
                results.append("redirect_302")
                continue
            if r.status_code != 200:
                print(f"      body: {r.text[:150]!r}", flush=True)
                results.append(f"http_{r.status_code}")
                continue
            blobs: list = []
            for script in _re.finditer(
                    r'<script type="application/json"[^>]*>(.*?)</script>',
                    r.text, _re.DOTALL):
                try:
                    blobs.extend(_embedded_query_data(json.loads(script.group(1))))
                except json.decoder.JSONDecodeError:
                    pass
            user: dict = {}
            for data in blobs:
                user.update(data.get("xig_user_by_username") or {})
            timeline = user.pop("polaris_ordered_timeline_connection", None) or {}
            edges = timeline.get("edges") or []
            codes = [e["node"].get("code") for e in edges
                     if isinstance(e, dict) and e.get("node")]
            print(f"      json_blobs={len(blobs)} user_pk={user.get('pk')} "
                  f"edges={len(edges)} has_next={timeline.get('page_info', {}).get('has_next_page')}",
                  flush=True)
            for idx, e in enumerate(edges, 1):
                node = e.get("node") or {}
                code = node.get("code")
                pt = node.get("product_type") or f"type{node.get('media_type')}"
                cap = node.get("caption")
                if isinstance(cap, dict):
                    cap = cap.get("text") or ""
                print(f"      {idx:>2}. [{pt}] {code}  {(cap or '')[:52]!r}", flush=True)
            codes = [e["node"].get("code") for e in edges
                     if isinstance(e, dict) and e.get("node")]
            if not codes:
                results.append("no_posts" if user.get("pk") else "no_user_data")
                continue
            # [2] 匿名单帖详情（PR 依赖的另一半：from_shortcode 仍匿名可用）
            L = Instaloader(quiet=True, download_comments=False, save_metadata=False,
                            post_metadata_txt_pattern="")
            L.context.max_connection_attempts = 2
            for code in codes[:2]:
                try:
                    p = Post.from_shortcode(L.context, code)
                    print(f"        detail {code}: type={p.typename} "
                          f"date={p.date_local:%Y-%m-%d %H:%M} "
                          f"caption={(p.caption or '')[:36]!r}", flush=True)
                except Exception as e:
                    print(f"        detail {code}: FAIL {type(e).__name__}: "
                          f"{str(e)[:110]}", flush=True)
            results.append("OK")
        except Exception as e:
            print(f"  [{i+1}] FAIL: {type(e).__name__}: {str(e)[:130]}", flush=True)
            results.append(type(e).__name__)
        if i < ATTEMPTS - 1:
            time.sleep(GAP)
    return results


_POOL_CAP = 600
_POOL_WORKERS = 50
# 已验证存活的免费代理（按存活轮次降序、再按新旧）——优先逐个尝试，命中即用。
# 生产实现：该列表来自 state 缓存（上一周期验证过的好代理排最前），本处为实测快照。
_KNOWN_GOOD_PROXIES = [
    "45.232.210.225:999",   # 3 轮中存活 2 轮（G v1、v3），v3 经它完成详情查询 200
    # ---- G v3（2026-10-24 03:24 UTC）----
    "173.180.22.3:80", "201.4.69.29:8080", "103.189.97.38:1080",
    "182.253.109.102:8080", "103.82.246.93:6080", "200.59.191.137:999",
    "103.173.230.250:8080", "189.90.248.229:8080", "176.88.65.165:8080",
    # ---- G v2（2026-10-24 03:15 UTC）----
    "201.7.31.115:8080", "82.152.138.127:999", "8.242.245.230:999",
    "193.43.140.85:8080", "202.58.77.195:8080", "190.94.213.20:999",
    "203.76.222.232:8090",
    # ---- G v1（2026-10-24 02:50 UTC）----
    "205.234.123.37:8080", "119.94.127.108:8081", "202.58.77.239:8080",
    "186.167.113.103:999", "45.149.24.19:8088", "179.126.34.189:8080",
    "5.129.254.243:8888", "181.78.195.137:999",
]
_POOL_SOURCES = [
    ("TheSpeedX", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("monosans", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("proxifly", "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt"),
]
_URL_PROXY_SERVICES = [
    ("cors.workers.dev", "https://test.cors.workers.dev/?{url}"),
    ("allorigins", "https://api.allorigins.win/raw?url={url}"),
    ("corsproxy.io", "https://corsproxy.io/?url={url}"),
    ("codetabs", "https://api.codetabs.com/v1/proxy?quest={url}"),
]
PROBE_ACCOUNTS = [  # 与 monitor/config.IG_ACCOUNTS 一致
    ("miyamoto_doppo", 10584438821),
    ("elephantsinc_official", 66821998949),
    ("h.m.staff", 71496324324),
]


def probe_url_proxies() -> list:
    """方法 H：公共 URL 转发服务（跑在 CF Workers 等非 Azure 出口）能否带回内嵌数据。

    若任一服务成功，= 该云出口能过 IG 的 IP 墙 → "免费自建 CF Worker" 有据可依。
    这些服务不透传 Sec-Fetch-* 头，成功率存疑——实测说话。纯匿名，每服务一次。
    """
    import json
    import re
    from urllib.parse import quote

    import requests
    out = []
    for name, tpl in _URL_PROXY_SERVICES:
        try:
            r = requests.get(tpl.format(url=quote(f"https://www.instagram.com/{USERNAME.lower()}/",
                                                  safe="")),
                             timeout=25, headers={"User-Agent": "Mozilla/5.0"})
            hit = False
            if "xig_user_by_username" in r.text:
                for script in re.finditer(
                        r'<script type="application/json"[^>]*>(.*?)</script>',
                        r.text, re.DOTALL):
                    try:
                        for data in _embedded_query_data(json.loads(script.group(1))):
                            if str((data.get("xig_user_by_username") or {}).get("pk")) == USERID:
                                hit = True
                                break
                    except json.decoder.JSONDecodeError:
                        pass
                    if hit:
                        break
            print(f"  {name}: HTTP {r.status_code} bytes={len(r.text)} embedded={hit}",
                  flush=True)
            out.append(f"{name}:{'OK' if hit else f'http_{r.status_code}'}")
        except Exception as e:
            print(f"  {name}: FAIL {type(e).__name__}: {str(e)[:90]}", flush=True)
            out.append(f"{name}:fail")
    return out


def probe_free_pool() -> list:
    """方法 G：免费公开代理池 × 匿名主页路线（PR #2782）实测。

    回答"不买代理、家里机器不常开，免费出口能否过 IG 的 IP 墙"。
    纪律：纯匿名零账号；每个代理只碰一次；单请求超时 5s+10s。
    成功判据：HTTP 200 且解析出目标 pk 的时间线（≥1 条 edges）——
    免费代理可能伪造/污染响应，必须验证数据本体，不认状态码。
    成功者用 ip-api 分类出口性质（residential / hosting / mobile）。
    """
    import json
    import random
    import re as _re
    from concurrent.futures import ThreadPoolExecutor

    import requests

    def fetch_pool() -> list:
        found: list = []
        for name, url in _POOL_SOURCES:
            try:
                r = requests.get(url, timeout=20)
                if r.status_code != 200:
                    print(f"  源 {name}: HTTP {r.status_code}", flush=True)
                    continue
                ips = []
                for ln in r.text.splitlines():
                    ln = ln.strip().split(",")[0]
                    if ln.startswith("http://"):
                        ln = ln[7:]
                    if _re.fullmatch(r"\d+\.\d+\.\d+\.\d+:\d+", ln):
                        ips.append(ln)
                print(f"  源 {name}: {len(ips)} 条 http 代理", flush=True)
                found.extend(ips)
            except Exception as e:
                print(f"  源 {name}: {type(e).__name__}: {str(e)[:80]}", flush=True)
        uniq = list(dict.fromkeys(found))
        random.shuffle(uniq)
        return uniq[:_POOL_CAP]

    ok_code: dict = {}   # 可用代理 → 首条短码（供同代理详情查询闭环）
    def check(proxy: str):
        try:
            r = requests.get(f"https://www.instagram.com/{USERNAME.lower()}/",
                             headers={"Accept": "text/html",
                                      "Sec-Fetch-Dest": "document",
                                      "Sec-Fetch-Mode": "navigate",
                                      "Sec-Fetch-Site": "none",
                                      "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                    "Chrome/142.0.0.0 Safari/537.36"},
                             proxies={"http": f"http://{proxy}", "https": f"http://{proxy}"},
                             timeout=(5, 10), allow_redirects=False)
        except Exception:
            return proxy, "err", 0
        if r.status_code != 200:
            return proxy, f"http_{r.status_code}", 0
        try:
            for script in _re.finditer(
                    r'<script type="application/json"[^>]*>(.*?)</script>',
                    r.text, _re.DOTALL):
                try:
                    for data in _embedded_query_data(json.loads(script.group(1))):
                        u = data.get("xig_user_by_username") or {}
                        if str(u.get("pk")) == USERID:
                            edges = ((u.get("polaris_ordered_timeline_connection") or {})
                                     .get("edges")) or []
                            if edges:
                                ok_code[proxy] = edges[0].get("node", {}).get("code")
                                return proxy, "ok", len(edges)
                except json.decoder.JSONDecodeError:
                    pass
        except Exception:
            pass
        return proxy, "no_data", len(r.text)

    import os
    results, oks = [], []
    print(f"  已知好代理优先：{len(_KNOWN_GOOD_PROXIES)} 个逐个单触", flush=True)
    kg_alive = []
    for p in _KNOWN_GOOD_PROXIES:
        proxy, status, extra = check(p)
        results.append((proxy, status, extra))
        if status == "ok":
            oks.append((p, extra))
            kg_alive.append(p)
    print(f"  已知好代理存活: {len(kg_alive)}/{len(_KNOWN_GOOD_PROXIES)}: "
          f"{kg_alive or '无'}", flush=True)
    if os.environ.get("IG_POOL_SKIP") and kg_alive:
        print("  已知好代理命中 → 跳过池子阶段（IG_POOL_SKIP=1）", flush=True)
    else:
        pool = fetch_pool()
        print(f"  池子: {len(pool)} 个候选，{_POOL_WORKERS} 并发，每个只碰一次", flush=True)
        with ThreadPoolExecutor(max_workers=_POOL_WORKERS) as ex:
            for res in ex.map(check, pool):
                results.append(res)
    stats: dict = {}
    for _, s, _ in results:
        stats[s] = stats.get(s, 0) + 1
    oks = [(p, e) for p, s, e in results if s == "ok"]
    print(f"  统计: {stats}", flush=True)
    for p, edges in oks[:10]:
        try:
            info = requests.get(f"http://ip-api.com/json/{p.split(':')[0]}"
                                "?fields=isp,org,mobile,proxy,hosting", timeout=8).json()
            print(f"  WORKING {p} edges={edges} isp={info.get('isp')} "
                  f"hosting={info.get('hosting')} mobile={info.get('mobile')}", flush=True)
        except Exception:
            print(f"  WORKING {p} edges={edges}", flush=True)

    # 闭环验证（= 生产流程原型）：经可用代理做内容转储与单帖详情。
    # 免费代理"刚验证完就死"是常态 → 失败自动换下一个可用代理（生产同款逻辑）。
    # 详情查询 403 修正：复用 Session——先触一次页面拿 csrftoken，再带 X-CSRFToken 打 graphql。
    if oks:
        print(f"  内容演示（{len(oks)} 个可用代理轮换）:", flush=True)
        pi = 0
        for name, uid in PROBE_ACCOUNTS:
            for _ in range(4):
                if pi >= len(oks):
                    print(f"    @{name}: 可用代理耗尽", flush=True)
                    break
                p0 = oks[pi][0]
                try:
                    r = requests.get(f"https://www.instagram.com/{name.lower()}/",
                                     headers={"Accept": "text/html",
                                              "Sec-Fetch-Dest": "document",
                                              "Sec-Fetch-Mode": "navigate",
                                              "Sec-Fetch-Site": "none",
                                              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                                                            "Chrome/142.0.0.0 Safari/537.36"},
                                     proxies={"http": f"http://{p0}", "https": f"http://{p0}"},
                                     timeout=(5, 12), allow_redirects=False)
                    if r.status_code != 200:
                        print(f"    @{name} via {p0}: HTTP {r.status_code} → 换代理", flush=True)
                        pi += 1
                        continue
                    user: dict = {}
                    for script in _re.finditer(
                            r'<script type="application/json"[^>]*>(.*?)</script>',
                            r.text, _re.DOTALL):
                        try:
                            for data in _embedded_query_data(json.loads(script.group(1))):
                                u = data.get("xig_user_by_username") or {}
                                if u.get("pk"):
                                    user.update(u)
                        except json.decoder.JSONDecodeError:
                            pass
                    tl = user.pop("polaris_ordered_timeline_connection", None) or {}
                    edges = tl.get("edges") or []
                    print(f"    @{name} (pk={user.get('pk')} 经 {p0}): edges={len(edges)}",
                          flush=True)
                    for idx, e in enumerate(edges, 1):
                        node = e.get("node") or {}
                        code = node.get("code")
                        pt = node.get("product_type") or f"type{node.get('media_type')}"
                        cap = node.get("caption")
                        if isinstance(cap, dict):
                            cap = cap.get("text") or ""
                        path = "reel" if pt == "clips" else "p"
                        print(f"      {idx:>2}. [{pt}] {code}  "
                              f"https://www.instagram.com/{path}/{code}/  "
                              f"{(cap or '')[:48]!r}", flush=True)
                    break
                except Exception as e:
                    print(f"    @{name} via {p0}: {type(e).__name__} → 换代理", flush=True)
                    pi += 1
    for p, _ in oks[:3]:
        code = ok_code.get(p)
        if not code:
            continue
        try:
            s = requests.Session()
            s.proxies = {"http": f"http://{p}", "https": f"http://{p}"}
            s.headers.update({"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                                            "Chrome/142.0.0.0 Safari/537.36",
                              "X-IG-App-ID": "936619743392459",
                              "X-Requested-With": "XMLHttpRequest",
                              "Referer": f"https://www.instagram.com/{USERNAME.lower()}/",
                              "Accept": "*/*"})
            s.get(f"https://www.instagram.com/{USERNAME.lower()}/",
                  headers={"Accept": "text/html", "Sec-Fetch-Dest": "document",
                           "Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "none"},
                  timeout=(5, 10), allow_redirects=False)
            csrf = s.cookies.get("csrftoken")
            if csrf:
                s.headers["X-CSRFToken"] = csrf
            rr = s.post("https://www.instagram.com/graphql/query",
                        data={"doc_id": "27128499623469141",
                              "variables": json.dumps({
                                  "shortcode": code,
                                  "__relay_internal__pv__PolarisAIGMMediaWebLabelEnabledrelayprovider": False})},
                        timeout=(5, 12))
            ok = "xdt_api__v1__media__shortcode__web_info" in rr.text
            print(f"  DETAIL via {p} shortcode={code}: HTTP {rr.status_code} data={ok}",
                  flush=True)
        except Exception as e:
            print(f"  DETAIL via {p}: {type(e).__name__}: {str(e)[:80]}", flush=True)
    return [p for p, _ in oks]


def main():
    print(f"IG anonymous probe v7: userid={USERID} attempts={ATTEMPTS}", flush=True)
    print("===== E. anonymous feed/user/{id} (PR #2722 复刻，对照：上游已 401) =====", flush=True)
    re_ = probe_feed_anon()
    time.sleep(GAP)
    print("===== F. anonymous profile-page HTML (PR #2782 复刻，三账号内容实测) =====", flush=True)
    rf = []
    for name, uid in PROBE_ACCOUNTS:
        print(f"  --- @{name} (uid={uid}) ---", flush=True)
        rf.extend(probe_profile_page(name, str(uid)))
        time.sleep(GAP)
    time.sleep(GAP)
    print("===== H. public URL-proxy services (CF Workers 等出口) =====", flush=True)
    rh = probe_url_proxies()
    print("===== G. free proxy pool × profile-page (免费出口实测) =====", flush=True)
    rg = probe_free_pool()
    print("===== SUMMARY =====", flush=True)
    print(f"E feed-anon: {re_}", flush=True)
    print(f"F profile-page: {rf}", flush=True)
    print(f"H url-proxy: {rh}", flush=True)
    print(f"G free-pool: working={len(rg)} {rg[:8]}", flush=True)


if __name__ == "__main__":
    main()
