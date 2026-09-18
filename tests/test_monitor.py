"""monitor 单元测试：全部 mock，不联网。"""
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from monitor.models import SourceError, Update
from monitor.storage import state as st
from monitor.sources import websites


def _u(ext: str) -> Update:
    return Update(source_key="t", platform="x", account_name="a",
                  content_type="tweet", external_id=ext, title=ext)


def _state(tmp_path):
    return st.load_state(tmp_path / "s.json")


def test_baseline_no_notify(tmp_path):
    state = _state(tmp_path)
    new, baseline = st.diff_new(state, "t", [_u("1"), _u("2")])
    assert baseline and new == []
    new, baseline = st.diff_new(state, "t", [_u("1"), _u("2")])
    assert not baseline and new == []


def test_new_then_duplicate(tmp_path):
    state = _state(tmp_path)
    st.diff_new(state, "t", [_u("1")])
    st.mark_notified(state, "t", [_u("1")])
    new, _ = st.diff_new(state, "t", [_u("1"), _u("9")])
    assert [u.external_id for u in new] == ["9"]


def test_seen_cap(tmp_path):
    state = _state(tmp_path)
    st.diff_new(state, "t", [_u(str(i)) for i in range(3000)])
    assert len(st._bucket(state, "t")["seen"]) == st.SEEN_CAP


def test_per_source_isolation(tmp_path):
    state = _state(tmp_path)
    st.diff_new(state, "a", [_u("x")])
    new, baseline = st.diff_new(state, "b", [_u("y")])
    assert baseline and new == []          # b 的基线独立于 a
    new, _ = st.diff_new(state, "a", [_u("z")])
    assert [u.external_id for u in new] == ["z"]


# ---------------- 官网解析器（按真实 HTML 结构的夹具） ----------------

MIYAMOTO_HTML = """
<div class="top-news-article">
 <div class="top-news-article-alpha">
  <p class="top-news-article-alpha__date">2026.09.12</p>
  <p class="top-news-article-category"><span>RELEASE</span></p>
 </div>
 <div class="top-news-article-beta">
  <p class="top-news-article-beta__txt">「零地点Bomb」のミュージックビデオ公開
   <br><a href="https://youtu.be/wYlTWePwgBU">こちら</a></p>
 </div>
</div>"""


def test_parse_miyamoto(tmp_path):
    from bs4 import BeautifulSoup
    src = type("S", (), {"key": "site_miyamoto", "label": "artist-site",
                         "url": "https://example.com/news/"})()
    ups = websites._parse_miyamoto(BeautifulSoup(MIYAMOTO_HTML, "html.parser"), src)
    assert len(ups) == 1
    u = ups[0]
    assert u.title == "「零地点Bomb」のミュージックビデオ公開"
    assert u.url == "https://youtu.be/wYlTWePwgBU"
    assert u.published_at.year == 2026 and u.published_at.month == 9
    ext1 = u.external_id
    ext2 = websites._parse_miyamoto(
        BeautifulSoup(MIYAMOTO_HTML, "html.parser"), src)[0].external_id
    assert ext1 == ext2  # 稳定 ID


EK_HTML = """
<li class="topics">
 <div class="topicDate"><span class="cat">NEWS</span><p>2025.12.21</p></div>
 <div class="newsBody">
  <p class="infoDetail">2026年カレンダー販売開始！</p>
  <div class="newsTxt">詳細はこちら</div>
 </div>
</li>"""


def test_parse_ek(tmp_path):
    from bs4 import BeautifulSoup
    src = type("S", (), {"key": "site_ek", "label": "ek",
                         "url": "https://www.elephantkashimashi.com/news/"})()
    ups = websites._parse_ek(BeautifulSoup(EK_HTML, "html.parser"), src)
    assert len(ups) == 1
    assert ups[0].title == "2026年カレンダー販売開始！"
    assert ups[0].published_at.month == 12


FC_HTML = """
<div class="news_index_box">
 <a name="newsID405"></a>
 <p class="news_day_s">2024.12.08</p>
 <p class="news_title_s">発送物のお知らせ</p>
</div>"""


def test_parse_fc(tmp_path):
    from bs4 import BeautifulSoup
    src = type("S", (), {"key": "site_ekfc", "label": "fc",
                         "url": "https://www.elephantkashimashi.com/fc/free/news/index.php?kd=NEWS"})()
    ups = websites._parse_fc(BeautifulSoup(FC_HTML, "html.parser"), src)
    assert len(ups) == 1
    assert ups[0].external_id == "405"
    assert ups[0].url.endswith("#newsID405")


def test_hashpage_changes_on_content():
    from bs4 import BeautifulSoup
    src = type("S", (), {"key": "site_elephantsinc", "label": "inc",
                         "url": "https://elephants-inc.com/news1007/"})()
    u1 = websites._parse_hashpage(BeautifulSoup("<html><body>お詫び A</body></html>", "html.parser"), src)
    u2 = websites._parse_hashpage(BeautifulSoup("<html><body>お詫び A</body></html>", "html.parser"), src)
    u3 = websites._parse_hashpage(BeautifulSoup("<html><body>お詫び B</body></html>", "html.parser"), src)
    assert u1[0].external_id == u2[0].external_id
    assert u1[0].external_id != u3[0].external_id  # 内容变化 → 新 ID → 通知


# ---------------- 频率门控（按轮次 tick，不受 Actions 调度延迟漂移影响） ----------------

def _fake_src(key, updates=None, fail=False, err="boom"):
    """fail=True 时抛 SourceError(err)；err 用于模拟限流文本（触发更长冷却）。"""
    from types import SimpleNamespace
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if fail:
            raise SourceError(err)
        return list(updates or [])

    return SimpleNamespace(key=key, check=check), calls


def _no_save(monkeypatch):
    from monitor.storage import state as st_mod
    monkeypatch.setattr(st_mod, "save_state", lambda path, state: None)


def _tick_state(last=None) -> dict:
    """构造 tick=100 的 state；last 为 {key: last_tick} 预置上次检查轮次。

    预设 last_sweep_hour = 真实当前小时：让不传 now 的旧测试处于
    "本小时 sweep 已发生"状态（hour_first=False），按原节奏断言。
    """
    state: dict = {"sources": {}, "tick": 100,
                   "last_sweep_hour": st.hour_key(time.time())}
    for k, t in (last or {}).items():
        st.set_last_checked_tick(state, k, t)
    return state


def test_first_run_always_checks(monkeypatch):
    from monitor import main as m
    _no_save(monkeypatch)
    state = {"sources": {}, "tick": 1}  # 从未检查过，无 last_checked_tick
    src, calls = _fake_src("site_miyamoto")
    summary, ok, fail = m.run_once([src], state, {}, set(), now=1_000_000.0, tick=1)
    assert calls["n"] == 1
    assert ok == 1 and fail == 0
    assert "在场" in summary[0][1]
    assert state["sources"]["site_miyamoto"]["last_checked_tick"] == 1


def test_every_round_source_checks_each_tick(monkeypatch):
    """5 分钟源（每轮查）：连续两轮都查。"""
    from monitor import main as m
    _no_save(monkeypatch)
    state = _tick_state({"x": 100})
    src, calls = _fake_src("x")
    m.run_once([src], state, {}, set(), tick=101)
    m.run_once([src], state, {}, set(), tick=102)
    assert calls["n"] == 2


def test_two_tick_source_skips_alternate_rounds(monkeypatch):
    """10 分钟源（隔 1 轮查一次）：跳过 → 查 → 跳过。"""
    from monitor import main as m
    _no_save(monkeypatch)
    state = _tick_state({"youtube_UCcUcK64JLSZAPUfG07s-Wew": 100})
    src, calls = _fake_src("youtube_UCcUcK64JLSZAPUfG07s-Wew")
    s1, ok1, _ = m.run_once([src], state, {}, set(), tick=101)  # 间隔 1 < 2 跳过
    assert calls["n"] == 0 and ok1 == 0 and "跳过" in s1[0][1]
    s2, ok2, _ = m.run_once([src], state, {}, set(), tick=102)  # 间隔 2 到期
    assert calls["n"] == 1 and ok2 == 1 and "跳过" not in s2[0][1]
    s3, ok3, _ = m.run_once([src], state, {}, set(), tick=103)  # 刚查过，跳过
    assert calls["n"] == 1 and ok3 == 0 and "跳过" in s3[0][1]


def test_twelve_tick_source_checks_every_12_rounds(monkeypatch):
    """60 分钟源（每 12 轮查一次）：11 轮跳过，第 12 轮查。"""
    from monitor import config, main as m
    _no_save(monkeypatch)
    monkeypatch.setattr(config, "HOUR_ALIGNED_SOURCES", set())  # 本测试验证 tick 路径（整点对齐另行覆盖）
    state = _tick_state({"site_miyamoto": 100})
    src, calls = _fake_src("site_miyamoto")
    for t in range(101, 112):
        summary, ok, fail = m.run_once([src], state, {}, set(), tick=t)
        assert calls["n"] == 0  # 未发 HTTP 请求
        assert ok == 0 and fail == 0  # 跳过不计入成功/失败
        assert "跳过" in summary[0][1]
    # 11 轮跳过中 last_checked_tick 保持不变
    assert state["sources"]["site_miyamoto"]["last_checked_tick"] == 100
    summary, ok, _ = m.run_once([src], state, {}, set(), tick=112)
    assert calls["n"] == 1 and ok == 1 and "跳过" not in summary[0][1]


def test_different_sources_have_different_intervals(monkeypatch):
    from monitor import config, main as m
    _no_save(monkeypatch)
    monkeypatch.setattr(config, "HOUR_ALIGNED_SOURCES", set())  # 本测试验证 tick 路径（整点对齐另行覆盖）
    assert config.check_interval_ticks("ig_story") == 1
    assert config.check_interval_ticks("x") == 1
    assert config.check_interval_ticks("youtube_UCcUcK64JLSZAPUfG07s-Wew") == 2
    assert config.check_interval_ticks("tiktok_miyamoto_hiroji_") == 2
    assert config.check_interval_ticks("site_miyamoto") == 12
    assert config.check_interval_ticks("site_ek") == 12
    assert config.check_interval_ticks("site_ekfc") == 12
    assert config.check_interval_ticks("site_elephantsinc") == 12
    assert config.check_interval_minutes("site_miyamoto") == 60  # 分钟表保留展示用
    # 同一轮：1 轮源查、12 轮源跳过
    state = _tick_state({"x": 100, "site_miyamoto": 100})
    sx, cx = _fake_src("x")
    ss, cs = _fake_src("site_miyamoto")
    summary, ok, _ = m.run_once([sx, ss], state, {}, set(), tick=101)
    assert cx["n"] == 1 and cs["n"] == 0
    assert ok == 1
    assert "跳过" not in summary[0][1] and "跳过" in summary[1][1]


def test_failed_check_still_advances_tick_and_streak(monkeypatch):
    """失败也算发起过 HTTP：更新 last_checked_tick + fail_streak，避免每轮重试突破频率。"""
    from monitor import main as m
    _no_save(monkeypatch)
    state = {"sources": {}, "tick": 1}
    src, calls = _fake_src("x", fail=True)
    summary, ok, fail = m.run_once([src], state, {}, set(), now=1_000_000.0, tick=1)
    assert calls["n"] == 1 and ok == 0 and fail == 1
    assert "失败" in summary[0][1]
    assert state["sources"]["x"]["last_checked_tick"] == 1
    assert st.get_fail_streak(state, "x") == 1


def test_old_state_without_tick_fields_is_compatible_and_due():
    state = {"sources": {"x": {"seen": ["1"], "notified": [], "baselined": True}}}
    assert st.get_tick(state) == 0
    assert st.get_fail_streak(state, "x") == 0
    assert st.is_due_tick(state, "x", 1, 1) is True  # 每轮源直接查
    assert st.is_due_tick(state, "x", 12, 999) is True  # 缺 last_checked_tick 首次必须查
    assert st.get_last_checked_at(state, "x") is None  # 旧墙钟字段同样兼容


def test_fail_streak_accumulates_across_rounds_and_alerts_once(monkeypatch):
    """跨轮累计：持久化 fail_streak 逐轮 +1，第 6 次触发一次报警，成功清零。"""
    from monitor import config, main as m
    _no_save(monkeypatch)
    alerts: list = []
    monkeypatch.setattr(m.email, "send_alert",
                        lambda subject, body: alerts.append((subject, body)) or True)
    state: dict = {"sources": {}, "tick": 0}
    # 现行实现下 x 未启用冷却：每轮都实际发起检查 → 80 轮 = 80 次检查。
    # （“失败后跳过若干轮”的冷却语义只作用于 config.COOLDOWN_SOURCES，见
    #   ig_post 冷却专项测试。）
    checks = 0
    for tick in range(1, 81):
        src, calls = _fake_src("x", fail=True)
        m.run_once([src], state, {}, set(), tick=tick)
        if calls["n"]:
            checks += 1
            assert st.get_fail_streak(state, "x") == checks
        if checks == config.ALERT_THRESHOLD:
            pass
    assert checks == 80         # 无冷却的源不会被跳过：每轮都查
    assert len(alerts) == 1     # 全程只报一次
    assert f"连续 {config.ALERT_THRESHOLD} 次" in alerts[0][0]
    src_ok, _ = _fake_src("x")  # 恢复轮：成功清零
    for tick in range(81, 100):
        src_ok, c2 = _fake_src("x")
        m.run_once([src_ok], state, {}, set(), tick=tick)
        if c2["n"]:
            break
    assert st.get_fail_streak(state, "x") == 0
    src, _ = _fake_src("x", fail=True)
    summary, _, _ = m.run_once([src], state, {}, set(), tick=9)
    assert "失败 x1" in summary[0][1]


def test_skip_round_does_not_touch_fail_streak(monkeypatch):
    """跳过轮不计入连续失败：不发请求、不动 streak。"""
    from monitor import config, main as m
    _no_save(monkeypatch)
    monkeypatch.setattr(config, "HOUR_ALIGNED_SOURCES", set())  # 本测试验证 tick 路径（整点对齐另行覆盖）
    state = _tick_state({"site_miyamoto": 100})
    st.set_fail_streak(state, "site_miyamoto", 3)
    src, calls = _fake_src("site_miyamoto")
    m.run_once([src], state, {}, set(), tick=101)  # 未到轮次
    assert calls["n"] == 0
    assert st.get_fail_streak(state, "site_miyamoto") == 3


def test_secondary_x_accounts_registered_every_12_rounds():
    from monitor import config, main as m
    from monitor.sources import x_twitter
    assert config.X_USERS == ["miyamoto_hiroji", "paonews_info",
                              "hmnews_info", "elekashi_ofcl"]
    keys = [s.key for s in m.build_sources() if s.key == "x" or s.key.startswith("x_")]
    assert keys == ["x", "x_paonews_info", "x_hmnews_info", "x_elekashi_ofcl"]
    assert config.check_interval_ticks("x") == 1  # 主号每轮，不受影响
    for k in ("x_paonews_info", "x_hmnews_info", "x_elekashi_ofcl"):
        assert config.check_interval_ticks(k) == 12
        assert config.check_interval_minutes(k) == 60
    # 次要号 source_key 独立（去重作用域隔离）；主号旧 key 不变
    assert x_twitter.source().key == "x"
    assert x_twitter.source("paonews_info").key == "x_paonews_info"
    # 次要号候选 URL 指向自己的 handle
    urls = [u for u, _, _, _ in x_twitter._candidates("paonews_info")]
    assert urls and all("/paonews_info/rss" in u for u in urls)
    assert "miyamoto_hiroji" not in " ".join(urls)


def test_hour_sweep_forces_all_sources(monkeypatch):
    """自然小时第一次 tick：所有源（含 10 分钟源）强制访问一次；小时内按各自节奏。"""
    from monitor import main as m
    _no_save(monkeypatch)
    now = 1757802000.0
    state = _tick_state({})
    state["last_sweep_hour"] = st.hour_key(now - 3600)  # 置于上一小时 → 本 tick 触发 sweep
    s_align, c_align = _fake_src("site_miyamoto")
    s_10m, c_10m = _fake_src("youtube_x")
    s_1m, c_1m = _fake_src("x")
    m.run_once([s_align, s_10m, s_1m], state, {}, set(), tick=101, now=now)
    assert (c_align["n"], c_10m["n"], c_1m["n"]) == (1, 1, 1)      # sweep 全查
    m.run_once([s_align, s_10m, s_1m], state, {}, set(), tick=102, now=now + 300)
    assert (c_align["n"], c_10m["n"], c_1m["n"]) == (1, 1, 2)      # 小时内：整点源停、10 分钟源隔轮、每轮源照跑
    m.run_once([s_align, s_10m, s_1m], state, {}, set(), tick=103, now=now + 3700)
    assert (c_align["n"], c_10m["n"], c_1m["n"]) == (2, 2, 3)      # 新小时 sweep：再次全查


def test_hour_aligned_source_runs_once_per_hour(monkeypatch):
    """整点对齐源：每自然小时第一次 tick 执行，小时内后续 tick 跳过。"""
    from monitor import main as m
    _no_save(monkeypatch)
    now = 1757802000.0
    state = _tick_state({})
    src, calls = _fake_src("site_miyamoto")
    m.run_once([src], state, {}, set(), tick=101, now=now)
    assert calls["n"] == 1
    m.run_once([src], state, {}, set(), tick=102, now=now + 300)   # 同一小时 → 跳过
    assert calls["n"] == 1
    m.run_once([src], state, {}, set(), tick=103, now=now + 3700)  # 下一自然小时 → 再查
    assert calls["n"] == 2


def test_checkpoint_alert_fires_once_per_incident(monkeypatch):
    """checkpoint 报警按事件去重：连续异常只发一次，恢复成功后重新武装。"""
    from monitor import main as m
    _no_save(monkeypatch)
    sent = []
    monkeypatch.setattr(m.email, "send_alert", lambda s, b: sent.append(s) or True)
    state = _tick_state({})

    class BadSrc:
        key = "ig_story"
        platform = "instagram"

        @staticmethod
        def check():
            raise m.CheckpointError("需要验证")

    m.run_once([BadSrc()], state, {}, set(), tick=1)
    m.run_once([BadSrc()], state, {}, set(), tick=2)
    assert len(sent) == 1  # 同一事件只报一次

    class GoodSrc:
        key = "ig_story"
        platform = "instagram"

        @staticmethod
        def check():
            return []

    m.run_once([GoodSrc()], state, {}, set(), tick=3)  # 恢复 → 解除武装
    state["sources"]["ig_story"]["alerts"]["checkpoint"] = False

    m.run_once([BadSrc()], state, {}, set(), tick=4)   # 再次故障 → 再报
    assert len(sent) == 2


ELEPHANTSINC_HOME_HTML = """
<html><body>
 <a href="./news202609/">最新公告</a>
 <a href="./news1007/">旧公告</a>
 <a href="https://google.com/">外部链接</a>
</body></html>"""


def test_parse_elephantsinc_home(tmp_path):
    from bs4 import BeautifulSoup
    src = type("S", (), {"key": "site_elephantsinc", "label": "mgmt",
                         "url": "https://elephants-inc.com/"})()
    ups = websites._parse_elephantsinc_home(
        BeautifulSoup(ELEPHANTSINC_HOME_HTML, "html.parser"), src)
    # 两个 news 链接都被提取（google 链接被过滤）
    slugs = {u.external_id for u in ups}
    assert slugs == {"news202609", "news1007"}
    assert all(u.url.startswith("https://elephants-inc.com/news") for u in ups)
    # 新 slug 出现 = 新 Update
    ups2 = websites._parse_elephantsinc_home(
        BeautifulSoup(ELEPHANTSINC_HOME_HTML.replace("news202609", "news202610"), "html.parser"), src)
    assert {u.external_id for u in ups2} == {"news202610", "news1007"}


# ---------------- 失败冷却：仅 ig_post（限流时跳过本源，其他源不受影响） ----------------

_RATE_LIMIT_ERR = "PleaseWaitFewMinutes: Please wait a few minutes before you try again."
_CD_NOW = 1757802000.0  # 固定墙钟：让 sweep/整点判定可复现（同一小时内）


def test_cooldown_config_only_covers_ig_post():
    """冷却只对 ig_post 生效：其他源 cooldown_ticks() 恒为 0（行为与改动前一致）。"""
    from monitor import config
    assert config.COOLDOWN_SOURCES == {"ig_post"}
    # 频率与 YouTube 完全一致（10 分钟 / 隔 1 轮），与冷却机制相互独立
    assert config.check_interval_ticks("ig_post") == 2
    assert config.check_interval_minutes("ig_post") == 10
    assert config.is_hour_aligned("ig_post") is False  # 与 youtube 一样不参与整点对齐
    assert config.cooldown_ticks("ig_post", "SourceError: boom") == config.COOLDOWN_TICKS
    assert config.cooldown_ticks("ig_post", _RATE_LIMIT_ERR) == config.RATE_LIMIT_COOLDOWN_TICKS
    for key in ("x", "ig_story", "youtube_UCcUcK64JLSZAPUfG07s-Wew", "site_miyamoto"):
        assert config.cooldown_ticks(key, _RATE_LIMIT_ERR) == 0
    assert config.is_rate_limited("HTTP 429 too many requests")
    assert config.is_rate_limited("Feedback_Required: 账号行为标记")
    assert not config.is_rate_limited("SourceError: boom")


def test_cooldown_state_helpers_and_old_state_compat(tmp_path):
    state = _state(tmp_path)
    assert st.get_cooldown_until_tick(state, "ig_post") == 0       # 全新 state
    assert st.cooldown_remaining_ticks(state, "ig_post", 5) == 0   # 未冷却
    st.set_last_checked_tick(state, "ig_post", 1)                  # 旧 bucket（无冷却字段）
    assert st.get_cooldown_until_tick(state, "ig_post") == 0
    st.set_cooldown_until_tick(state, "ig_post", 20)               # 跳过至第 20 轮（含）
    assert st.cooldown_remaining_ticks(state, "ig_post", 15) == 6
    assert st.cooldown_remaining_ticks(state, "ig_post", 21) == 0  # 截止后恢复
    st.clear_cooldown(state, "ig_post")
    assert st.get_cooldown_until_tick(state, "ig_post") == 0


def test_success_writes_no_cooldown_field(monkeypatch):
    """成功的源不会被写入 cooldown 字段（对其他源零侵入）。"""
    from monitor import main as m
    _no_save(monkeypatch)
    state = _tick_state({})
    x, x_calls = _fake_src("x")
    ig, ig_calls = _fake_src("ig_post")
    m.run_once([x, ig], state, {}, set(), tick=101, now=_CD_NOW)
    assert x_calls["n"] == 1 and ig_calls["n"] == 1
    assert "cooldown_until_tick" not in state["sources"]["x"]
    assert "cooldown_until_tick" not in state["sources"]["ig_post"]


def test_rate_limited_ig_post_cools_down_while_others_keep_running(monkeypatch):
    """ig_post 触发 IG 限流 → 本源跳过 RATE_LIMIT_COOLDOWN_TICKS 轮；其他源每轮照查。"""
    from monitor import config, main as m
    _no_save(monkeypatch)
    monkeypatch.setattr(m.email, "send_alert", lambda s, b: True)
    state = _tick_state({"x": 100})
    ig, ig_calls = _fake_src("ig_post", fail=True, err=_RATE_LIMIT_ERR)
    x, x_calls = _fake_src("x")
    n = config.RATE_LIMIT_COOLDOWN_TICKS

    summary, ok, fail = m.run_once([ig, x], state, {}, set(), tick=101, now=_CD_NOW)
    assert ig_calls["n"] == 1 and ok == 1 and fail == 1
    note = dict(summary)["ig_post"]
    assert "限流" in note and f"冷却 {n} 轮" in note

    for tick in range(102, 102 + n):  # 冷却期内：ig_post 零请求，且不计成功/失败
        summary, ok, fail = m.run_once([ig, x], state, {}, set(), tick=tick, now=_CD_NOW)
        assert dict(summary)["ig_post"].startswith("冷却中")
        assert ok == 1 and fail == 0
    assert ig_calls["n"] == 1
    assert x_calls["n"] == 1 + n  # 其他源完全不受影响：每轮照查

    m.run_once([ig, x], state, {}, set(), tick=102 + n, now=_CD_NOW)  # 冷却结束 → 恢复检查
    assert ig_calls["n"] == 2


def test_ordinary_ig_post_failure_uses_shorter_cooldown(monkeypatch):
    """非限流的普通失败：冷却更短，且不产生限流报警邮件。"""
    from monitor import config, main as m
    _no_save(monkeypatch)
    alerts: list = []
    monkeypatch.setattr(m.email, "send_alert", lambda s, b: alerts.append(s) or True)
    state = _tick_state({})
    ig, ig_calls = _fake_src("ig_post", fail=True, err="SessionExpired: login required")

    summary, _, _ = m.run_once([ig], state, {}, set(), tick=101, now=_CD_NOW)
    assert st.get_cooldown_until_tick(state, "ig_post") == 101 + config.COOLDOWN_TICKS
    note = dict(summary)["ig_post"]
    assert "冷却" in note and "限流" not in note
    assert alerts == []  # 未达 ALERT_THRESHOLD 且非限流事件 → 不报警

    m.run_once([ig], state, {}, set(), tick=101 + config.COOLDOWN_TICKS + 1, now=_CD_NOW)
    assert ig_calls["n"] == 2  # 冷却一结束就恢复检查


def test_cooldown_cleared_on_success_and_rate_limit_alert_rearmed(monkeypatch):
    """恢复成功 → 解除冷却 + 重新武装限流报警（每个限流事件最多一封邮件）。"""
    from monitor import config, main as m
    _no_save(monkeypatch)
    alerts: list = []
    monkeypatch.setattr(m.email, "send_alert", lambda s, b: alerts.append(s) or True)
    state = _tick_state({})
    n = config.RATE_LIMIT_COOLDOWN_TICKS
    bad, _ = _fake_src("ig_post", fail=True, err=_RATE_LIMIT_ERR)
    good, good_calls = _fake_src("ig_post")

    m.run_once([bad], state, {}, set(), tick=101, now=_CD_NOW)   # 限流事件 1 → 报警
    assert len(alerts) == 1 and "限流" in alerts[0]
    m.run_once([bad], state, {}, set(), tick=102, now=_CD_NOW)   # 冷却期内：不再报警
    assert len(alerts) == 1
    assert st.cooldown_remaining_ticks(state, "ig_post", 102) == n

    m.run_once([good], state, {}, set(), tick=102 + n, now=_CD_NOW)  # 冷却结束 → 恢复成功
    assert good_calls["n"] == 1
    assert st.get_cooldown_until_tick(state, "ig_post") == 0
    assert st.get_fail_streak(state, "ig_post") == 0

    step = config.check_interval_ticks("ig_post")  # 恢复轮之后需再满一个间隔才到期
    m.run_once([bad], state, {}, set(), tick=102 + n + step, now=_CD_NOW)  # 限流事件 2 → 再报警
    assert len(alerts) == 2 and "限流" in alerts[1]


def test_hour_sweep_does_not_bypass_cooldown(monkeypatch):
    """整点 sweep 会强制所有源访问，但冷却中的源仍必须零请求（防封优先）。"""
    from monitor import main as m
    _no_save(monkeypatch)
    monkeypatch.setattr(m.email, "send_alert", lambda s, b: True)
    state = _tick_state({})
    ig, ig_calls = _fake_src("ig_post", fail=True, err=_RATE_LIMIT_ERR)

    m.run_once([ig], state, {}, set(), tick=101, now=_CD_NOW)  # 进入冷却
    assert ig_calls["n"] == 1
    summary, ok, fail = m.run_once([ig], state, {}, set(), tick=102, now=_CD_NOW + 3600)
    assert ig_calls["n"] == 1                    # sweep 轮同样不发请求
    assert "冷却中" in dict(summary)["ig_post"]
    assert ok == 0 and fail == 0                 # 冷却跳过不计入成功/失败


def test_yt_rss_ok_does_not_call_api(monkeypatch):
    """RSS 正常 → 绝不调用 Data API（Key 配了也不打，只走 RSS 主通道）。"""
    import requests as req
    from monitor.sources import youtube
    from monitor.sources import youtube_api
    calls = {"api": 0}

    class Resp:
        status_code = 200
        content = b"<feed/>"

    monkeypatch.setattr(req, "get", lambda *a, **k: Resp())
    monkeypatch.setattr(
        youtube.feedparser, "parse",
        lambda content: SimpleNamespace(
            bozo=False, bozo_exception=None,
            entries=[{"yt_videoid": "vid1", "title": "t1",
                      "published": "Mon, 01 Sep 2026 00:00:00 +0000"}]))
    monkeypatch.setattr(
        youtube_api, "fetch_uploads",
        lambda cid: calls.__setitem__("api", calls["api"] + 1) or [])
    ups = youtube.check_channel("artist", "UCcUcK64JLSZAPUfG07s-Wew")
    assert calls["api"] == 0
    assert [u.external_id for u in ups] == ["vid1"]


def test_yt_rss_500_falls_back_to_api(monkeypatch):
    """RSS 500 → fallback 打一次 Data API；POC 只打印、不转 Update（返回 []）。"""
    import requests as req
    from monitor import config
    from monitor.sources import youtube
    from monitor.sources import youtube_api
    calls = {"api": 0}

    class Resp:
        status_code = 500
        text = "boom"

    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(req, "get", lambda *a, **k: Resp())

    def fake_fetch(cid):
        calls["api"] += 1
        assert cid == "UCcUcK64JLSZAPUfG07s-Wew"
        return [{"videoId": "abc", "title": "t", "publishedAt": "2026-09-01T00:00:00Z"}]

    monkeypatch.setattr(youtube_api, "fetch_uploads", fake_fetch)
    assert youtube.check_channel("artist", "UCcUcK64JLSZAPUfG07s-Wew") == []
    assert calls["api"] == 1


def test_yt_rss_timeout_without_key_keeps_old_behavior(monkeypatch):
    """RSS 超时 + 未配 Key → 直接报 RSS 原始错误（与改动前一致，不碰 API）。"""
    import requests as req
    from monitor import config
    from monitor.models import SourceError
    from monitor.sources import youtube
    from monitor.sources import youtube_api
    calls = {"api": 0}
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr(youtube_api, "fetch_uploads",
                        lambda cid: calls.__setitem__("api", 1) or [])

    def boom(*a, **k):
        raise req.Timeout("timed out")

    monkeypatch.setattr(req, "get", boom)
    with pytest.raises(SourceError, match="RSS 请求失败"):
        youtube.check_channel("artist", "UCcUcK64JLSZAPUfG07s-Wew")
    assert calls["api"] == 0


def test_yt_api_uses_uploads_playlist_not_search(monkeypatch, capsys):
    """Data API 走 uploads playlist 路线：channels.list → playlistItems.list（无 search）。"""
    import requests as req
    from monitor import config
    from monitor.sources import youtube_api
    seen_urls: list = []

    class Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._p = payload

        def json(self):
            return self._p

    def fake_get(url, params=None, **k):
        seen_urls.append(url)
        if url.endswith("/channels"):
            assert params["id"] == "UCcUcK64JLSZAPUfG07s-Wew"
            assert params["part"] == "contentDetails"
            return Resp({"items": [{"contentDetails": {"relatedPlaylists":
                                                        {"uploads": "UUxxx"}}}]})
        assert url.endswith("/playlistItems")
        assert params["playlistId"] == "UUxxx"
        assert params["maxResults"] == 5
        return Resp({"items": [
            {"snippet": {"resourceId": {"videoId": "v1"}, "title": "Hello",
                         "publishedAt": "2026-09-10T01:02:03Z"}},
            {"snippet": {"resourceId": {"videoId": "v2"}, "title": "World",
                         "publishedAt": "2026-09-09T01:02:03Z"}}]})

    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(req, "get", fake_get)
    items = youtube_api.fetch_uploads("UCcUcK64JLSZAPUfG07s-Wew")
    assert [i["videoId"] for i in items] == ["v1", "v2"]
    assert all("search" not in u for u in seen_urls)
    out = capsys.readouterr().out  # POC 打印 videoId/title/publishedAt
    assert "v1" in out and "Hello" in out and "2026-09-10T01:02:03Z" in out


def test_yt_rss_403_does_not_fallback(monkeypatch):
    """RSS 403（非 fallback 条件）→ 直接报错，不打 API（避免把配额浪费在权限问题上）。"""
    import requests as req
    from monitor import config
    from monitor.models import SourceError
    from monitor.sources import youtube
    from monitor.sources import youtube_api
    calls = {"api": 0}
    monkeypatch.setattr(config, "YOUTUBE_API_KEY", "test-key")
    monkeypatch.setattr(youtube_api, "fetch_uploads",
                        lambda cid: calls.__setitem__("api", 1) or [])

    class Resp:
        status_code = 403
        text = "forbidden"

    monkeypatch.setattr(req, "get", lambda *a, **k: Resp())
    with pytest.raises(SourceError, match="HTTP 403"):
        youtube.check_channel("artist", "UCcUcK64JLSZAPUfG07s-Wew")
    assert calls["api"] == 0


def test_ig_post_follows_same_cadence_as_youtube(monkeypatch):
    """ig_post 与 youtube 同为 10 分钟源：隔 1 轮才查一次（sweep 仍会强制）。"""
    from monitor import config, main as m
    _no_save(monkeypatch)
    monkeypatch.setattr(config, "HOUR_ALIGNED_SOURCES", set())  # 只验证 tick 节奏
    state = _tick_state({})
    st.set_last_checked_tick(state, "ig_post", 100)
    ig, calls = _fake_src("ig_post")

    s1, ok1, _ = m.run_once([ig], state, {}, set(), tick=101)  # 间隔 1 < 2 → 跳过
    assert calls["n"] == 0 and ok1 == 0 and "跳过" in s1[0][1]
    s2, ok2, _ = m.run_once([ig], state, {}, set(), tick=102)  # 间隔 2 → 检查
    assert calls["n"] == 1 and ok2 == 1
    assert config.check_interval_ticks("ig_post") == \
        config.check_interval_ticks("youtube_UCcUcK64JLSZAPUfG07s-Wew")
