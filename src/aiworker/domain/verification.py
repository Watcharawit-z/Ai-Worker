"""ตัวจับเวลาปริศนายืนยันตัวตน (จิ๊กซอว์)

TikTok เด้งปริศนาเลื่อนภาพขึ้นมาเป็นระยะเพื่อยืนยันว่ามี "คน" เฝ้าไลฟ์อยู่จริง
มีเวลาจำกัดในการเลื่อน ถ้าไม่ทำหรือทำไม่ถูก จะโดนเตือนหรือหลุดไลฟ์

ระบบนี้ทำหน้าที่ **ตรวจจับและปลุกคน** ให้ทันเวลาเท่านั้น
ระบบไม่เลื่อนปริศนาแทน เพราะปริศนานี้มีไว้ยืนยันว่ามีคนอยู่
การเลื่อนแทนคือการทำให้การตรวจสอบนั้นไร้ความหมาย และเป็นเหตุให้โดนระงับ
ถ้าโดนจับได้ — ซึ่งเป็นความเสี่ยงที่ใหญ่กว่าเวลาที่ประหยัดได้มาก

สิ่งที่ระบบทำแทนได้จริง และเป็นงานที่คนทำได้แย่กว่าเครื่อง:
เฝ้าจอ 5 เครื่องพร้อมกันตลอด 24 ชม. แล้วปลุกภายในไม่กี่วินาทีเมื่อปริศนาโผล่
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class ChallengeState(str, Enum):
    IDLE = "idle"
    """ไม่มีปริศนาค้างอยู่"""

    PENDING = "pending"
    """ปริศนาโผล่แล้ว กำลังรอคนมาเลื่อน"""

    SOLVED = "solved"
    """ปริศนาหายไปแล้ว = มีคนจัดการทัน"""

    MISSED = "missed"
    """หมดเวลาแล้วปริศนายังอยู่ = น่าจะโดนเตือนแล้ว"""


@dataclass(slots=True)
class Challenge:
    """ปริศนาหนึ่งครั้ง"""

    machine: str
    detected_at: float
    deadline_seconds: float
    state: ChallengeState = ChallengeState.PENDING
    resolved_at: float | None = None
    confidence: float = 1.0
    """ความมั่นใจของตัวตรวจจับ — ต่ำแปลว่าอาจเป็นสัญญาณหลอก"""

    @property
    def seconds_left(self) -> float:
        if self.state is not ChallengeState.PENDING:
            return 0.0
        return max(0.0, self.deadline_seconds - (time.time() - self.detected_at))

    @property
    def seconds_taken(self) -> float:
        end = self.resolved_at or time.time()
        return end - self.detected_at

    @property
    def is_expired(self) -> bool:
        return self.state is ChallengeState.PENDING and self.seconds_left <= 0.0

    def as_dict(self) -> dict:
        return {
            "machine": self.machine,
            "detected_at": self.detected_at,
            "state": self.state.value,
            "seconds_left": round(self.seconds_left, 1),
            "seconds_taken": round(self.seconds_taken, 1),
            "confidence": round(self.confidence, 2),
        }


@dataclass(slots=True)
class EscalationStep:
    """จังหวะการปลุกคน — ยิ่งใกล้หมดเวลา ยิ่งดังขึ้น"""

    after_seconds: float
    channels: list[str]
    urgency: str


DEFAULT_ESCALATION = [
    EscalationStep(0.0, ["console", "webhook"], "แจ้งทันที"),
    EscalationStep(45.0, ["telegram"], "ยังไม่มีใครจัดการ"),
    EscalationStep(120.0, ["telegram", "webhook"], "เหลือเวลาไม่ถึงครึ่ง"),
    EscalationStep(210.0, ["telegram", "webhook"], "ใกล้หมดเวลาแล้ว"),
]


class ChallengeTracker:
    """คุมสถานะปริศนาของเครื่องหนึ่งเครื่อง + จังหวะการปลุกคน"""

    def __init__(
        self,
        machine: str,
        deadline_seconds: float = 300.0,
        escalation: list[EscalationStep] | None = None,
        min_confidence: float = 0.6,
    ) -> None:
        self.machine = machine
        self.deadline_seconds = deadline_seconds
        self.escalation = escalation or list(DEFAULT_ESCALATION)
        self.min_confidence = min_confidence
        self.current: Challenge | None = None
        self.history: list[Challenge] = []
        self._steps_fired: set[int] = set()

    # ---------------- สัญญาณจากตัวตรวจจับ ----------------

    def on_detected(self, confidence: float = 1.0) -> Challenge | None:
        """ตัวตรวจจับบอกว่าเห็นปริศนาบนจอ

        คืน Challenge ใหม่เฉพาะตอนที่เป็นการโผล่ครั้งใหม่จริง ๆ
        (ตัวตรวจจับจะยิงสัญญาณซ้ำทุกรอบที่ปริศนายังอยู่)
        """
        if confidence < self.min_confidence:
            return None
        if self.current is not None and self.current.state is ChallengeState.PENDING:
            self.current.confidence = max(self.current.confidence, confidence)
            return None

        challenge = Challenge(
            machine=self.machine,
            detected_at=time.time(),
            deadline_seconds=self.deadline_seconds,
            confidence=confidence,
        )
        self.current = challenge
        self._steps_fired.clear()
        return challenge

    def on_cleared(self) -> Challenge | None:
        """ตัวตรวจจับบอกว่าปริศนาหายไปแล้ว = มีคนเลื่อนสำเร็จ"""
        challenge = self.current
        if challenge is None or challenge.state is not ChallengeState.PENDING:
            return None
        challenge.state = ChallengeState.SOLVED
        challenge.resolved_at = time.time()
        self.history.append(challenge)
        self.current = None
        return challenge

    def check_expiry(self) -> Challenge | None:
        """เรียกเป็นรอบ ๆ — คืนค่าเมื่อเพิ่งหมดเวลาพอดี"""
        challenge = self.current
        if challenge is None or not challenge.is_expired:
            return None
        challenge.state = ChallengeState.MISSED
        challenge.resolved_at = time.time()
        self.history.append(challenge)
        self.current = None
        return challenge

    # ---------------- การปลุกคน ----------------

    def due_escalations(self) -> list[EscalationStep]:
        """ขั้นการปลุกที่ถึงเวลาแล้วและยังไม่ได้ยิง"""
        challenge = self.current
        if challenge is None or challenge.state is not ChallengeState.PENDING:
            return []
        elapsed = time.time() - challenge.detected_at
        due = []
        for i, step in enumerate(self.escalation):
            if i not in self._steps_fired and elapsed >= step.after_seconds:
                self._steps_fired.add(i)
                due.append(step)
        return due

    # ---------------- สรุปผล ----------------

    def stats(self) -> dict:
        solved = [c for c in self.history if c.state is ChallengeState.SOLVED]
        missed = [c for c in self.history if c.state is ChallengeState.MISSED]
        avg = sum(c.seconds_taken for c in solved) / len(solved) if solved else 0.0
        return {
            "machine": self.machine,
            "current": self.current.as_dict() if self.current else None,
            "total": len(self.history),
            "solved": len(solved),
            "missed": len(missed),
            "avg_response_seconds": round(avg, 1),
            "worst_response_seconds": round(
                max((c.seconds_taken for c in solved), default=0.0), 1
            ),
        }
