"""monitor 单元测试：全部 mock，不联网。"""
import json
import sys
import time
from pathlib import Path

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

def _fake_src(key, updates=None, fail=False):
    from types import SimpleNamespace
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if fail:
            raise SourceError("boom")
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
    for i in range(1, 7):
        src, calls = _fake_src("x", fail=True)
        summary, _, _ = m.run_once([src], state, {}, set(), tick=i)
        assert calls["n"] == 1
        assert f"失败 x{i}" in summary[0][1]  # 跨进程照样累计（state 驱动）
        assert st.get_fail_streak(state, "x") == i
    assert len(alerts) == 1  # 恰好阈值报一次
    assert f"连续 {config.ALERT_THRESHOLD} 次" in alerts[0][0]
    src, _ = _fake_src("x", fail=True)  # 第 7 次不再重复报警
    m.run_once([src], state, {}, set(), tick=7)
    assert len(alerts) == 1 and st.get_fail_streak(state, "x") == 7
    src_ok, _ = _fake_src("x")  # 成功清零，下一轮从头计
    m.run_once([src_ok], state, {}, set(), tick=8)
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


def test_failure_backoff_skips_rounds(monkeypatch):
    """失败后指数退避：n=2 失败 → 跳过若干轮才允许再试；成功后恢复节奏。"""
    from monitor import main as m
    _no_save(monkeypatch)
    state = _tick_state({})
    src, calls = _fake_src("ig_post", fail=True)

    # 第 1 次失败（tick=1）
    m.run_once([src], state, {}, set(), tick=1)
    assert calls["n"] == 1
    # 第 2 次失败（tick=2，退避允许立即再试一次）
    m.run_once([src], state, {}, set(), tick=2)
    assert calls["n"] == 2
    # 退避生效：last_checked_tick 被推到未来（2^1-1=1 轮 → tick=3）
    assert state["sources"]["ig_post"]["last_checked_tick"] >= 3
    # tick=3 未到退避轮 → 跳过
    m.run_once([src], state, {}, set(), tick=3)
    assert calls["n"] == 2
    # 成功后：fail_streak 清零，下一轮正常
    src2, calls2 = _fake_src("ig_post")
    m.run_once([src2], state, {}, set(), tick=10)
    assert calls2["n"] == 1 and st.get_fail_streak(state, "ig_post") == 0
