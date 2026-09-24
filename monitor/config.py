"""集中配置：账号注册表、镜像列表、阈值。密钥全部来自环境变量。"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_FILE = BASE_DIR / "monitor_state.json"

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")

# ---- Instagram Story（已 POC 验证） --------------------------------------
IG_SESSION_USER = os.environ.get("IG_MONITOR_USER", "")  # 本地会话文件名后缀；生产用 IG_COOKIE
IG_COOKIE = os.environ.get("IG_COOKIE", "")  # 生产：整串 Cookie 头；本地留空走 session 文件
IG_ACCOUNTS = [  # (用户名, 用户ID) —— ID 永久不变，POC 已实测
    ("miyamoto_doppo", 10584438821),
    ("elephantsinc_official", 66821998949),
    ("h.m.staff", 71496324324),
]
IG_WEB_APP_ID = "936619743392459"

# ---- Instagram Post/Reel（instagrapi 私有 API，与 Story 源分离） ----------
# 频率：30 分钟源（CHECK_INTERVAL_TICKS = 6）；失败后另见 COOLDOWN_*。
IG_POST_ACCOUNTS = [  # (用户名, 用户ID)
    ("miyamoto_doppo", 10584438821),
    ("elephantsinc_official", 66821998949),
    ("h.m.staff", 71496324324),
]
IG_POST_SESSION_JSON = os.environ.get("IG_POST_SESSION_JSON", "")  # Actions: session.json 完整内容

# ---- X（已 POC 验证，完全无账号） ----------------------------------------
X_USERS = [  # 按优先级排列：主号在前；每个账号 = 独立 source（独立去重/频率/报警）
    "miyamoto_hiroji",  # 最重要：每轮查
    "paonews_info",     # 次要：每 12 轮（≈60 分钟）查
    "hmnews_info",
    "elekashi_ofcl",
]
X_USER = X_USERS[0]  # 兼容保留：旧单账号入口默认主号
X_UA = "Feedfetcher-Google; (+http://www.google.com/feedfetcher.html)"
X_MIRRORS = [  # 按优先级排列；按顺序尝试，遇到第一个成功的就停止
    "https://nitter.kareem.one",
    "https://nitter.miningtcup.me",
    "https://nitter.meowing.monster",
    "https://nitter.xitter.cc",
    "https://nitter.jaydenha.uk",
    "https://nitter.click",
    "https://x.n0g.xyz",
    "https://tw.eir-nya.gay",
]
# 最后兜底：公开代抓服务（它的服务器出口，绕开对数据中心 IP 的封锁；免费 20 次/分钟）
X_PROXY = "https://r.jina.ai"

# ---- YouTube（官方频道 RSS，无账号） --------------------------------------
YOUTUBE_CHANNELS = [
    ("artist", "UCcUcK64JLSZAPUfG07s-Wew"),
    ("band", "UCT9b7yx6qEl0q994k4s6IEw"),
]
# Data API v3 备用通道（POC）：仅 RSS 失败时 fallback 用；Key 只从环境变量读
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# ---- TikTok（公开主页解析，无账号） ----------------------------------------
# 官方 = @miyamoto_hiroji_（带下划线；无下划线的是占位号）。secUid 稳定，供日后升级用。
TIKTOK_ACCOUNTS = [
    ("miyamoto_hiroji_", "MS4wLjABAAAAI_uFHmmcaJqxDN6W2Ve-FWRXWI3Lovy8-0xEksEXiqG3_htnn6Lnd0zSB7IhQQpN"),
]

# ---- 检查频率：Cloudflare 每 ~5 分钟触发一轮 Action（延迟不可避免）。
#   为避免墙钟漂移漏查，改为按轮次计数（每轮 Action = 1 tick）：
#   5 分钟源每轮查，10 分钟源每 2 轮查，15 分钟源每 3 轮查，30 分钟源每 6 轮查。
CHECK_INTERVAL_MINUTES = {  # 保留：人类可读的期望 cadence（实际门控用下面的轮次）
    "ig_story": 10,
    "ig_post": 30,  # 私有 API：进一步降频以降低被 IG 限流/封禁的风险
    "x": 5,
    "x_paonews_info": 15,
    "x_hmnews_info": 15,
    "x_elekashi_ofcl": 15,
    "site_miyamoto": 15,
    "site_ek": 15,
    "site_ekfc": 15,
    "site_elephantsinc": 15,
}
YOUTUBE_CHECK_INTERVAL_MINUTES = 10  # youtube_<channel_id> 前缀匹配
TIKTOK_CHECK_INTERVAL_MINUTES = 10  # tiktok_<handle> 前缀匹配
DEFAULT_CHECK_INTERVAL_MINUTES = 5  # 未知新源的兜底：保持最高频率

CHECK_INTERVAL_TICKS = {  # 实际门控：每 N 轮查一次
    "ig_story": 2,  # ≈10 分钟（私有 API 会话：降频降低风控压力）
    "ig_post": 6,  # ≈30 分钟；失败后由下面的 COOLDOWN_* 进一步降频
    "x": 1,  # 主号 @miyamoto_hiroji：每轮查，不受次要号影响
    "x_paonews_info": 3,  # 次要 X 号：每 3 轮（≈15 分钟）查
    "x_hmnews_info": 3,
    "x_elekashi_ofcl": 3,
    "site_miyamoto": 3,
    "site_ek": 3,
    "site_ekfc": 3,
    "site_elephantsinc": 3,
}
YOUTUBE_CHECK_INTERVAL_TICKS = 2  # youtube_<channel_id> 前缀匹配
TIKTOK_CHECK_INTERVAL_TICKS = 2  # tiktok_<handle> 前缀匹配
DEFAULT_CHECK_INTERVAL_TICKS = 1  # 未知新源的兜底：每轮都查

# ---- 整点对齐 --------------------------------------------------------------
# 命中的源不再按"距上次检查满 N 轮"触发，而是**每个自然小时的第一次 tick** 检查
# （例如 19:00 起的第一个 tick，受 Actions 调度影响可能自然顺延到 19:01~19:05）。
# 2026-09 起次要 X 号与网站源改为 15 分钟轮询，已全部移出该表；机制保留备用。
HOUR_ALIGNED_SOURCES: set[str] = set()


def is_hour_aligned(key: str) -> bool:
    return key in HOUR_ALIGNED_SOURCES


def check_interval_minutes(key: str) -> int:
    """返回某 source 的期望检查间隔（分钟，仅展示/兼容用）。精确匹配优先，其次前缀匹配。"""
    if key in CHECK_INTERVAL_MINUTES:
        return CHECK_INTERVAL_MINUTES[key]
    if key.startswith("youtube_"):
        return YOUTUBE_CHECK_INTERVAL_MINUTES
    if key.startswith("tiktok_"):
        return TIKTOK_CHECK_INTERVAL_MINUTES
    return DEFAULT_CHECK_INTERVAL_MINUTES


def check_interval_ticks(key: str) -> int:
    """返回某 source 每 N 轮查一次。精确匹配优先，其次前缀匹配。"""
    if key in CHECK_INTERVAL_TICKS:
        return CHECK_INTERVAL_TICKS[key]
    if key.startswith("youtube_"):
        return YOUTUBE_CHECK_INTERVAL_TICKS
    if key.startswith("tiktok_"):
        return TIKTOK_CHECK_INTERVAL_TICKS
    return DEFAULT_CHECK_INTERVAL_TICKS


# ---- 故障报警 --------------------------------------------------------------
# 连续“检查轮次”失败 N 次发一封（只在检查轮计数，跳过轮不计数）。
# 时间含义：5 分钟源 6 轮 ≈ 30 分钟；10 分钟源 ≈ 60 分钟；15 分钟源 ≈ 90 分钟。
ALERT_THRESHOLD = 6

# ---- 失败冷却（只对列出的源生效；其他源行为与改动前完全一致） ------------------
# ig_post 走 instagrapi 私有 API（对 3 个账号各发 1 次 user_medias，默认 10 分钟
# 一轮）。触发 IG “Please wait a few minutes before you try again”限流后，若继续
# 高频重试会加重账号标记甚至封禁；因此对**本源**采用与 ig_story 的 checkpoint
# 同构的处理：
#   失败 → 本源冷却 N 轮（零请求、不计入成功/失败）+ 限流类事件报警一次（去重）
#   恢复成功 → 解除冷却并重新武装报警
# 未列入 COOLDOWN_SOURCES 的源，cooldown_ticks() 恒返回 0 → 门控逻辑完全跳过。
COOLDOWN_SOURCES = {"ig_post"}      # 仅这些源失败后冷却（置空 set() 即全关）
# 冷却按“调度轮次”计数：每轮 Action ≈5 分钟，与源自身的检查间隔无关
# （即 10 分钟源冷却 12 轮同样是 ≈60 分钟，因为冷却期内每个 tick 都跳过）。
COOLDOWN_TICKS = 3                  # 普通失败：跳过 3 轮 ≈ 15 分钟
RATE_LIMIT_COOLDOWN_TICKS = 12      # 限流类失败：跳过 12 轮 ≈ 60 分钟
RATE_LIMIT_MARKERS = (              # 命中任一（小写子串）即视为平台限流/行为标记
    "pleasewait",                   # instagrapi 异常类名 PleaseWaitFewMinutes
    "please wait",
    "few minutes",
    "feedback_required",
    "rate limit",
    "ratelimit",
    "too many requests",
    "429 too many",
)


def is_rate_limited(err_text: str) -> bool:
    """错误文本是否属于平台限流/行为标记（用于选择更长的冷却）。"""
    low = (err_text or "").lower()
    return any(marker in low for marker in RATE_LIMIT_MARKERS)


def cooldown_ticks(key: str, err_text: str = "") -> int:
    """失败后本源应跳过的轮数；未启用冷却的源返回 0（行为不变）。"""
    if key not in COOLDOWN_SOURCES:
        return 0
    return RATE_LIMIT_COOLDOWN_TICKS if is_rate_limited(err_text) else COOLDOWN_TICKS
