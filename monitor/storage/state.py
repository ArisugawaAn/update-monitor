"""运行状态：per-source 的 seen/notified/baselined，JSON 持久化。

- seen：抓到即登记（防重复插入）
- notified：邮件投递成功后才登记（投递至少一次、通知至多一次）
- baselined：首次运行建立基线，历史内容不通知
- tick：全局轮次计数（每轮 Action +1），按轮次门控各源检查频率
- fail_streak：per-source 连续“检查轮次”失败计数，跨进程持久化（Actions
  每轮都是全新进程，内存计数无法跨轮累计）
"""
import json
from pathlib import Path

from monitor.models import Update

SEEN_CAP = 2000  # 单源 seen 上限，滚动清理


def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"sources": {}}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


def _bucket(state: dict, key: str) -> dict:
    return state["sources"].setdefault(
        key, {"seen": [], "notified": [], "baselined": False})


def diff_new(state: dict, key: str, updates: list[Update]) -> tuple[list[Update], bool]:
    """登记 seen 并返回 (本次需要通知的, 是否为 baseline 轮)。"""
    b = _bucket(state, key)
    new = [u for u in updates if u.external_id and u.external_id not in b["seen"]]
    for u in new:
        b["seen"].append(u.external_id)
    del b["seen"][:-SEEN_CAP]
    baseline = not b["baselined"]
    b["baselined"] = True
    return ([] if baseline else new), baseline


def mark_notified(state: dict, key: str, updates: list[Update]) -> None:
    b = _bucket(state, key)
    b["notified"].extend(u.external_id for u in updates)
    del b["notified"][:-SEEN_CAP]


def get_last_checked_at(state: dict, key: str) -> float | None:
    """返回某 source 上次实际发起检查的时间戳（秒）。从未检查返回 None。

    兼容旧 state：缺字段的旧 bucket 返回 None（= 首次运行，必须检查）。
    保留原因：诊断/兼容旧数据；实际频率门控已改用按轮次 tick。
    """
    b = state.get("sources", {}).get(key)
    if not b:
        return None
    ts = b.get("last_checked_at")
    return float(ts) if isinstance(ts, (int, float)) else None


def set_last_checked_at(state: dict, key: str, ts: float) -> None:
    """记录上次实际发起检查的时间（诊断用）；跳过时不得调用。"""
    _bucket(state, key)["last_checked_at"] = float(ts)


def is_due(state: dict, key: str, interval_minutes: int, now: float) -> bool:
    """兼容保留：墙钟到期判断。从未检查过 → True。

    实际频率门控已改用 is_due_tick（按轮次，不受 Actions 调度延迟漂移影响）。
    """
    last = get_last_checked_at(state, key)
    if last is None:
        return True
    return (now - last) >= interval_minutes * 60


def get_tick(state: dict) -> int:
    """全局轮次计数；旧 state 缺字段返回 0。"""
    tick = state.get("tick")
    return int(tick) if isinstance(tick, (int, float)) else 0


def advance_tick(state: dict) -> int:
    """本轮开始时 +1，返回新 tick。"""
    state["tick"] = get_tick(state) + 1
    return state["tick"]


def is_due_tick(state: dict, key: str, interval_ticks: int, tick: int) -> bool:
    """按轮次门控：从未检查过 → True；否则 (tick - last_tick) >= interval。

    last_tick 缺失的旧 bucket 视为从未检查（首次必须查）。
    interval=1 时同样走 (tick - last) >= 1 判断 —— tick 每轮 +1，
    正常情况下等价于"每轮必查"，但失败退避可把 last_tick 推向未来以跳过轮次。
    """
    b = state.get("sources", {}).get(key)
    if not b:
        return True
    last = b.get("last_checked_tick")
    if not isinstance(last, (int, float)):
        return True
    return (tick - int(last)) >= interval_ticks


def set_last_checked_tick(state: dict, key: str, tick: int) -> None:
    """仅在实际发起 HTTP 请求后调用；跳过时不得调用。"""
    _bucket(state, key)["last_checked_tick"] = int(tick)


def get_fail_streak(state: dict, key: str) -> int:
    """per-source 连续检查失败次数；旧 state 缺字段返回 0。"""
    b = state.get("sources", {}).get(key)
    if not b:
        return 0
    n = b.get("fail_streak")
    return int(n) if isinstance(n, (int, float)) and int(n) > 0 else 0


def set_fail_streak(state: dict, key: str, n: int) -> None:
    """持久化连续失败计数（成功时置 0）。"""
    _bucket(state, key)["fail_streak"] = int(n)


def get_alert_flag(state: dict, key: str, kind: str) -> bool:
    """某源某类报警是否已处于"已报警"状态（事件去重用）。"""
    b = state.get("sources", {}).get(key)
    return bool(b and b.get("alerts", {}).get(kind))


def set_alert_flag(state: dict, key: str, kind: str) -> None:
    _bucket(state, key).setdefault("alerts", {})[kind] = True


def clear_alert_flag(state: dict, key: str, kind: str) -> None:
    """源恢复成功后解除报警武装，下次故障可再次报警。"""
    b = state.get("sources", {}).get(key)
    if b and b.get("alerts", {}).get(kind):
        b["alerts"][kind] = False


def hour_key(ts: float) -> str:
    """自然小时键（UTC 整点边界与东九/东八区一致，均为整小时）。"""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H")


def is_new_hour(state: dict, now_ts: float) -> bool:
    """本 tick 是否为当前自然小时的第一次 tick（全局 sweep 信号）。

    sweep 语义：每小时第一次 tick，**所有源**（含 10 分钟源）都强制访问一次；
    小时内其余 tick 按各自 tick 节奏。整点边界对 UTC/东九/东八完全一致。
    """
    return state.get("last_sweep_hour") != hour_key(now_ts)


def set_sweep_hour(state: dict, now_ts: float) -> None:
    """sweep 发生时记录小时键（必须在发起任何 HTTP 请求前调用）。"""
    state["last_sweep_hour"] = hour_key(now_ts)
