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
    from monitor.sources import ig_post, ig_story, tiktok, websites, x_twitter, youtube
    return [
        ig_story.source(),
        ig_post.source(),
        *[x_twitter.source(user) for user in config.X_USERS],  # 主号在前，每轮查
        *[youtube.source(name, cid) for name, cid in config.YOUTUBE_CHANNELS],
        *[tiktok.source(handle, sec) for handle, sec in config.TIKTOK_ACCOUNTS],
        *[websites.source(*site) for site in websites.SITES],
    ]


def run_once(sources: list, state: dict, counters: dict, disabled: set,
             now: float | None = None, tick: int | None = None) -> tuple[list[tuple[str, str]], int, int]:
    """返回 (摘要, 成功源数, 失败源数)。全部失败时 main 以非零退出，让 CI 可见。

    频率控制（按轮次，不受 Actions 调度延迟漂移影响）：每轮 Action = 1 tick，
    5 分钟源每轮查，10 分钟源隔 1 轮查，60 分钟源每 12 轮查。未到轮次直接跳过
    （不发 HTTP 请求、不更新 last_checked_tick、不计入成功/失败/连续失败）。
    连续失败计数持久化在 state[fail_streak]，跨进程累计，成功清零。
    失败冷却：仅 config.COOLDOWN_SOURCES 内的源（当前 = ig_post）失败后本源跳过
    N 轮（限流类失败更久），期间不发请求、不计成功/失败、其他源完全不受影响；
    检查恢复成功后自动解除冷却并重新武装报警（与 ig_story 的 checkpoint 同构）。
    counters 参数保留兼容（本地调试可读），实际以 state 为准。
    """
    summary: list[tuple[str, str]] = []
    ok = 0
    fail = 0
    now_ts = _now_ts() if now is None else now
    cur_tick = st.get_tick(state) if tick is None else tick
    hour_first = st.is_new_hour(state, now_ts)  # 自然小时第一次 tick → 全源 sweep
    if hour_first:
        st.set_sweep_hour(state, now_ts)  # 请求前记录（与 tick 门控同一取舍）
        st.save_state(config.STATE_FILE, state)
    for src in sources:
        if src.key in disabled:  # checkpoint 后本轮剩余时间不再请求
            summary.append((src.key, "已停用（等待 checkpoint 处理）"))
            fail += 1
            continue
        cd_left = st.cooldown_remaining_ticks(state, src.key, cur_tick)
        if cd_left:  # 失败冷却中：本源零请求（整点 sweep 也不破例），其他源照常
            summary.append((src.key, f"冷却中（跳过 {cd_left} 轮，避免限流封禁）"))
            continue
        interval_ticks = config.check_interval_ticks(src.key)
        interval_min = config.check_interval_minutes(src.key)
        if config.is_hour_aligned(src.key):
            # 整点对齐源：只在每自然小时的 sweep tick 执行（≈HH:00，自然顺延不刻意延后）
            if not hour_first:
                summary.append((src.key, "跳过（等待下一个整点 sweep）"))
                continue
        elif not (hour_first or st.is_due_tick(state, src.key, interval_ticks, cur_tick)):
            # 非整点对齐源：正常按轮次节奏；但 sweep tick 强制全量访问一次
            summary.append((src.key, f"跳过（未到轮次，间隔 {interval_min} 分钟/每 {interval_ticks} 轮）"))
            continue
        # 只有实际发起 HTTP 请求后才更新门控标记（含失败轮次，
        # 否则失败源会每轮重试，突破频率限制）。
        st.set_last_checked_tick(state, src.key, cur_tick)
        st.set_last_checked_at(state, src.key, now_ts)
        st.save_state(config.STATE_FILE, state)
        try:
            updates = src.check()
        except CheckpointError as e:
            disabled.add(src.key)
            if not st.get_alert_flag(state, src.key, "checkpoint"):
                st.set_alert_flag(state, src.key, "checkpoint")
                st.save_state(config.STATE_FILE, state)
                email.send_alert(f"【监测报警】{src.key} 账号需 checkpoint 验证",
                                 f"{e}\n恢复方法见仓库 README；本事件只报一次，"
                                 f"源恢复成功后自动重新武装。")
            summary.append((src.key, f"⛔ {e}"))
            fail += 1
            continue
        except Exception as e:
            n = st.get_fail_streak(state, src.key) + 1  # 跨轮持久化累计
            st.set_fail_streak(state, src.key, n)
            err = f"{type(e).__name__}: {e}"
            cool = config.cooldown_ticks(src.key, err)  # 未启用冷却的源恒为 0
            limited = cool > 0 and config.is_rate_limited(err)
            tag = ""
            if cool:  # 冷却 = 本源后续 N 轮不发请求（仅本源，其他源不受影响）
                st.set_cooldown_until_tick(state, src.key, cur_tick + cool)
                tag = f"（{'限流' if limited else '失败'}，本源冷却 {cool} 轮）"
            st.save_state(config.STATE_FILE, state)
            counters[src.key] = n
            note = f"失败 x{n}{tag}: {err}"[:160]
            if n == config.ALERT_THRESHOLD:  # 恰好跨过阈值时报警一次
                email.send_alert(f"【监测报警】{src.key} 连续 {n} 次检查失败", err)
            if limited and not st.get_alert_flag(state, src.key, "ratelimit"):
                # 限流事件按事件去重报警（与 ig_story checkpoint 同构），恢复后重新武装
                st.set_alert_flag(state, src.key, "ratelimit")
                st.save_state(config.STATE_FILE, state)
                email.send_alert(
                    f"【监测报警】{src.key} 触发平台限流，已冷却 {cool} 轮",
                    f"{err}\n\n本源已自动跳过后续 {cool} 轮"
                    f"（≈{cool * config.check_interval_minutes(src.key)} 分钟）："
                    f"冷却期间不发任何请求，以免加重账号标记；"
                    f"检查恢复成功后自动解除冷却，其余数据源照常运行。")
            summary.append((src.key, note))
            fail += 1
            continue

        ok += 1
        st.set_fail_streak(state, src.key, 0)  # 成功清零
        st.clear_cooldown(state, src.key)  # 恢复成功 → 解除冷却
        st.clear_alert_flag(state, src.key, "checkpoint")  # 恢复后重新武装报警
        st.clear_alert_flag(state, src.key, "ratelimit")  # 限流报警同样重新武装
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
    counters: dict = {k: st.get_fail_streak(state, k) for k in
                      [s.key for s in sources]}  # 从持久化恢复（跨轮累计）
    disabled: set = set()
    print(f"监测源: {[s.key for s in sources]}")
    while True:
        tick = st.advance_tick(state)  # 每轮 +1；CI 回写 state 后下一轮继续累加
        st.save_state(config.STATE_FILE, state)
        print(f"===== 检查 {datetime.now():%Y-%m-%d %H:%M:%S}（第 {tick} 轮） =====")
        summary, ok, fail = run_once(sources, state, counters, disabled, tick=tick)
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
