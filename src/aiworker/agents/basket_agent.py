"""พนักงานดูแลช่องและปักตะกร้า

**หลักการเดียวที่ต้องจำ: ตะกร้าเดินตามปาก ไม่ได้เดินตามนาฬิกา**

รีรันคือการเปิดคลิปที่อัดไว้ คนในคลิปพูดถึงสินค้าตัวไหนอยู่
เราต้องปักตะกร้าตัวนั้นขึ้นแสดงให้ตรงกัน ถ้าปักผิด คนดูกดสั่งผิดตัว
หรือหาของที่พูดถึงไม่เจอ แล้วก็ออกจากไลฟ์ไป

ระบบจึงไม่หมุนตะกร้าตามเวลาหรือตามยอดขาย — คลิปเป็นคนกำหนด เราแค่ตามให้ทัน
(ถ้าอยากได้ตรรกะ 'ขายไม่ดีให้เปลี่ยนสินค้า' นั่นคือไลฟ์สด ไม่ใช่รีรัน)
"""

from __future__ import annotations

import time

from ..adapters.base import ShopAdapter
from ..domain.basket import Basket
from ..events import BasketPinned, Event, SegmentChanged, Severity
from ..settings import Settings
from ..state import ShiftState
from .base import Agent


class BasketAgent(Agent):
    """ปักตะกร้าให้ตรงกับสิ่งที่คนในคลิปกำลังนำเสนอ"""

    name = "basket_manager"
    subscribes = ("stream.segment_changed",)

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        shop: ShopAdapter,
    ) -> None:
        super().__init__(bus, state, settings)
        self.shop = shop
        self.tick_interval = settings.baskets.pin_check_seconds
        self._pinned_key: tuple[str, str] | None = None
        """(ท่อน, sku) ที่ปักไปแล้ว — กันปักซ้ำเมื่อ tick กับ event มาพร้อมกัน"""

        self._warned_missing: set[str] = set()
        self._last_pin_at = 0.0

    async def on_start(self) -> None:
        await self.shop.connect()
        self._index_baskets()

    async def on_stop(self) -> None:
        await self.shop.disconnect()

    # ---------------- ตอบสนอง event ----------------

    async def handle(self, event: Event) -> None:
        if isinstance(event, SegmentChanged):
            # เปลี่ยนท่อนแล้ว ปักใหม่ทันทีไม่ต้องรอ tick ถัดไป
            await self._sync_pin(force=True)

    async def tick(self) -> None:
        await self._sync_pin()
        await self._sync_sales()

    async def _sync_sales(self) -> None:
        """ดึงยอดขายของตะกร้าที่ปักอยู่มาบันทึก"""
        pinned = self.state.baskets.pinned
        if pinned is None:
            return
        sales = await self.shop.fetch_sales(pinned.id)
        if sales is None:
            return
        new_orders = max(0, sales.orders - pinned.orders)
        new_revenue = max(0.0, sales.revenue - pinned.revenue)
        if new_orders or new_revenue:
            self.state.baskets.record_sale(pinned.sku, new_orders, new_revenue)
        if sales.stock is not None:
            pinned.stock = sales.stock
            if sales.stock == 0:
                self._warn_out_of_stock(pinned)

    def _warn_out_of_stock(self, basket: Basket) -> None:
        if basket.id in self._warned_missing:
            return
        self._warned_missing.add(basket.id)
        self.say(f"'{basket.name}' ของหมดแล้วแต่ยังปักอยู่", "warn")
        self.notify(
            "สินค้าที่ปักอยู่ของหมด",
            f"{basket.name} หมดสต็อกแล้ว แต่คลิปยังพูดถึงอยู่\n"
            "เติมสต็อกหรือเอาตะกร้าออกจากไลฟ์",
            Severity.HIGH,
            needs_human=True,
        )

    # ---------------- หัวใจ ----------------

    async def _sync_pin(self, *, force: bool = False) -> None:
        """เช็คว่าตะกร้าที่ปักอยู่ ตรงกับที่คนในคลิปพูดถึงหรือยัง"""
        segment = self.state.playlist.current
        if segment is None:
            return

        position = self._playback_position(segment.duration_seconds)
        lead = self.settings.baskets.pin_lead_seconds
        wanted_sku = segment.sku_at(position + lead)

        if not wanted_sku:
            return

        # ท่อนใหม่ต้องปักใหม่เสมอ แม้สินค้าจะเป็นตัวเดิม (กันร้านเผลอเปลี่ยนสถานะ)
        # แต่ถ้าท่อนเดิม+สินค้าเดิม ไม่ต้องทำอะไร ต่อให้ถูกสั่ง force มา
        key = (segment.id, wanted_sku)
        if key == self._pinned_key:
            return

        basket = self.state.baskets.by_sku(wanted_sku)
        if basket is None:
            self._warn_missing(wanted_sku, segment.id, position)
            return

        ok = await self.shop.pin_basket(basket.id, basket.sku, basket.name)
        if not ok:
            self.say(f"ปักตะกร้า '{basket.name}' ไม่สำเร็จ", "error")
            return

        previous = self._pinned_key[1] if self._pinned_key else None
        self._pinned_key = key
        self._last_pin_at = time.time()
        self.state.baskets.mark_pinned(basket.id)

        reason = (
            "เริ่มท่อนใหม่"
            if previous is None or force
            else f"คนในคลิปเปลี่ยนไปพูดถึง {basket.name}"
        )
        self.emit(
            BasketPinned(
                basket_id=basket.id,
                name=basket.name,
                sku=basket.sku,
                price=basket.price,
                reason=reason,
                segment=segment.id,
            )
        )
        arrow = f" (จากเดิม {previous})" if previous else ""
        self.say(
            f"ปักตะกร้า '{basket.name}' ({basket.price:.0f}฿){arrow} "
            f"— นาทีที่ {position / 60:.1f} ของท่อน {segment.id}"
        )

        # มองจากจุดที่ใช้ตัดสินใจ ไม่ใช่จากตำแหน่งจริง
        # ไม่งั้นจะได้ cue ตัวที่เพิ่งปักไปแล้ว แล้วประกาศว่า "อีก 0 นาทีจะเปลี่ยนไปตัวเดิม"
        upcoming = segment.cues.next_change(position + lead)
        if upcoming is not None and upcoming.sku != wanted_sku:
            wait = max(0.0, upcoming.at_seconds - position)
            nxt = self.state.baskets.by_sku(upcoming.sku)
            if nxt is not None:
                self.say(f"อีก {wait / 60:.1f} นาทีจะเปลี่ยนไป '{nxt.name}'")

    SETTLE_SECONDS = 60.0
    """ช่วงหลังเปลี่ยนท่อนที่ค่าจากตัวเล่นอาจยังเป็นของท่อนเก่า"""

    STALE_GAP_SECONDS = 30.0
    """ห่างจากเวลาจริงเกินนี้ในช่วง settle = ถือว่าเป็นค่าค้าง"""

    def _playback_position(self, duration: float) -> float:
        """เล่นคลิปมาถึงวินาทีที่เท่าไหร่แล้ว

        ปกติเชื่อค่าจากตัวเล่น เพราะแม่นกว่าการจับเวลาเอง (คลิป buffer/กระตุกได้)
        ยกเว้นช่วงไม่กี่สิบวินาทีแรกหลังเปลี่ยนท่อน ที่ค่านั้นอาจยังเป็นของท่อนเก่า
        ค้างอยู่หนึ่งรอบ — เชื่อตอนนั้นจะได้ตำแหน่งผิดไปหลายนาทีแล้วปักตะกร้าผิดตัว
        """
        elapsed = max(0.0, time.time() - self.state.stream.segment_started_at)
        if duration > 0:
            elapsed = min(elapsed, duration)

        remaining = self.state.stream.seconds_remaining
        if duration <= 0 or remaining <= 0:
            return elapsed

        from_player = max(0.0, duration - remaining)
        settling = elapsed < self.SETTLE_SECONDS
        looks_stale = abs(from_player - elapsed) > self.STALE_GAP_SECONDS
        return elapsed if (settling and looks_stale) else from_player

    def _warn_missing(self, sku: str, segment_id: str, position: float) -> None:
        """คลิปพูดถึงสินค้าที่ไม่มีในตะกร้า — ปักไม่ได้ ต้องบอกคน"""
        if not self.settings.baskets.alert_on_missing_sku:
            return
        if sku in self._warned_missing:
            return
        self._warned_missing.add(sku)
        self.say(
            f"คลิปพูดถึง {sku} แต่ไม่มีตะกร้าตัวนี้ในไลฟ์ — ปักไม่ได้ "
            f"(ท่อน {segment_id} นาทีที่ {position / 60:.1f})",
            "error",
        )
        self.notify(
            "คลิปพูดถึงสินค้าที่ไม่มีในตะกร้า",
            f"SKU {sku} ถูกพูดถึงในท่อน {segment_id} แต่ไม่มีในรายการตะกร้า\n"
            "คนดูจะหาของไม่เจอ — เพิ่มตะกร้าหรือแก้คิวชีต",
            Severity.HIGH,
            needs_human=True,
        )

    def _index_baskets(self) -> None:
        count = len(self.state.baskets.baskets)
        skus = ", ".join(b.sku for b in self.state.baskets.baskets[:5])
        self.say(f"โหลดตะกร้าในไลฟ์ {count} ใบ ({skus}…)")
