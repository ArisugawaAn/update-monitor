"""统一监测编排：每个来源独立 try/except，一个来源失败不影响其他来源。

用法：
  python -m monitor.main            # 单轮检查（cron 每次调用一轮）
  python -m monitor.main --loop 5   # 本地连续模式：每 5 分钟一轮
"""
import sys
import time
from datetime import datetime, timezone

from monitor import config
from monitor.models import CheckpointError
from monitor.notification import email
from monitor.storage import state as st


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def build_sources() -> list:
    from monitor.sources import ig_story, tiktok, websites, x_twitter, youtube
    return [
        ig_story.source(),
        x_twitter.source(),
        *[youtube.source(name, cid) for name, cid in config.YOUTUBE_CHANNELS],
        *[tiktok.source(handle, sec) for handle, sec in config.TIKTOK_ACCOUNTS],
        *[websites.source(*site) for site in websites.SITES],
    ]


def run_once(sources: list, state: dict, counters: dict, disabled: set,
             now: float | None = None) -> tuple[list[tuple[str, str]], int, int]:
    """返回 (摘要, 成功源数, 失败源数)。全部失败时 main 以非零退出，让 CI 可见。

    频率控制：按 state[last_checked_at] 判断是否到期，未到期直接跳过（不发
    HTTP 请求、不更新 last_checked_at、不计入成功/失败）。
    """
    summary: list[tuple[str, str]] = []
    ok = 0
    fail = 0
    now_ts = _now_ts() if now is None else now
    for src in sources:
        if src.key in disabled:  # checkpoint 后本轮剩余时间不再请求
            summary.append((src.key, "已停用（等待 checkpoint 处理）"))
            fail += 1
            continue
        interval = config.check_interval_minutes(src.key)
        if not st.is_due(state, src.key, interval, now_ts):
            summary.append((src.key, f"跳过（未到时间，间隔 {interval} 分钟）"))
            continue
        # 只有实际发起 HTTP 请求后才更新 last_checked_at（含失败轮次，
        # 否则失败源会每轮重试，突破频率限制）。
        st.set_last_checked_at(state, src.key, now_ts)
        st.save_state(config.STATE_FILE, state)
        try:
            updates = src.check()
        except CheckpointError as e:
            disabled.add(src.key)
            email.send_alert(f"【监测报警】{src.key} 账号需 checkpoint 验证", str(e))
            summary.append((src.key, f"⛔ {e}"))
            fail += 1
            continue
        except Exception as e:
            counters[src.key] = counters.get(src.key, 0) + 1
            n = counters[src.key]
            note = f"失败 x{n}: {type(e).__name__}: {e}"[:140]
            if n == config.ALERT_THRESHOLD:  # 恰好跨过阈值时报警一次
                email.send_alert(f"【监测报警】{src.key} 连续 {n} 轮失败", f"{type(e).__name__}: {e}")
            summary.append((src.key, note))
            fail += 1
            continue

        ok += 1
        counters[src.key] = 0
        notify_list, baseline = st.diff_new(state, src.key, updates)
        st.save_state(config.STATE_FILE, state)
        delivered: list = []
        for u in notify_list:
            if email.send_update(u):
                delivered.append(u)
        if delivered:
            st.mark_notified(state, src.key, delivered)
            st.save_state(config.STATE_FILE, state)
        tag = " [baseline 建立，历史不通知]" if baseline else ""
        summary.append((src.key, f"在场 {len(updates)} / 新 {len(notify_list)}{tag}"))
    return summary, ok, fail


def health_check(sources: list) -> int:
    """健康检查：逐源探测（不写状态、不发通知）+ 发送测试邮件验证通路。"""
    lines: list[str] = []
    ok = 0
    for src in sources:
        try:
            updates = src.check()
            lines.append(f"{src.key:<22} 正常，在场 {len(updates)} 条")
            ok += 1
        except Exception as e:
            lines.append(f"{src.key:<22} 异常: {type(e).__name__}: {e}"[:600])
    sent = email.send_test(lines)
    print(f"数据源健康: {ok}/{len(sources)} 正常")
    print("测试邮件:", "已发送（查收邮箱/微信）" if sent else "发送失败")
    return 0 if sent else 1


def main() -> None:
    if "--test-email" in sys.argv:
        sys.exit(health_check(build_sources()))

    loop_minutes = 0
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        loop_minutes = int(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 5

    sources = build_sources()
    state = st.load_state(config.STATE_FILE)
    counters: dict = {}
    disabled: set = set()
    print(f"监测源: {[s.key for s in sources]}")
    while True:
        print(f"===== 检查 {datetime.now():%Y-%m-%d %H:%M:%S} =====")
        summary, ok, fail = run_once(sources, state, counters, disabled)
        for key, msg in summary:
            print(f"  {key:<22} {msg}")
        print(f"  >> 成功 {ok} / 失败 {fail}")
        if ok == 0 and (fail or disabled):
            sys.exit(1)  # 全部来源失败：让 CI 标红可见
        if not loop_minutes:
            break
        time.sleep(loop_minutes * 60)


if __name__ == "__main__":
    main()
