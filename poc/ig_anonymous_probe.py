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


def main():
    print(f"IG anonymous probe v2: {USERNAME}({USERID}) attempts={ATTEMPTS}", flush=True)
    print("\n===== A. instagrapi public_transport=curl + chrome136 =====", flush=True)
    ra = probe_instagrapi()
    time.sleep(GAP)
    print("\n===== C. anonymous graphql doc_id timeline (核心验证) =====", flush=True)
    rc = probe_graphql_timeline()
    time.sleep(GAP)
    print("\n===== B. Instaloader from_id + get_posts (对照) =====", flush=True)
    rb = probe_instaloader()
    print("\n===== SUMMARY =====", flush=True)
    print(f"A instagrapi-public: {ra}", flush=True)
    print(f"C graphql-timeline:  {rc}", flush=True)
    print(f"B instaloader:       {rb}", flush=True)


if __name__ == "__main__":
    main()
