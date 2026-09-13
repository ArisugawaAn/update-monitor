"""官网源 ×4：公开 HTML 解析，无账号。

- miyamotohiroji.com/news/     → div.top-news-article（日期+分类+正文，无独立 URL → 哈希 ID）
- elephantkashimashi.com/news/ → li.topics（日期+分类+标题+正文，无独立 URL → 哈希 ID）
- elephantkashimashi.com/fc/   → div.news_index_box（newsID 锚点 = 稳定 ID）
- elephants-inc.com/news1007/  → 静态公告页，无列表 → 整页内容哈希监测变更
"""
import hashlib
import re
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

from monitor import config
from monitor.models import SourceError, Update

JST = timezone(timedelta(hours=9))


def _get_soup(url: str) -> BeautifulSoup:
    try:
        r = requests.get(url, timeout=20, headers={"User-Agent": config.BROWSER_UA})
    except Exception as e:
        raise SourceError(f"请求异常: {type(e).__name__}: {e}") from e
    if r.status_code != 200:
        raise SourceError(f"HTTP {r.status_code}")
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "html.parser")


def _hid(*parts: str) -> str:
    return hashlib.sha1("|".join(p or "" for p in parts).encode("utf-8")).hexdigest()[:16]


def _parse_jst(s: str | None) -> datetime | None:
    m = re.search(r"(20\d{2})\.(\d{1,2})\.(\d{1,2})", s or "")
    if not m:
        return None
    return datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=JST)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            return line
    return ""


def _parse_miyamoto(soup: BeautifulSoup, src) -> list[Update]:
    out = []
    for a in soup.select("div.top-news-article"):
        date_el = a.select_one(".top-news-article-alpha__date")
        cat_el = a.select_one(".top-news-article-category span")
        txt_el = a.select_one(".top-news-article-beta__txt")
        if not (date_el and txt_el):
            continue
        date_s = date_el.get_text(strip=True)
        cat = (cat_el.get_text(strip=True) if cat_el else "") or "NEWS"
        full = txt_el.get_text("\n", strip=True)
        title = _first_line(full)
        ext = _hid(date_s, cat, title)
        link = ""
        for tag in txt_el.select("a[href^=http]"):
            href = tag.get("href", "")
            if "miyamotohiroji.com" not in href:
                link = href
                break
        out.append(Update(
            source_key=src.key, platform="website", account_name=src.label,
            content_type="news", external_id=ext, title=title,
            text=full.replace("\n", " ")[:300], url=link or src.url,
            published_at=_parse_jst(date_s)))
    return out


def _parse_ek(soup: BeautifulSoup, src) -> list[Update]:
    out = []
    for li in soup.select("li.topics"):
        cat_el = li.select_one(".cat")
        date_el = li.select_one(".topicDate p")
        title_el = li.select_one(".infoDetail")
        if not (date_el and title_el):
            continue
        date_s = date_el.get_text(strip=True)
        cat = cat_el.get_text(strip=True) if cat_el else "NEWS"
        title = title_el.get_text(strip=True)
        body_el = li.select_one(".newsTxt")
        body = body_el.get_text(" ", strip=True)[:300] if body_el else ""
        detail = li.select_one('a[href*="detail.php"]')
        url = detail["href"] if detail else src.url
        if url.startswith("./"):
            url = "https://www.elephantkashimashi.com/news/" + url[2:]
        out.append(Update(
            source_key=src.key, platform="website", account_name=src.label,
            content_type="news", external_id=_hid(date_s, cat, title), title=title,
            text=body, url=url, published_at=_parse_jst(date_s)))
    return out


def _parse_fc(soup: BeautifulSoup, src) -> list[Update]:
    out = []
    for box in soup.select("div.news_index_box"):
        anchor = box.select_one("a[name^=newsID]")
        title_el = box.select_one(".news_title_s")
        if not (anchor and title_el):
            continue
        m = re.search(r"newsID(\d+)", anchor.get("name", ""))
        if not m:
            continue
        date_el = box.select_one(".news_day_s")
        out.append(Update(
            source_key=src.key, platform="website", account_name=src.label,
            content_type="news", external_id=m.group(1),
            title=title_el.get_text(strip=True),
            text=(box.get_text(" ", strip=True) or "")[:200],
            url=f"{src.url}#newsID{m.group(1)}",
            published_at=_parse_jst(date_el.get_text(strip=True) if date_el else None)))
    return out


def _parse_hashpage(soup: BeautifulSoup, src) -> list[Update]:
    """无列表结构的静态页：整页文本哈希 = 版本号，内容一变即新 Update。"""
    text = soup.get_text(" ", strip=True)
    if not text:
        raise SourceError("页面无文本内容")
    first = _first_line(text)[:80]
    return [Update(
        source_key=src.key, platform="website", account_name=src.label,
        content_type="page", external_id=_hid(text[:6000]),
        title=f"公告页面有更新：{first}", text=text[:300], url=src.url,
        published_at=None)]


SITES = [
    ("site_miyamoto", "artist site", "https://miyamotohiroji.com/news/", _parse_miyamoto),
    ("site_ek", "band site", "https://www.elephantkashimashi.com/news/", _parse_ek),
    ("site_ekfc", "band fc", "https://www.elephantkashimashi.com/fc/free/news/index.php?kd=NEWS", _parse_fc),
    ("site_elephantsinc", "mgmt site", "https://elephants-inc.com/news1007/", _parse_hashpage),
]


def source(key: str, label: str, url: str, parser):
    from types import SimpleNamespace

    def check() -> list[Update]:
        return parser(_get_soup(url), SimpleNamespace(key=key, label=label, url=url))

    return SimpleNamespace(key=key, platform="website", check=check)
