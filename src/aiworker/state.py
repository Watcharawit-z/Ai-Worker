"""สถานะร่วมของกะไลฟ์หนึ่งกะ — แหล่งความจริงเดียวที่ทุก agent อ่านได้"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from .domain.basket import BasketQueue
from .domain.segments import SegmentPlaylist
from .events import Severity


@dataclass(slots=True)
class StreamStatus:
    is_live: bool = False
    bitrate_kbps: int = 0
    dropped_frames: int = 0
    fps: float = 0.0
    viewers: int = 0
    current_segment: str | None = None
    seconds_remaining: float = 0.0
    last_update: float = field(default_factory=time.time)
    last_alert: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "is_live": self.is_live,
            "bitrate_kbps": self.bitrate_kbps,
            "dropped_frames": self.dropped_frames,
            "fps": round(self.fps, 1),
            "viewers": self.viewers,
            "current_segment": self.current_segment,
            "seconds_remaining": round(self.seconds_remaining, 1),
            "stale_seconds": round(time.time() - self.last_update, 1),
            "last_alert": self.last_alert,
        }


@dataclass(slots=True)
class ComplianceStatus:
    strikes: int = 0
    worst_severity: Severity = Severity.INFO
    last_violation: str = ""
    last_violation_at: float = 0.0
    paused: bool = False
    recent: deque = field(default_factory=lambda: deque(maxlen=50))

    def as_dict(self) -> dict[str, Any]:
        return {
            "strikes": self.strikes,
            "worst_severity": self.worst_severity.value,
            "last_violation": self.last_violation,
            "paused": self.paused,
            "recent": list(self.recent),
        }


@dataclass(slots=True)
class AdsStatus:
    campaign_id: str = ""
    budget: float = 0.0
    spend: float = 0.0
    orders: int = 0
    revenue: float = 0.0
    last_action: str = ""

    @property
    def roas(self) -> float:
        return self.revenue / self.spend if self.spend > 0 else 0.0

    @property
    def cpa(self) -> float:
        return self.spend / self.orders if self.orders > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "budget": round(self.budget, 2),
            "spend": round(self.spend, 2),
            "orders": self.orders,
            "revenue": round(self.revenue, 2),
            "roas": round(self.roas, 2),
            "cpa": round(self.cpa, 2),
            "last_action": self.last_action,
        }


class ShiftState:
    """สถานะทั้งกะ — agent อ่านได้ทุกตัว แต่เขียนเฉพาะส่วนที่ตัวเองรับผิดชอบ"""

    def __init__(
        self,
        channel_id: str,
        baskets: BasketQueue,
        playlist: SegmentPlaylist,
    ) -> None:
        self.channel_id = channel_id
        self.started_at = time.time()
        self.baskets = baskets
        self.playlist = playlist
        self.stream = StreamStatus()
        self.compliance = ComplianceStatus()
        self.ads = AdsStatus()
        self.comment_count = 0
        self.reply_count = 0
        self.escalation_count = 0
        self.agent_logs: deque[dict[str, Any]] = deque(maxlen=300)
        self.running = True

    @property
    def uptime_minutes(self) -> float:
        return (time.time() - self.started_at) / 60.0

    def log(self, agent: str, message: str, level: str = "info") -> None:
        self.agent_logs.append(
            {"ts": time.time(), "agent": agent, "message": message, "level": level}
        )

    def snapshot(self) -> dict[str, Any]:
        """ภาพรวมทั้งหมด — ใช้ป้อน dashboard และใช้เป็น context ให้ LLM"""
        return {
            "channel_id": self.channel_id,
            "uptime_minutes": round(self.uptime_minutes, 1),
            "stream": self.stream.as_dict(),
            "baskets": self.baskets.summary(),
            "compliance": self.compliance.as_dict(),
            "ads": self.ads.as_dict(),
            "comments": {
                "received": self.comment_count,
                "replied": self.reply_count,
                "escalated": self.escalation_count,
            },
            "logs": list(self.agent_logs)[-60:],
        }
