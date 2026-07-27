"""ตัวส่งสถานะจากเครื่องลูกไปยัง hub

ออกแบบให้ล้มเหลวเงียบ ๆ: hub ล่มหรือเน็ตหลุด เครื่องลูกต้องไลฟ์ต่อได้ปกติ
การรายงานเป็นของแถม ไม่ใช่สิ่งที่ไลฟ์ต้องพึ่ง
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Callable

log = logging.getLogger(__name__)


class FleetReporter:
    """ส่ง snapshot เข้า hub เป็นระยะ"""

    def __init__(
        self,
        hub_url: str,
        machine: str,
        snapshot_fn: Callable[[], dict[str, Any]],
        interval: float = 5.0,
    ) -> None:
        self.hub_url = hub_url.rstrip("/")
        self.machine = machine
        self.snapshot_fn = snapshot_fn
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._stopping = asyncio.Event()
        self._fail_streak = 0

    def start(self) -> None:
        if not self.hub_url:
            return
        self._task = asyncio.create_task(self._loop(), name="fleet-reporter")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: B014
                pass
            self._task = None

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await asyncio.sleep(self.interval)
                await self._send_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.debug("รายงานเข้า hub ไม่สำเร็จ", exc_info=True)

    async def _send_once(self) -> None:
        payload = json.dumps(
            {"machine": self.machine, "snapshot": self.snapshot_fn()},
            ensure_ascii=False,
            default=str,
        ).encode()

        # urllib บล็อก จึงโยนไป thread แยกไม่ให้หน่วง event loop ของ agent
        ok = await asyncio.to_thread(self._post, payload)
        if ok:
            if self._fail_streak >= 3:
                log.info("กลับมาส่งสถานะเข้า hub ได้แล้ว")
            self._fail_streak = 0
        else:
            self._fail_streak += 1
            # เตือนครั้งเดียวตอนเริ่มมีปัญหา ไม่ต้องบ่นทุกรอบ
            if self._fail_streak == 3:
                log.warning(
                    "ส่งสถานะเข้า hub (%s) ไม่ได้ติดต่อกัน 3 ครั้ง — "
                    "ไลฟ์ยังทำงานปกติ แต่จอรวมจะไม่เห็นเครื่องนี้",
                    self.hub_url,
                )

    def _post(self, payload: bytes) -> bool:
        request = urllib.request.Request(
            f"{self.hub_url}/api/report",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False
