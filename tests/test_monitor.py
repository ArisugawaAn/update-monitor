"""monitor 单元测试：全部 mock，不联网。"""
import json
import sys
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
