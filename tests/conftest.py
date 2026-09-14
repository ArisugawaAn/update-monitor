"""测试全局夹具：所有测试一律禁止真实写状态文件。

run_once / main 的状态写入目标是仓库根目录的 monitor_state.json（生产状态），
测试绝不能碰它。此处 autouse 关闭 save_state；个别测试需要验证保存行为时，
再自行 monkeypatch 一个捕获用假实现。
"""
import pytest

from monitor.storage import state as st_mod


@pytest.fixture(autouse=True)
def _no_real_state_writes(monkeypatch):
    monkeypatch.setattr(st_mod, "save_state", lambda path, state: None)
