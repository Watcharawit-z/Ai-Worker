"""ชนิดของ event ทั้งหมดที่วิ่งอยู่ในระบบ

ทุกอย่างที่พนักงาน AI คุยกันจะผ่าน event เหล่านี้ ไม่มีการเรียกกันตรง ๆ
ทำให้เพิ่ม/ถอดพนักงานได้โดยไม่กระทบตัวอื่น
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class Severity(str, Enum):
    """ระดับความรุนแรงของสิ่งที่ตรวจพบ"""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.LOW: 1,
    Severity.MEDIUM: 2,
    Severity.HIGH: 3,
    Severity.CRITICAL: 4,
}


@dataclass(slots=True)
class Event:
    """base ของทุก event

    `topic` เป็น ClassVar ไม่ใช่ field — subclass จึงเขียนทับได้ด้วยการประกาศ
    ธรรมดา และไม่กินพื้นที่ slot ต่อ instance
    """

    topic: ClassVar[str] = "event"

    id: str = field(default_factory=_new_id)
    ts: float = field(default_factory=_now)
    channel_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"topic": self.topic}
        for slot in _all_slots(type(self)):
            value = getattr(self, slot, None)
            out[slot] = value.value if isinstance(value, Enum) else value
        return out


def _all_slots(cls: type) -> list[str]:
    seen: list[str] = []
    for klass in reversed(cls.__mro__):
        for slot in getattr(klass, "__slots__", ()):  # type: ignore[arg-type]
            if slot not in seen:
                seen.append(slot)
    return seen


# --------------------------------------------------------------------------
# สตรีม / การเล่นวิดีโอ (ต่อจิ๊กซอว์)
# --------------------------------------------------------------------------


@dataclass(slots=True)
class StreamHealth(Event):
    """สุขภาพของสตรีมที่กำลังรีรันอยู่"""

    topic = "stream.health"
    is_live: bool = False
    bitrate_kbps: int = 0
    dropped_frames: int = 0
    fps: float = 0.0
    viewers: int = 0
    current_segment: str | None = None
    seconds_into_segment: float = 0.0
    seconds_remaining: float = 0.0


@dataclass(slots=True)
class SegmentChanged(Event):
    """เปลี่ยนคลิป/ท่อนไลฟ์ (ต่อจิ๊กซอว์สำเร็จ)"""

    topic = "stream.segment_changed"
    from_segment: str | None = None
    to_segment: str = ""
    reason: str = ""


@dataclass(slots=True)
class ChallengeDetected(Event):
    """ปริศนายืนยันตัวตน (จิ๊กซอว์) โผล่ขึ้นมาบนจอ — ต้องมีคนไปเลื่อน"""

    topic = "verify.challenge_detected"
    machine: str = ""
    seconds_left: float = 0.0
    confidence: float = 0.0
    urgency: str = ""


@dataclass(slots=True)
class ChallengeResolved(Event):
    """ปริศนาถูกจัดการแล้ว หรือหมดเวลาไปแล้ว"""

    topic = "verify.challenge_resolved"
    machine: str = ""
    solved: bool = False
    seconds_taken: float = 0.0


@dataclass(slots=True)
class BasketPinned(Event):
    """ปักตะกร้าขึ้นแสดง ให้ตรงกับสินค้าที่คนในคลิปกำลังพูดถึง"""

    topic = "basket.pinned"
    basket_id: str = ""
    name: str = ""
    sku: str = ""
    price: float = 0.0
    reason: str = ""
    segment: str = ""


@dataclass(slots=True)
class StreamAlert(Event):
    """สตรีมมีปัญหา เช่น ค้าง ดับ เฟรมตก"""

    topic = "stream.alert"
    severity: Severity = Severity.MEDIUM
    kind: str = ""
    detail: str = ""


# --------------------------------------------------------------------------
# คอมเมนท์
# --------------------------------------------------------------------------


@dataclass(slots=True)
class CommentIn(Event):
    """คอมเมนท์ที่ลูกค้าพิมพ์เข้ามา"""

    topic = "comment.in"
    user_id: str = ""
    nickname: str = ""
    text: str = ""
    is_follower: bool = False
    is_subscriber: bool = False


@dataclass(slots=True)
class CommentReply(Event):
    """ข้อความที่พนักงาน AI จะตอบกลับ"""

    topic = "comment.reply"
    reply_to_user: str = ""
    reply_to_comment_id: str = ""
    text: str = ""
    intent: str = ""
    confidence: float = 0.0
    auto_sent: bool = True


@dataclass(slots=True)
class CommentEscalation(Event):
    """คอมเมนท์ที่ AI ไม่กล้าตอบเอง ต้องให้คนดู"""

    topic = "comment.escalation"
    comment_id: str = ""
    nickname: str = ""
    text: str = ""
    reason: str = ""


# --------------------------------------------------------------------------
# การละเมิดกฎ
# --------------------------------------------------------------------------


@dataclass(slots=True)
class ViolationDetected(Event):
    """ตรวจพบความเสี่ยงผิดกฎ TikTok"""

    topic = "compliance.violation"
    severity: Severity = Severity.MEDIUM
    code: str = ""
    detail: str = ""
    source: str = ""
    evidence: str = ""
    suggested_action: str = ""


@dataclass(slots=True)
class ComplianceAction(Event):
    """สิ่งที่ระบบลงมือทำหลังเจอความเสี่ยง"""

    topic = "compliance.action"
    action: str = ""
    reason: str = ""
    violation_code: str = ""


# --------------------------------------------------------------------------
# ตะกร้า / สินค้า
# --------------------------------------------------------------------------


@dataclass(slots=True)
class BasketActivated(Event):
    """ขึ้นตะกร้าใหม่"""

    topic = "basket.activated"
    basket_id: str = ""
    name: str = ""
    sku: str = ""
    price: float = 0.0
    position_in_queue: int = 0
    queue_size: int = 0


@dataclass(slots=True)
class BasketPerformance(Event):
    """ผลขายของตะกร้าที่กำลังรันอยู่"""

    topic = "basket.performance"
    basket_id: str = ""
    minutes_live: float = 0.0
    orders: int = 0
    revenue: float = 0.0
    verdict: str = ""


@dataclass(slots=True)
class BasketRotateRequest(Event):
    """ขอเปลี่ยนตะกร้า"""

    topic = "basket.rotate_request"
    reason: str = ""
    requested_by: str = ""


# --------------------------------------------------------------------------
# แอด
# --------------------------------------------------------------------------


@dataclass(slots=True)
class AdAction(Event):
    """การกระทำกับแอด"""

    topic = "ads.action"
    action: str = ""
    campaign_id: str = ""
    basket_id: str = ""
    budget: float = 0.0
    reason: str = ""


@dataclass(slots=True)
class AdMetrics(Event):
    """ตัวเลขแอดล่าสุด"""

    topic = "ads.metrics"
    campaign_id: str = ""
    basket_id: str = ""
    spend: float = 0.0
    orders: int = 0
    revenue: float = 0.0
    cpa: float = 0.0
    roas: float = 0.0


# --------------------------------------------------------------------------
# แจ้งเตือนเจ้าของร้าน / log
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Notification(Event):
    """เรื่องที่ต้องให้คนรู้"""

    topic = "notify"
    severity: Severity = Severity.INFO
    title: str = ""
    body: str = ""
    needs_human: bool = False


@dataclass(slots=True)
class AgentLog(Event):
    """บันทึกสิ่งที่พนักงาน AI แต่ละคนทำ (ใช้โชว์บน dashboard)"""

    topic = "agent.log"
    agent: str = ""
    message: str = ""
    level: str = "info"
