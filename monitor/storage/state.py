"""运行状态：per-source 的 seen/notified/baselined，JSON 持久化。

- seen：抓到即登记（防重复插入）
- notified：邮件投递成功后才登记（投递至少一次、通知至多一次）
- baselined：首次运行建立基线，历史内容不通知
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
    """
    b = state.get("sources", {}).get(key)
    if not b:
        return None
    ts = b.get("last_checked_at")
    return float(ts) if isinstance(ts, (int, float)) else None


def set_last_checked_at(state: dict, key: str, ts: float) -> None:
    """仅在实际发起 HTTP 请求后调用；跳过时不得调用。"""
    _bucket(state, key)["last_checked_at"] = float(ts)


def is_due(state: dict, key: str, interval_minutes: int, now: float) -> bool:
    """从未检查过 → True；否则 now - last_checked_at >= interval 才到期。"""
    last = get_last_checked_at(state, key)
    if last is None:
        return True
    return (now - last) >= interval_minutes * 60
