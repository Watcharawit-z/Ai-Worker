"""ส่งต่อเรื่องที่ต้องให้คนรู้ ออกไปยังช่องทางจริง

แยกเป็น agent ของตัวเองเพราะ:
- agent อื่นแค่ emit Notification ไม่ต้องรู้ว่าส่งไปทางไหน
- รวมการกันสแปมไว้ที่เดียว (เรื่องเดิมซ้ำใน 5 นาที ส่งครั้งเดียวพอ)
"""

from __future__ import annotations

import time

from ..adapters.notify import NotifierGroup
from ..events import Event, Notification, Severity
from ..settings import Settings
from ..state import ShiftState
from .base import Agent


class NotifierAgent(Agent):
    name = "notifier"
    subscribes = ("notify",)

    DEDUPE_WINDOW = 300.0
    MIN_SEVERITY = Severity.MEDIUM

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        notifiers: NotifierGroup,
    ) -> None:
        super().__init__(bus, state, settings)
        self.notifiers = notifiers
        self._last_sent: dict[str, float] = {}

    async def handle(self, event: Event) -> None:
        if not isinstance(event, Notification):
            return
        # เรื่องเล็กไม่ต้องรบกวนคน ดูย้อนหลังบน dashboard ได้
        if event.severity.rank < self.MIN_SEVERITY.rank and not event.needs_human:
            return

        key = f"{event.severity.value}:{event.title}"
        now = time.time()
        if now - self._last_sent.get(key, 0.0) < self.DEDUPE_WINDOW:
            return
        self._last_sent[key] = now

        prefix = "[ต้องให้คนจัดการ] " if event.needs_human else ""
        await self.notifiers.send(
            f"{prefix}{event.title}",
            f"{event.body}\n\nช่อง: {self.state.channel_id}",
            event.severity.value,
        )
