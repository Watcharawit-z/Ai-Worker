"""อ่านคอมเมนท์จากไลฟ์ TikTok

ใช้ TikTokLive (อ่านอย่างเดียว ไม่ต้องล็อกอิน):
    pip install TikTokLive

ข้อจำกัดที่ต้องรู้ก่อนใช้:
- TikTok ไม่มี API สาธารณะสำหรับ "ส่ง" คอมเมนท์กลับเข้าไลฟ์
  ฉะนั้นส่วนตอบกลับต้องเลือกอย่างใดอย่างหนึ่ง:
  (ก) ให้ระบบร่างคำตอบแล้วคนกดส่ง (ปลอดภัยที่สุด, ใช้หน้า dashboard)
  (ข) ต่อกับเครื่องมือที่พิมพ์แทนคนได้ ซึ่งเสี่ยงผิดข้อตกลงการใช้งาน
  ค่าเริ่มต้นของระบบนี้คือ (ก)
- ถ้าโครงสร้างฝั่ง TikTok เปลี่ยน ไลบรารีอาจต้องอัปเดตตาม
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)


class TikTokCommentSource:
    """ดูดคอมเมนท์สดจากห้องไลฟ์"""

    def __init__(self, unique_id: str = "", reconnect_seconds: float = 10.0, **_: Any) -> None:
        self.unique_id = unique_id.lstrip("@")
        self.reconnect_seconds = reconnect_seconds
        self._handler = None
        self._running = False

    def set_handler(self, handler) -> None:
        self._handler = handler

    async def connect(self) -> bool:
        return bool(self.unique_id)

    async def disconnect(self) -> None:
        self._running = False

    async def run(self) -> None:
        try:
            from TikTokLive import TikTokLiveClient
            from TikTokLive.events import CommentEvent
        except ImportError:
            log.error("ต้องติดตั้ง TikTokLive ก่อน: pip install TikTokLive")
            return

        if not self.unique_id:
            log.error("ยังไม่ได้ตั้งค่า adapters.options.tiktok.unique_id")
            return

        self._running = True
        while self._running:
            client = TikTokLiveClient(unique_id=self.unique_id)

            @client.on(CommentEvent)
            async def _on_comment(event) -> None:  # pragma: no cover - ต้องต่อของจริง
                if self._handler is None:
                    return
                user = event.user
                self._handler(
                    {
                        "user_id": str(getattr(user, "unique_id", "") or getattr(user, "id", "")),
                        "nickname": str(getattr(user, "nickname", "") or "ผู้ชม"),
                        "text": str(event.comment or ""),
                        "is_follower": bool(getattr(user, "is_friend", False)),
                    }
                )

            try:
                await client.start()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("หลุดจากห้องไลฟ์ (%s) — ต่อใหม่ใน %.0f วิ", exc, self.reconnect_seconds)
            if self._running:
                await asyncio.sleep(self.reconnect_seconds)


class ReviewQueueSender:
    """คิวคำตอบรอให้คนกดส่ง — ทางเลือกที่ไม่เสี่ยงผิดข้อตกลง

    คำตอบจะโผล่บนหน้า dashboard พร้อมปุ่มคัดลอก
    คนแค่กดวางแล้ว Enter ซึ่งเร็วกว่าคิดเองเยอะ
    """

    def __init__(self, maxlen: int = 100, **_: Any) -> None:
        from collections import deque

        self.pending: deque[dict[str, Any]] = deque(maxlen=maxlen)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def send(self, text: str, *, reply_to: str = "") -> bool:
        import time

        self.pending.append({"ts": time.time(), "to": reply_to, "text": text})
        return True

    def drain(self) -> list[dict[str, Any]]:
        items = list(self.pending)
        self.pending.clear()
        return items
