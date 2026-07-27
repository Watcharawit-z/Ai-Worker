"""พนักงานดูแลแอด

ตรงกับสไลด์:
- ขึ้นแอดใหม่ทุกครั้งที่ขึ้นตะกร้าใหม่
- ดูว่าสินค้าที่ไลฟ์อยู่ค่าแอดแพงไหม ไปต่อได้หรือเปล่า
- สเกลแอดเมื่อเจอตะกร้าติดแล้ว

ตรรกะการตัดสินใจอยู่ในโค้ดล้วน ไม่เรียก LLM — เรื่องเงินต้องคาดเดาได้
"""

from __future__ import annotations

from ..adapters.base import AdsAdapter
from ..events import (
    AdAction,
    AdMetrics,
    BasketActivated,
    BasketPerformance,
    ComplianceAction,
    Event,
    Severity,
)
from ..settings import Settings
from ..state import ShiftState
from .base import Agent


class AdsAgent(Agent):
    """แทนคนยิงแอด"""

    name = "ads_manager"
    subscribes = ("basket.activated", "basket.performance", "compliance.action")

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        ads: AdsAdapter,
    ) -> None:
        super().__init__(bus, state, settings)
        self.ads = ads
        self.tick_interval = settings.ads.check_interval_seconds
        self._scaled_for_basket: set[str] = set()

    async def on_start(self) -> None:
        await self.ads.connect()

    async def on_stop(self) -> None:
        await self.ads.disconnect()

    # ---------------- ตอบสนอง event ----------------

    async def handle(self, event: Event) -> None:
        if isinstance(event, BasketActivated):
            await self._launch(event)
        elif isinstance(event, BasketPerformance):
            await self._on_performance(event)
        elif isinstance(event, ComplianceAction) and event.action == "stop_stream":
            await self._pause_all("หยุดตามคำสั่งฝ่ายตรวจการละเมิด")

    async def _launch(self, event: BasketActivated) -> None:
        """ขึ้นแอดใหม่ทุกครั้งที่ขึ้นตะกร้าใหม่"""
        if not self.settings.ads.enabled:
            return

        old = self.state.ads.campaign_id
        if old:
            await self.ads.pause_campaign(old)
            self.say(f"ปิดแคมเปญเดิม {old}")

        budget = self.settings.ads.starting_budget
        campaign = await self.ads.create_campaign(
            basket_id=event.basket_id, sku=event.sku, budget=budget
        )
        if campaign is None:
            self.say("สร้างแคมเปญไม่สำเร็จ", "error")
            self.notify(
                "ยิงแอดไม่สำเร็จ",
                f"ตะกร้า {event.name} ขึ้นแล้วแต่ระบบแอดไม่ตอบสนอง",
                Severity.HIGH,
                needs_human=True,
            )
            return

        self.state.ads = type(self.state.ads)(campaign_id=campaign, budget=budget)
        self.state.ads.last_action = "launched"
        self.emit(
            AdAction(
                action="launch",
                campaign_id=campaign,
                basket_id=event.basket_id,
                budget=budget,
                reason=f"ขึ้นตะกร้าใหม่: {event.name}",
            )
        )
        self.say(f"ขึ้นแอดใหม่ให้ '{event.name}' งบเริ่ม {budget:.0f}฿ (แคมเปญ {campaign})")

    async def _on_performance(self, event: BasketPerformance) -> None:
        if event.verdict == "scale" and event.basket_id not in self._scaled_for_basket:
            self._scaled_for_basket.add(event.basket_id)
            await self._scale("ตะกร้าติดแล้ว — ฝ่ายตะกร้าแจ้งมา")

    # ---------------- งานตามรอบ ----------------

    async def tick(self) -> None:
        if not self.settings.ads.enabled or not self.state.ads.campaign_id:
            return

        metrics = await self.ads.fetch_metrics(self.state.ads.campaign_id)
        if metrics is None:
            return

        ads = self.state.ads
        spend_delta = max(0.0, metrics.spend - ads.spend)
        ads.spend = metrics.spend
        ads.orders = metrics.orders
        ads.revenue = metrics.revenue
        if spend_delta:
            self.state.baskets.record_ad_spend(spend_delta)

        basket = self.state.baskets.live
        self.emit(
            AdMetrics(
                campaign_id=ads.campaign_id,
                basket_id=basket.id if basket else "",
                spend=ads.spend,
                orders=ads.orders,
                revenue=ads.revenue,
                cpa=ads.cpa,
                roas=ads.roas,
            )
        )

        await self._decide()

    async def _decide(self) -> None:
        """ค่าแอดแพงไปไหม ไปต่อได้รึเปล่า"""
        cfg = self.settings.ads
        ads = self.state.ads

        # ยังใช้เงินน้อยเกินไป ตัวเลขยังไม่มีความหมาย
        if ads.spend < cfg.min_spend_before_judging:
            return

        if ads.roas < cfg.kill_roas:
            await self._kill(
                f"ROAS {ads.roas:.2f} ต่ำกว่าเกณฑ์ {cfg.kill_roas} "
                f"(ใช้ไป {ads.spend:.0f}฿ ได้กลับ {ads.revenue:.0f}฿)"
            )
        elif ads.roas >= cfg.target_roas and ads.budget < cfg.max_budget:
            await self._scale(f"ROAS {ads.roas:.2f} ทะลุเป้า {cfg.target_roas}")

    async def _scale(self, reason: str) -> None:
        cfg = self.settings.ads
        ads = self.state.ads
        if not ads.campaign_id or ads.budget >= cfg.max_budget:
            return

        new_budget = min(cfg.max_budget, ads.budget * cfg.scale_step)
        if new_budget <= ads.budget:
            return

        ok = await self.ads.set_budget(ads.campaign_id, new_budget)
        if not ok:
            self.say("ปรับงบแอดไม่สำเร็จ", "warn")
            return

        ads.budget = new_budget
        ads.last_action = "scaled"
        self.emit(
            AdAction(
                action="scale",
                campaign_id=ads.campaign_id,
                budget=new_budget,
                reason=reason,
            )
        )
        self.say(f"สเกลแอดขึ้นเป็น {new_budget:.0f}฿ — {reason}")

    async def _kill(self, reason: str) -> None:
        ads = self.state.ads
        if not ads.campaign_id:
            return
        await self.ads.pause_campaign(ads.campaign_id)
        ads.last_action = "killed"
        self.emit(
            AdAction(action="kill", campaign_id=ads.campaign_id, reason=reason)
        )
        self.say(f"ปิดแอด — {reason}", "warn")
        self.notify(
            "ปิดแอดเพราะไม่คุ้ม",
            f"{reason}\nแนะนำให้เปลี่ยนตะกร้าหรือปรับครีเอทีฟ",
            Severity.MEDIUM,
        )

    async def _pause_all(self, reason: str) -> None:
        if self.state.ads.campaign_id:
            await self.ads.pause_campaign(self.state.ads.campaign_id)
            self.state.ads.last_action = "paused"
            self.say(f"พักแอดทั้งหมด — {reason}", "warn")
