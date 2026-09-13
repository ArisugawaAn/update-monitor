"""统一动态数据模型与源错误类型：所有监测源最终都转换成 Update。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SourceError(RuntimeError):
    """单个来源检查失败（不影响其他来源）。"""


class CheckpointError(SourceError):
    """IG 账号被要求 checkpoint 验证 —— 必须立即停止该来源并报警。"""


@dataclass
class Update:
    source_key: str            # 源标识（状态分组/去重作用域）
    platform: str              # instagram | x | youtube | tiktok | website
    account_name: str
    content_type: str          # story | tweet | video | news | page（可扩展）
    external_id: str           # 去重主键（源内稳定唯一，绝不自增 ID）
    title: str | None = None
    text: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    detected_at: datetime = field(default_factory=utcnow)
    media_urls: list[str] = field(default_factory=list)
