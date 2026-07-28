"""โครงพื้นฐานของพนักงาน AI ทุกคน"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod

from ..bus import EventBus, Subscription
from ..events import AgentLog, Event, Notification, Severity
from ..settings import Settings
from ..state import ShiftState

log = logging.getLogger(__name__)


class Agent(ABC):
    """พนักงาน AI หนึ่งคน

    ทุกคนทำงานเป็น 2 ขา:
    - `handle()` — ตอบสนอง event ที่เข้ามา
    - `tick()`   — งานที่ต้องทำเป็นรอบ ๆ เอง (ถ้ามี)
    """

    name = "agent"
    subscribes: tuple[str, ...] = ()
    tick_interval: float | None = None

    def __init__(self, bus: EventBus, state: ShiftState, settings: Settings) -> None:
        self.bus = bus
        self.state = state
        self.settings = settings
        self._sub: Subscription | None = None
        self._tasks: list[asyncio.Task] = []
        self._stopping = asyncio.Event()

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        if self.subscribes:
            self._sub = self.bus.subscribe(*self.subscribes, name=self.name)
            self._tasks.append(asyncio.create_task(self._consume(), name=f"{self.name}-consume"))
        if self.tick_interval:
            self._tasks.append(asyncio.create_task(self._tick_loop(), name=f"{self.name}-tick"))
        await self.on_start()
        self.say("เริ่มทำงานแล้ว")

    async def stop(self) -> None:
        self._stopping.set()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
        self._tasks.clear()
        if self._sub:
            self.bus.unsubscribe(self._sub)
        await self.on_stop()

    async def on_start(self) -> None:
        """hook สำหรับตั้งค่าเริ่มต้น"""

    async def on_stop(self) -> None:
        """hook สำหรับเก็บกวาด"""

    # ---------------- loops ----------------

    async def _consume(self) -> None:
        assert self._sub is not None
        while not self._stopping.is_set():
            try:
                event = await self._sub.get()
            except asyncio.CancelledError:
                raise
            try:
                await self.handle(event)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - agent หนึ่งพังต้องไม่ลากตัวอื่นลงไป
                log.exception("[%s] จัดการ event %s ไม่สำเร็จ", self.name, event.topic)
                self.say(f"เกิดข้อผิดพลาดตอนจัดการ {event.topic}", level="error")

    async def _tick_loop(self) -> None:
        assert self.tick_interval is not None
        while not self._stopping.is_set():
            try:
                await asyncio.sleep(self.tick_interval)
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("[%s] tick ล้มเหลว", self.name)

    # ---------------- ให้ subclass override ----------------

    @abstractmethod
    async def handle(self, event: Event) -> None: ...

    async def tick(self) -> None:
        """งานตามรอบ — ค่าเริ่มต้นคือไม่ทำอะไร"""

    # ---------------- ตัวช่วย ----------------

    def emit(self, event: Event) -> None:
        if not event.channel_id:
            event.channel_id = self.state.channel_id
        self.bus.publish(event)

    _LOG_LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}

    def say(self, message: str, level: str = "info") -> None:
        """บันทึกสิ่งที่พนักงานคนนี้ทำ — เห็นทั้งบน terminal และ dashboard"""
        self.state.log(self.name, message, level)
        self.emit(AgentLog(agent=self.name, message=message, level=level))
        log.log(self._LOG_LEVELS.get(level, logging.INFO), "[%s] %s", self.name, message)

    def notify(
        self,
        title: str,
        body: str,
        severity: Severity = Severity.INFO,
        needs_human: bool = False,
        image_path: str = "",
    ) -> None:
        """ส่งเรื่องถึงคน (เจ้าของร้าน/หัวหน้ากะ)"""
        self.emit(
            Notification(
                title=title,
                body=body,
                severity=severity,
                needs_human=needs_human,
                image_path=image_path,
            )
        )
