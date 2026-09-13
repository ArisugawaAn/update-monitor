"""QQ SMTP 通知。SMTP_* 环境变量未配置时降级为控制台打印（本地 POC 模式）。

一条新动态 = 一封邮件，不合并不延迟（项目明确要求）。
"""
import os
import smtplib
import ssl
from email.header import Header
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


def _render(u: Update) -> tuple[str, str]:
    label = PLATFORM_LABEL.get((u.platform, u.content_type), u.platform)
    prefix = os.environ.get("NOTIFY_TITLE_PREFIX", "更新通知")
    subject = f"【{prefix}｜{label}】新动态"
    lines = [
        "🌟 有新的官方动态", "",
        f"来源: {label}",
        f"账号: {u.account_name}",
        f"发布时间: {u.published_at or '未知'}",
        f"发现时间: {u.detected_at:%Y-%m-%d %H:%M:%S UTC}",
    ]
    if u.title:
        lines.append(f"标题: {u.title}")
    if u.text:
        lines.append(f"摘要: {u.text[:300]}")
    if u.url:
        lines += ["", f"原文: {u.url}"]
    for i, m in enumerate(u.media_urls[:3], 1):
        lines.append(f"媒体{i}: {m}")
    return subject, "\n".join(lines)


def _smtp_send(subject: str, body: str) -> bool:
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["NOTIFY_TO"]
    try:
        with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.qq.com"),
                              int(os.environ.get("SMTP_PORT", "465")),
                              context=ssl.create_default_context(), timeout=30) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.sendmail(os.environ["SMTP_USER"], [os.environ["NOTIFY_TO"]], msg.as_string())
        return True
    except Exception as e:  # 密码/网络失败都不外泄敏感信息
        print(f"    ✗ 邮件发送失败: {type(e).__name__}")
        return False


def send_update(u: Update) -> bool:
    """投递新动态邮件；本地未配置 SMTP 时打印并视为已投递。"""
    subject, body = _render(u)
    if not _configured():
        print(f"    [EMAIL·本地模式] {subject}")
        for line in body.splitlines()[:6]:
            print(f"      {line}")
        return True
    return _smtp_send(subject, body)


def send_alert(subject: str, body: str) -> bool:
    """故障报警（源失联/会话失效/checkpoint）。未配置时打印。"""
    if not _configured():
        print(f"    [ALERT·本地模式] {subject} | {body[:120]}")
        return True
    return _smtp_send(subject, body)


def send_test(source_lines: list[str]) -> bool:
    """健康检查测试邮件：验证 Actions → SMTP → 邮箱（微信）整条链路。"""
    from datetime import datetime, timezone
    subject = "【监测系统】测试邮件 — 通路正常"
    body = "\n".join([
        "这是一封测试邮件。", "",
        f"发送时间: {datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S UTC}",
        "收到即说明 GitHub Actions → SMTP → 邮箱（微信提醒）链路正常。", "",
        "各数据源当前健康快照：",
        *(f"  {line}" for line in source_lines),
    ])
    if not _configured():
        print(f"[EMAIL·本地模式] {subject}\n{body}")
        return True
    return _smtp_send(subject, body)
