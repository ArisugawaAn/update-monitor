"""QQ SMTP 通知。SMTP_* 环境变量未配置时降级为控制台打印（本地 POC 模式）。

一条新动态 = 一封邮件，不合并不延迟（项目明确要求）。
邮件使用 HTML 格式（QQ 邮箱 / 微信可正常渲染），同时附纯文本 fallback。
"""
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from monitor.models import Update

PLATFORM_LABEL = {
    ("instagram", "story"): "Instagram Story",
    ("instagram", "post"): "Instagram Post",
    ("instagram", "reel"): "Instagram Reel",
    ("x", "tweet"): "X",
    ("youtube", "video"): "YouTube",
    ("tiktok", "video"): "TikTok",
    ("website", "news"): "官网",
    ("website", "page"): "官网",
}


def _configured() -> bool:
    return bool(os.environ.get("SMTP_USER") and os.environ.get("SMTP_PASS")
                and os.environ.get("NOTIFY_TO"))


def _prefix() -> str:
    return os.environ.get("NOTIFY_TITLE_PREFIX", "更新通知")


def _preview(u: Update) -> str:
    """从 title/text 提取通知主题预览。"""
    s = (u.title or u.text or "").strip()
    if s:
        return s[:42]
    if u.content_type == "story":
        name = u.account_name.lstrip("@")
        return f"@{name} 发布了新 Story"
    return "新动态"


def _fmt_dt(u: Update, field: str) -> str:
    dt = getattr(u, field, None)
    if not dt:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# ---------------------------------------------------------------- HTML

_STYLE_TD_KEY = ("style='padding:4px 14px 4px 0; color:#8892a0; font-size:13px;"
                 " white-space:nowrap; vertical-align:top;'")
_STYLE_TD_VAL = ("style='padding:4px 0; color:#232946; font-size:14px;'")
_STYLE_CARD = ("style='margin:14px 0; padding:14px 16px; background:#f6f7fb;"
               " border-radius:8px; border-left:3px solid #e94560;'")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _render_html(u: Update, label: str) -> str:
    title_html = ""
    if u.title:
        title_html = f"<p style='font-size:16px; font-weight:600; color:#1a1a2e; margin:0 0 10px;'>{_esc(u.title)}</p>"
    text_html = ""
    if u.text and u.text != u.title:
        text_html = (f"<div {_STYLE_CARD}><p style='margin:0; font-size:14px;"
                     f" color:#555; line-height:1.7;'>{_esc(u.text[:300])}</p></div>")
    media_html = ""
    for i, m in enumerate(u.media_urls[:3], 1):
        media_html += (f"<a href='{m}' style='display:inline-block; margin:4px 8px 4px 0;"
                       f" font-size:13px; color:#4361ee;'>📎 媒体{i}</a>")

    cta = ""
    if u.url:
        cta = (f"<a href='{u.url}' style='display:inline-block; background:#1a1a2e; color:#ffffff;"
               f" padding:10px 28px; border-radius:6px; text-decoration:none;"
               f" font-size:14px; font-weight:600; margin-top:4px;'>查看原文 →</a>")

    return f"""\
<table width="100%" cellpadding="0" cellspacing="0" style="font-family:'Hiragino Sans GB','Microsoft YaHei',sans-serif; max-width:560px; margin:0 auto; border:1px solid #dde1e7; border-radius:12px; overflow:hidden;">
<tr><td style="background:#1a1a2e; padding:14px 22px;">
<span style="color:#ffffff; font-size:17px; font-weight:600;">🌟 有新的官方动态</span>
<span style="color:#8892a0; font-size:12px; float:right; margin-top:3px;">{label}</span>
</td></tr>
<tr><td style="padding:20px 24px 24px;">
{title_html}
{text_html}
<table cellpadding="0" cellspacing="0" style="margin:4px 0 12px;">
<tr><td {_STYLE_TD_KEY}>账号</td><td {_STYLE_TD_VAL}>{_esc(u.account_name)}</td></tr>
<tr><td {_STYLE_TD_KEY}>发布时间</td><td {_STYLE_TD_VAL}>{_fmt_dt(u, "published_at")}</td></tr>
<tr><td {_STYLE_TD_KEY}>发现时间</td><td {_STYLE_TD_VAL}>{_fmt_dt(u, "detected_at")}</td></tr>
</table>
{media_html}
{cta}
</td></tr>
</table>"""


# ---------------------------------------------------------------- 纯文本

def _render_plain(u: Update, label: str) -> str:
    lines = [
        f"来源: {label}",
        f"账号: {u.account_name}",
    ]
    if u.title:
        lines.append(f"标题: {u.title}")
    if u.text and u.text != u.title:
        lines.append(f"内容: {u.text[:300]}")
    lines.append(f"发布: {_fmt_dt(u, 'published_at')}")
    lines.append(f"发现: {_fmt_dt(u, 'detected_at')}")
    if u.url:
        lines.append(f"链接: {u.url}")
    for i, m in enumerate(u.media_urls[:3], 1):
        lines.append(f"媒体{i}: {m}")
    return "\n".join(lines)


def _render(u: Update) -> tuple[str, str, str]:
    """返回 (subject, plain_text, html_body)。"""
    label = PLATFORM_LABEL.get((u.platform, u.content_type), u.platform)
    prefix = _prefix()
    preview = _preview(u)
    subject = f"【{prefix}｜{label}】{preview}"
    return subject, _render_plain(u, label), _render_html(u, label)


# ---------------------------------------------------------------- 发送

def _smtp_send(subject: str, plain: str, html: str) -> bool:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["NOTIFY_TO"]
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.qq.com"),
                              int(os.environ.get("SMTP_PORT", "465")),
                              context=ssl.create_default_context(), timeout=30) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.sendmail(os.environ["SMTP_USER"], [os.environ["NOTIFY_TO"]], msg.as_string())
        return True
    except Exception as e:
        print(f"    ✗ 邮件发送失败: {type(e).__name__}")
        return False


def _send_or_print(subject: str, plain: str, html: str) -> bool:
    if not _configured():
        print(f"    [EMAIL·本地模式] {subject}")
        for line in plain.splitlines()[:6]:
            print(f"      {line}")
        return True
    return _smtp_send(subject, plain, html)


def send_update(u: Update) -> bool:
    """投递新动态邮件；本地未配置 SMTP 时打印并视为已投递。"""
    subject, plain, html = _render(u)
    return _send_or_print(subject, plain, html)


def send_alert(subject: str, body: str) -> bool:
    """故障报警。未配置时打印。"""
    html = f"<p style='font-size:14px; color:#333; line-height:1.6;'>{_esc(body)}</p>"
    return _send_or_print(subject, body, html)


def send_test(source_lines: list[str]) -> bool:
    """健康检查测试邮件：验证 Actions → SMTP → 邮箱（微信）整条链路。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    subject = "【监测系统】测试邮件 — 通路正常"
    plain = "\n".join([
        "这是一封测试邮件。", "",
        f"发送时间: {now}",
        "收到即说明 GitHub Actions → SMTP → 邮箱（微信提醒）链路正常。", "",
        "各数据源当前健康快照：",
        *(f"  {l}" for l in source_lines),
    ])
    rows = "\n".join(
        f"<div style='padding:3px 0; font-size:13px; color:#333;'>{line}</div>"
        for line in source_lines)
    html = f"""\
<table width="100%" cellpadding="0" cellspacing="0" style="font-family:sans-serif; max-width:560px; margin:0 auto; border:1px solid #dde1e7; border-radius:12px;">
<tr><td style="background:#1a1a2e; padding:14px 22px;">
<span style="color:#ffffff; font-size:16px; font-weight:600;">✅ 测试邮件 — 通路正常</span>
</td></tr>
<tr><td style="padding:20px 24px;">
<p style="font-size:14px; color:#232946; margin:0 0 8px;">发送时间: {now}</p>
<p style="font-size:14px; color:#232946; margin:0 0 14px;">收到此邮件说明 GitHub Actions → SMTP → 邮箱（微信提醒）链路正常。</p>
<p style="font-size:13px; color:#8892a0; margin:0 0 6px;">各数据源健康快照：</p>
{rows}
</td></tr>
</table>"""
    return _send_or_print(subject, plain, html)
