"""Event bus แบบ in-process

ออกแบบให้พนักงาน AI แต่ละคนไม่รู้จักกัน คุยผ่าน bus อย่างเดียว
ถ้าวันหลังจะย้ายไป Redis/NATS ก็เปลี่ยนแค่ไฟล์นี้
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections import deque
from collections.abc import Iterable
from typing import Any

from .events import Event

log = logging.getLogger(__name__)


class Subscription:
    """คิวของ subscriber หนึ่งราย"""

    def __init__(self, patterns: Iterable[str], maxsize: int, name: str) -> None:
        self.patterns = tuple(patterns)
        self.name = name
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0

    def matches(self, topic: str) -> bool:
        return any(fnmatch.fnmatchcase(topic, p) for p in self.patterns)

    def offer(self, event: Event) -> None:
        """ใส่ event เข้าคิวแบบไม่บล็อก ถ้าคิวเต็มให้ทิ้งตัวเก่าสุด

        ยอมทิ้ง event เก่าดีกว่าปล่อยให้ agent ที่ช้าไปหน่วง agent ตัวอื่น
        """
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:  # pragma: no cover - แข่งกันหยิบพอดี
                pass
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:  # pragma: no cover
                pass

    async def get(self) -> Event:
        return await self.queue.get()


class EventBus:
    """pub/sub ง่าย ๆ พร้อมเก็บประวัติไว้โชว์บน dashboard"""

    def __init__(self, history_size: int = 500, queue_size: int = 200) -> None:
        self._subs: list[Subscription] = []
        self._history: deque[Event] = deque(maxlen=history_size)
        self._queue_size = queue_size
        self._published = 0

    def subscribe(self, *patterns: str, name: str = "anon") -> Subscription:
        """สมัครรับ event ตาม pattern เช่น `comment.*` หรือ `*`"""
        sub = Subscription(patterns or ("*",), self._queue_size, name)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        if sub in self._subs:
            self._subs.remove(sub)

    def publish(self, event: Event) -> None:
        """ส่ง event ให้ทุกคนที่สนใจ — ไม่บล็อก เรียกจาก sync context ได้"""
        self._published += 1
        self._history.append(event)
        for sub in self._subs:
            if sub.matches(event.topic):
                sub.offer(event)

    def history(self, limit: int = 100, topic_glob: str = "*") -> list[dict[str, Any]]:
        items = [e for e in self._history if fnmatch.fnmatchcase(e.topic, topic_glob)]
        return [e.as_dict() for e in items[-limit:]]

    def stats(self) -> dict[str, Any]:
        return {
            "published": self._published,
            "subscribers": [
                {
                    "name": s.name,
                    "patterns": list(s.patterns),
                    "pending": s.queue.qsize(),
                    "dropped": s.dropped,
                }
                for s in self._subs
            ],
        }
