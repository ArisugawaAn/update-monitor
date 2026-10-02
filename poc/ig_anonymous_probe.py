"""在 GitHub Actions 真实出口验证两条免账号通道（一次性实验，与监控主流程无关）。

只读实验：不登录任何账号、不使用任何 cookie/token、不读写 monitor_state.json、
不发通知、不修改任何业务代码。结果用于决策"是否把免账号通道接为监控第一层"。

用法：
  python poc/ig_anonymous_probe.py [username] [attempts_per_channel]

默认 username=miyamoto_doppo、attempts=3；同一通道内每次尝试间隔 30 秒。

测试对象：
  A. instagrapi public_transport="curl" + chrome136 指纹
     （user_info_by_username → 成功则继续 user_medias 取最近 5 条）
  B. Instaloader 匿名（Profile.from_username → get_posts 取前 5 条短码）
"""
import sys
import time

USERNAME = sys.argv[1] if len(sys.argv) > 1 else "miyamoto_doppo"
ATTEMPTS = int(sys.argv[2]) if len(sys.argv) > 2 else 3
GAP = 30


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


def probe_instaloader() -> list:
    from instaloader import Instaloader, Profile
    results = []
    for i in range(ATTEMPTS):
        L = Instaloader(quiet=True, download_comments=False, save_metadata=False,
                        post_metadata_txt_pattern="")
        L.context.max_connection_attempts = 2
        try:
            p = Profile.from_username(L.context, USERNAME)
            print(f"  [{i+1}] profile OK: userid={p.userid} mediacount={p.mediacount}",
                  flush=True)
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
    print(f"IG anonymous probe: username={USERNAME} attempts/channel={ATTEMPTS}", flush=True)
    print("\n===== A. instagrapi public_transport=curl + chrome136 =====", flush=True)
    ra = probe_instagrapi()
    time.sleep(GAP)
    print("\n===== B. Instaloader anonymous =====", flush=True)
    rb = probe_instaloader()
    print("\n===== SUMMARY =====", flush=True)
    print(f"A instagrapi-public: {ra}", flush=True)
    print(f"B instaloader:       {rb}", flush=True)


if __name__ == "__main__":
    main()
