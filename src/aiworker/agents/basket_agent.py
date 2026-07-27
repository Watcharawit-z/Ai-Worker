"""พนักงานดูแลช่องและจัดการตะกร้า

ตรงกับสไลด์ทุกข้อ:
- จัดการตะกร้าเตรียมไว้ 10 ตะกร้า
- แจ้งพนักงานไลฟ์ว่าสินค้าที่จะไลฟ์คืออะไรตามลำดับคิว
- แจ้งพนักงานยิงแอดทุกครั้งที่ขึ้นตะกร้าใหม่  (ทำผ่าน event basket.activated)
- แก้ปัญหาช่องหากการไลฟ์มีการละเมิด            (รับ event compliance.action)
- สังเกตยอดขายภายใน 1 ชม. ว่าไปต่อได้มั้ย     (evaluate())
"""

from __future__ import annotations

from ..adapters.base import ShopAdapter
from ..domain.basket import Verdict
from ..events import (
    BasketActivated,
    BasketPerformance,
    BasketRotateRequest,
    ComplianceAction,
    Event,
    Severity,
)
from ..settings import Settings
from ..state import ShiftState
from .base import Agent


class BasketAgent(Agent):
    """แทนคนที่คุมคิวตะกร้าและตัดสินว่าสินค้าไหนไปต่อ"""

    name = "basket_manager"
    subscribes = ("basket.rotate_request", "compliance.action")

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        shop: ShopAdapter,
    ) -> None:
        super().__init__(bus, state, settings)
        self.shop = shop
        self.tick_interval = 30.0
        self._announced_upcoming: str = ""

    async def on_start(self) -> None:
        await self.shop.connect()
        await self._activate_next("เริ่มกะ")

    async def on_stop(self) -> None:
        await self.shop.disconnect()

    # ---------------- ตอบสนอง event ----------------

    async def handle(self, event: Event) -> None:
        if isinstance(event, BasketRotateRequest):
            await self._activate_next(f"{event.reason} (ขอโดย {event.requested_by})")
        elif isinstance(event, ComplianceAction) and event.action == "stop_stream":
            self.say("หยุดขายชั่วคราวตามคำสั่งฝ่ายตรวจการละเมิด", "error")

    # ---------------- งานตามรอบ ----------------

    async def tick(self) -> None:
        if self.state.compliance.paused:
            return

        await self._sync_sales()
        await self._announce_upcoming()

        verdict = self.state.baskets.evaluate(
            min_minutes=self.settings.baskets.min_minutes_per_basket,
            max_minutes=self.settings.baskets.max_minutes_per_basket,
            evaluate_after_minutes=self.settings.baskets.evaluate_after_minutes,
            min_orders=self.settings.baskets.min_orders_to_continue,
            target_roas=self.settings.ads.target_roas,
        )
        basket = self.state.baskets.live
        if basket is None:
            await self._activate_next("ยังไม่มีตะกร้าที่ไลฟ์อยู่")
            return

        self.emit(
            BasketPerformance(
                basket_id=basket.id,
                minutes_live=basket.minutes_live,
                orders=basket.orders,
                revenue=basket.revenue,
                verdict=verdict.value,
            )
        )

        if verdict is Verdict.OUT_OF_STOCK:
            await self._activate_next("สินค้าหมดสต็อก")
        elif verdict is Verdict.ROTATE and self.settings.baskets.rotate_on_poor_performance:
            reason = (
                f"ยอดขาย {basket.orders} ออเดอร์ใน {basket.minutes_live:.0f} นาที "
                f"ไม่ถึงเกณฑ์ {self.settings.baskets.min_orders_to_continue}"
            )
            await self._activate_next(reason)
        elif verdict is Verdict.SCALE:
            self.say(
                f"'{basket.name}' ตะกร้าติดแล้ว ({basket.orders} ออเดอร์) — แจ้งฝ่ายแอดให้สเกล"
            )

    async def _sync_sales(self) -> None:
        """ดึงยอดขายล่าสุดจากร้าน"""
        basket = self.state.baskets.live
        if basket is None:
            return
        sales = await self.shop.fetch_sales(basket.id)
        if sales is None:
            return
        new_orders = max(0, sales.orders - basket.orders)
        new_revenue = max(0.0, sales.revenue - basket.revenue)
        if new_orders or new_revenue:
            self.state.baskets.record_sale(new_orders, new_revenue)
        if sales.stock is not None:
            basket.stock = sales.stock

    async def _announce_upcoming(self) -> None:
        """แจ้งล่วงหน้าว่าคิวถัดไปคือสินค้าอะไร — งานที่คนต้องทำในสไลด์"""
        upcoming = self.state.baskets.upcoming(2)
        if not upcoming:
            return
        key = ",".join(b.id for b in upcoming)
        if key == self._announced_upcoming:
            return
        self._announced_upcoming = key
        names = " → ".join(f"{b.name} ({b.price:.0f}฿)" for b in upcoming)
        self.say(f"คิวถัดไป: {names}")

    # ---------------- เปลี่ยนตะกร้า ----------------

    async def _activate_next(self, reason: str) -> None:
        basket = self.state.baskets.activate_next(reason=reason)
        if basket is None:
            self.say("ตะกร้าในคิวหมดแล้ว — ต้องเติมสินค้าหรือปิดกะ", "warn")
            self.notify(
                "ตะกร้าหมดคิว",
                "เตรียมตะกร้าใหม่หรือสั่งปิดกะได้เลย",
                Severity.HIGH,
                needs_human=True,
            )
            return

        ok = await self.shop.set_active_basket(basket.id, basket.sku)
        if not ok:
            self.say(f"ขึ้นตะกร้า '{basket.name}' ในระบบร้านไม่สำเร็จ", "error")
            return

        queue = self.state.baskets
        self.emit(
            BasketActivated(
                basket_id=basket.id,
                name=basket.name,
                sku=basket.sku,
                price=basket.price,
                position_in_queue=queue.position,
                queue_size=len(queue.baskets),
            )
        )
        self.say(
            f"ขึ้นตะกร้าที่ {queue.position}/{len(queue.baskets)}: "
            f"{basket.name} ({basket.price:.0f}฿) — {reason}"
        )
