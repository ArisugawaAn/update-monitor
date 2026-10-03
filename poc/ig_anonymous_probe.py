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
    """方法 D：生产 story cookie（IG_COOKIE secret）+ www web 端点查 post。

    与 ig_story 同机制（浏览器 UA + x-ig-app-id + 整串 Cookie 头），端点换成
    post 相关：验证 cookie 会话能否从数据中心出口解锁被 IP 门禁挡住的
    web_profile_info / feed/user。这是 misiektoja 项目推荐做法的实测。
    日志绝不打印 cookie 本体（GitHub 亦会自动打码 secret）。
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
    for i in range(ATTEMPTS):
        try:
            r = s.get("https://www.instagram.com/api/v1/users/web_profile_info/",
                      params={"username": USERNAME}, timeout=25)
            print(f"  [{i+1}] web_profile_info HTTP {r.status_code}", flush=True)
            if r.status_code == 200:
                j = r.json().get("data", {}).get("user", {})
                print(f"      user={j.get('username')} "
                      f"posts={j.get('edge_owner_to_timeline_media', {}).get('count')}",
                      flush=True)
                r2 = s.get(f"https://www.instagram.com/api/v1/feed/user/{USERID}/",
                           params={"count": 5}, timeout=25)
                print(f"      feed/user HTTP {r2.status_code}", flush=True)
                if r2.status_code == 200:
                    for it in r2.json().get("items", [])[:5]:
                        print(f"        {it.get('code')} "
                              f"{'video' if it.get('media_type') == 2 else 'image'}",
                              flush=True)
                    results.append("OK")
                else:
                    print(f"      feed body: {r2.text[:120]}", flush=True)
                    results.append(f"profile_ok/feed_{r2.status_code}")
            else:
                print(f"      body: {r.text[:120]}", flush=True)
                results.append(f"http_{r.status_code}")
        except Exception as e:
            print(f"  [{i+1}] FAIL: {type(e).__name__}: {str(e)[:120]}", flush=True)
            results.append(type(e).__name__)
        if i < ATTEMPTS - 1:
            time.sleep(GAP)
    return results


def main():
    print(f"IG anonymous probe v3: {USERNAME}({USERID}) attempts={ATTEMPTS}", flush=True)
    print("\n===== D. story-cookie web 会话查 post（核心验证）=====", flush=True)
    rd = probe_cookie_web()
    time.sleep(GAP)
    print("\n===== C. anonymous graphql doc_id timeline =====", flush=True)
    rc = probe_graphql_timeline()
    print("\n===== SUMMARY =====", flush=True)
    print(f"D cookie-web:       {rd}", flush=True)
    print(f"C graphql-timeline: {rc}", flush=True)


if __name__ == "__main__":
    main()
