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


def main():
    print(f"IG anonymous probe v5: userid={USERID} attempts={ATTEMPTS}", flush=True)
    print("===== E. anonymous feed/user/{id} (PR #2722 复刻，核心验证) =====", flush=True)
    re_ = probe_feed_anon()
    print("===== SUMMARY =====", flush=True)
    print(f"E feed-anon: {re_}", flush=True)


if __name__ == "__main__":
    main()
