"""หน้าตาของ adapter ทุกตัว

Adapter คือจุดที่ระบบไปแตะโลกจริง (โปรแกรมสตรีม, TikTok, ระบบแอด)
ถ้าจะต่อของจริง เขียนคลาสใหม่ให้ครบเมธอดตามนี้แล้วลงทะเบียนใน registry.py
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..domain.segments import Segment


@dataclass(slots=True)
class HealthSnapshot:
    is_live: bool = False
    bitrate_kbps: int = 0
    dropped_frames: int = 0
    fps: float = 0.0
    viewers: int = 0
    current_segment: str | None = None
    seconds_remaining: float = 0.0


@dataclass(slots=True)
class SalesSnapshot:
    orders: int = 0
    revenue: float = 0.0
    stock: int | None = None


@dataclass(slots=True)
class AdMetricsSnapshot:
    spend: float = 0.0
    orders: int = 0
    revenue: float = 0.0


class Adapter(Protocol):
    async def connect(self) -> bool: ...
    async def disconnect(self) -> None: ...


@runtime_checkable
class PlayerAdapter(Adapter, Protocol):
    """ควบคุมโปรแกรมที่เล่นวิดีโอออกอากาศ (OBS / vMix / อื่น ๆ)"""

    async def play_segment(self, segment: Segment) -> bool: ...
    async def start(self) -> bool: ...
    async def stop(self) -> bool: ...
    async def health(self) -> HealthSnapshot: ...


@runtime_checkable
class CommentSource(Adapter, Protocol):
    """แหล่งคอมเมนท์ที่ไหลเข้ามา"""

    def set_handler(self, handler) -> None: ...
    async def run(self) -> None: ...


@runtime_checkable
class CommentSender(Adapter, Protocol):
    """ช่องทางส่งคำตอบกลับเข้าไลฟ์"""

    async def send(self, text: str, *, reply_to: str = "") -> bool: ...


@runtime_checkable
class ShopAdapter(Adapter, Protocol):
    """ระบบร้าน — ปักตะกร้าขึ้นแสดงและอ่านยอดขาย"""

    async def pin_basket(self, basket_id: str, sku: str) -> bool:
        """ปักตะกร้าใบนี้ขึ้นแสดงในไลฟ์ (ตะกร้าใบอื่นยังอยู่ในไลฟ์เหมือนเดิม)"""
        ...

    async def fetch_sales(self, basket_id: str) -> SalesSnapshot | None: ...


@runtime_checkable
class ScreenWatcher(Protocol):
    """อ่านหน้าจอเพื่อดูว่าปริศนายืนยันตัวตนโผล่หรือยัง — อ่านอย่างเดียว"""

    async def connect(self) -> bool: ...
    async def disconnect(self) -> None: ...
    async def read(self) -> Any: ...


@runtime_checkable
class AdsAdapter(Adapter, Protocol):
    """ระบบแอด"""

    async def create_campaign(
        self,
        *,
        basket_id: str,
        sku: str,
        budget: float,
        campaign_type: str = "gmv_max_live",
    ) -> str | None: ...
    async def set_budget(self, campaign_id: str, budget: float) -> bool: ...
    async def pause_campaign(self, campaign_id: str) -> bool: ...
    async def fetch_metrics(self, campaign_id: str) -> AdMetricsSnapshot | None: ...


@runtime_checkable
class NotifyChannel(Protocol):
    """ช่องทางแจ้งเตือนคน"""

    async def send(self, title: str, body: str, severity: str) -> bool: ...
