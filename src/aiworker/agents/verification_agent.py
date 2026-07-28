"""พนักงานเฝ้าปริศนายืนยันตัวตน (จิ๊กซอว์)

งานเดียว แต่เป็นงานที่คนทำได้แย่ที่สุด:
นั่งจ้องจอ 5 เครื่องพร้อมกันตลอดกะ รอสิ่งที่มาชั่วโมงละครั้งแบบเดาไม่ได้
พลาดครั้งเดียวคือโดนเตือน

สิ่งที่ระบบนี้ทำ:
- เห็นปริศนาภายในไม่กี่วินาที
- ปลุกคนทันที แล้วปลุกซ้ำแรงขึ้นเรื่อย ๆ ถ้ายังไม่มีใครจัดการ
- นับถอยหลังให้เห็นชัดว่าเหลือเวลาเท่าไหร่ และเครื่องไหน
- บันทึกสถิติว่าตอบสนองเร็วแค่ไหน พลาดไปกี่ครั้ง

สิ่งที่ระบบนี้ไม่ทำ: เลื่อนปริศนาแทนคน
ปริศนามีไว้ยืนยันว่ามีคนเฝ้าอยู่ ถ้าให้โปรแกรมเลื่อนแทนก็เท่ากับปิดตาระบบตรวจสอบ
ซึ่งเป็นเหตุให้โดนระงับถาวรถ้าโดนจับได้ — เสี่ยงกว่าเวลาที่ประหยัดได้มาก
ตัวเฝ้าจอจึงอ่านภาพอย่างเดียว ไม่มีความสามารถควบคุมเมาส์หรือคีย์บอร์ดเลย
"""

from __future__ import annotations

from ..domain.verification import ChallengeState, ChallengeTracker
from ..events import ChallengeDetected, ChallengeResolved, Event, Severity
from ..settings import Settings
from ..state import ShiftState
from .base import Agent


class VerificationAgent(Agent):
    """เฝ้าปริศนาและปลุกคนให้ทันเวลา"""

    name = "verify_watcher"

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        watcher,
    ) -> None:
        super().__init__(bus, state, settings)
        self.watcher = watcher
        self.tick_interval = settings.verification.poll_seconds
        self.tracker = ChallengeTracker(
            machine=settings.verification.machine_name or settings.shop.channel_id,
            deadline_seconds=settings.verification.deadline_seconds,
            min_confidence=settings.verification.min_confidence,
        )
        self._ready = False
        self._snapshot = ""

    async def on_start(self) -> None:
        self._ready = await self.watcher.connect()
        if not self._ready:
            self.say(
                "ตัวเฝ้าจอยังใช้ไม่ได้ — ปริศนาจิ๊กซอว์จะไม่มีใครเตือน "
                "ต้องมีคนเฝ้าจอเองตลอดกะ",
                "error",
            )
            self.notify(
                "ตัวเฝ้าปริศนาไม่พร้อมใช้งาน",
                "ยังไม่ได้ calibrate หรือยังไม่ได้ติดตั้ง mss/pillow\n"
                "รัน: python scripts/calibrate_screen.py\n"
                "ระหว่างนี้ต้องมีคนเฝ้าจอเองตลอด ไม่งั้นพลาดปริศนาแล้วโดนเตือน",
                Severity.HIGH,
                needs_human=True,
            )

    async def on_stop(self) -> None:
        await self.watcher.disconnect()

    async def handle(self, event: Event) -> None:
        """ตัวนี้ไม่ต้องรอ event จากใคร ทำงานจากการอ่านจออย่างเดียว"""

    async def tick(self) -> None:
        if not self._ready:
            return

        reading = await self.watcher.read()

        if reading.challenge_visible:
            self._snapshot = reading.snapshot_path
            fresh = self.tracker.on_detected(reading.confidence)
            if fresh is not None:
                self.state.verification.total += 1
                self.say(
                    f"เจอปริศนาจิ๊กซอว์ที่เครื่อง {self.tracker.machine} — "
                    f"ต้องมีคนไปเลื่อนภายใน {fresh.deadline_seconds / 60:.0f} นาที",
                    "warn",
                )
            self._escalate()
        else:
            solved = self.tracker.on_cleared()
            self._snapshot = ""
            if solved is not None:
                self.state.verification.solved += 1
                self.emit(
                    ChallengeResolved(
                        machine=solved.machine,
                        solved=True,
                        seconds_taken=solved.seconds_taken,
                    )
                )
                self.say(
                    f"ปริศนาถูกจัดการแล้ว ใช้เวลา {solved.seconds_taken:.0f} วินาที"
                )

        missed = self.tracker.check_expiry()
        if missed is not None:
            self.state.verification.missed += 1
            self.emit(
                ChallengeResolved(
                    machine=missed.machine,
                    solved=False,
                    seconds_taken=missed.seconds_taken,
                )
            )
            self.say(
                f"หมดเวลาปริศนาที่เครื่อง {missed.machine} — น่าจะโดนเตือนแล้ว",
                "error",
            )
            self.notify(
                "พลาดปริศนายืนยันตัวตน",
                f"เครื่อง {missed.machine} ไม่มีใครเลื่อนภายในเวลา\n"
                "ไปเช็คสถานะช่องด่วน อาจโดนเตือนหรือหลุดไลฟ์แล้ว",
                Severity.CRITICAL,
                needs_human=True,
            )

        self.state.verification.current = (
            self.tracker.current.as_dict() if self.tracker.current else None
        )

    def _escalate(self) -> None:
        """ปลุกคนแรงขึ้นเรื่อย ๆ ตามเวลาที่ผ่านไป"""
        challenge = self.tracker.current
        if challenge is None or challenge.state is not ChallengeState.PENDING:
            return

        for step in self.tracker.due_escalations():
            left = challenge.seconds_left
            self.emit(
                ChallengeDetected(
                    machine=challenge.machine,
                    seconds_left=left,
                    confidence=challenge.confidence,
                    urgency=step.urgency,
                )
            )
            severity = (
                Severity.CRITICAL if left <= 120 else Severity.HIGH
            )
            self.notify(
                f"จิ๊กซอว์! เครื่อง {challenge.machine} — เหลือ {left / 60:.1f} นาที",
                f"{step.urgency}\n"
                f"ไปเลื่อนปริศนาที่เครื่อง {challenge.machine} ให้ถูกตำแหน่ง\n"
                "ถ้าไม่ทันจะโดนเตือนหรือหลุดไลฟ์",
                severity,
                needs_human=True,
                image_path=self._snapshot,
            )
