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

# ---- X（已 POC 验证，完全无账号） ----------------------------------------
X_USER = "miyamoto_hiroji"
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

# ---- TikTok（公开主页解析，无账号） ----------------------------------------
# 官方 = @miyamoto_hiroji_（带下划线；无下划线的是占位号）。secUid 稳定，供日后升级用。
TIKTOK_ACCOUNTS = [
    ("miyamoto_hiroji_", "MS4wLjABAAAAI_uFHmmcaJqxDN6W2Ve-FWRXWI3Lovy8-0xEksEXiqG3_htnn6Lnd0zSB7IhQQpN"),
]

# ---- 检查频率（分钟）：Cloudflare 仍每 5 分钟触发一次，进程内按 state 限流 ----
CHECK_INTERVAL_MINUTES = {
    "ig_story": 5,
    "x": 5,
    "site_miyamoto": 60,
    "site_ek": 60,
    "site_ekfc": 60,
    "site_elephantsinc": 60,
}
YOUTUBE_CHECK_INTERVAL_MINUTES = 10  # youtube_<channel_id> 前缀匹配
TIKTOK_CHECK_INTERVAL_MINUTES = 10  # tiktok_<handle> 前缀匹配
DEFAULT_CHECK_INTERVAL_MINUTES = 5  # 未知新源的兜底：保持最高频率


def check_interval_minutes(key: str) -> int:
    """返回某 source 的检查间隔（分钟）。精确匹配优先，其次前缀匹配。"""
    if key in CHECK_INTERVAL_MINUTES:
        return CHECK_INTERVAL_MINUTES[key]
    if key.startswith("youtube_"):
        return YOUTUBE_CHECK_INTERVAL_MINUTES
    if key.startswith("tiktok_"):
        return TIKTOK_CHECK_INTERVAL_MINUTES
    return DEFAULT_CHECK_INTERVAL_MINUTES


# ---- 故障报警 --------------------------------------------------------------
ALERT_THRESHOLD = 6  # 同一来源连续失败 N 轮发一封报警（≈30 分钟）
