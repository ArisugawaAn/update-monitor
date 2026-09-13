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
