"""พนักงานดูแลแอด — GMV Max Live สำหรับไลฟ์นายหน้า

ต่างจากไลฟ์ขายของตัวเองตรงที่ **กำไรต่อชิ้นบาง** ตัวชี้ขาดจึงไม่ใช่ยอดขายรวม
แต่คือ **ต้นทุนต่อการซื้อ (CPA)** ถ้าค่าแอดต่อออเดอร์แพงเกิน ยิ่งขายยิ่งขาดทุน

กฎที่ต่างจากไลฟ์ทั่วไป:
- แอดผูกกับ "ไลฟ์" ไม่ได้ผูกกับ "ตะกร้า"
  เปลี่ยนตะกร้าที่ปักไม่ต้องแตะแอด — ขึ้นไลฟ์ใหม่ถึงจะขึ้นแอดใหม่
- แอดของไลฟ์ที่รันอยู่แพงขึ้นเรื่อย ๆ → ปิดตัวเก่า ขึ้นตัวใหม่ในไลฟ์เดิม
- ตรรกะทั้งหมดเป็นโค้ด ไม่เรียก AI เพราะเรื่องเงินต้องคาดเดาได้และอธิบายได้

หมายเหตุเรื่อง GMV Max Live: ระบบไม่ได้เลือก audience หรือ bid เอง
เพราะแคมเปญประเภทนี้ TikTok คุมการกระจายให้เองอยู่แล้ว
สิ่งที่เราคุมได้จริงมีสามอย่าง — เปิด, ปรับงบ, ปิด — ระบบจึงทำแค่สามอย่างนี้
"""

from __future__ import annotations

import time

from ..adapters.base import AdsAdapter
from ..events import AdAction, AdMetrics, ComplianceAction, Event, Severity
from ..settings import Settings
from ..state import ShiftState
from .base import Agent


class AdsAgent(Agent):
    """คุมต้นทุนการซื้อของไลฟ์ที่กำลังรันอยู่"""

    name = "ads_manager"
    subscribes = ("compliance.action",)

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
        self._launched_at = 0.0
        self._killed_at = 0.0
        self._relaunches = 0
        self._halted = False
        """ขาดทุนถึงเพดานแล้ว — ไม่ยิงแอดอีกจนกว่าจะเริ่มไลฟ์ใหม่"""

    async def on_start(self) -> None:
        await self.ads.connect()
        # ขึ้นไลฟ์ใหม่ = ขึ้นแอดใหม่หนึ่งครั้ง
        await self._launch("เริ่มไลฟ์ใหม่")

    async def on_stop(self) -> None:
        if self.state.ads.campaign_id:
            await self.ads.pause_campaign(self.state.ads.campaign_id)
            self.say("ปิดแอดตอนจบไลฟ์")
        await self.ads.disconnect()

    # ---------------- ตอบสนอง event ----------------

    async def handle(self, event: Event) -> None:
        if isinstance(event, ComplianceAction) and event.action == "stop_stream":
            await self._pause("หยุดตามคำสั่งฝ่ายตรวจการละเมิด")

    # ---------------- เปิด / ปิด ----------------

    async def _launch(self, reason: str) -> None:
        cfg = self.settings.ads
        if not cfg.enabled:
            self.say("ปิดการยิงแอดอัตโนมัติไว้ใน config")
            return
        if self._halted:
            return

        campaign = await self.ads.create_campaign(
            basket_id="",
            sku="",
            budget=cfg.starting_budget,
            campaign_type=cfg.campaign_type,
        )
        if campaign is None:
            self.say("สร้างแคมเปญไม่สำเร็จ", "error")
            self.notify(
                "ยิงแอดไม่สำเร็จ",
                "ไลฟ์เริ่มแล้วแต่ระบบแอดไม่ตอบสนอง — ต้องขึ้นแอดเองด่วน",
                Severity.HIGH,
                needs_human=True,
            )
            return

        ads = self.state.ads
        ads.campaign_id = campaign
        ads.budget = cfg.starting_budget
        ads.spend = 0.0
        ads.orders = 0
        ads.revenue = 0.0
        ads.last_action = "launched"
        self._launched_at = time.time()

        self.emit(
            AdAction(
                action="launch",
                campaign_id=campaign,
                budget=cfg.starting_budget,
                reason=f"{reason} ({cfg.campaign_type})",
            )
        )
        self.say(
            f"ขึ้นแอด {cfg.campaign_type} งบ {cfg.starting_budget:.0f}฿ "
            f"(แคมเปญ {campaign}) — {reason} | "
            f"เพดานค่าแอด {self._max_cpa():.0f}฿/ออเดอร์ "
            f"จากค่าคอม {self._commission():.0f}฿/ชิ้น"
        )

    async def _pause(self, reason: str) -> None:
        if not self.state.ads.campaign_id:
            return
        await self.ads.pause_campaign(self.state.ads.campaign_id)
        self.state.ads.last_action = "paused"
        self.say(f"พักแอด — {reason}", "warn")

    # ---------------- งานตามรอบ ----------------

    async def tick(self) -> None:
        if not self.settings.ads.enabled or self._halted:
            return
        if not self.state.ads.campaign_id:
            return

        metrics = await self.ads.fetch_metrics(self.state.ads.campaign_id)
        if metrics is None:
            return

        ads = self.state.ads
        ads.spend = metrics.spend
        ads.orders = metrics.orders
        ads.revenue = metrics.revenue

        self.emit(
            AdMetrics(
                campaign_id=ads.campaign_id,
                spend=ads.spend,
                orders=ads.orders,
                revenue=ads.revenue,
                cpa=ads.cpa,
                roas=ads.roas,
            )
        )
        await self._decide()

    # ---------------- เพดานที่คิดจากค่าคอมจริง ----------------

    def _commission(self) -> float:
        """ค่าคอมเฉลี่ยต่อชิ้นของสินค้าที่ขายอยู่จริงในไลฟ์นี้"""
        return self.state.baskets.blended_commission(self.settings.ads.default_commission)

    def _max_cpa(self) -> float:
        """จ่ายค่าแอดต่อออเดอร์ได้สูงสุดเท่าไหร่ถึงจะยังไม่ขาดทุน"""
        cfg = self.settings.ads
        if cfg.max_cpa_override > 0:
            return cfg.max_cpa_override
        return self._commission() * cfg.cpa_ceiling_ratio

    def _target_cpa(self) -> float:
        cfg = self.settings.ads
        if cfg.max_cpa_override > 0:
            return cfg.max_cpa_override * (cfg.cpa_target_ratio / cfg.cpa_ceiling_ratio)
        return self._commission() * cfg.cpa_target_ratio

    def _net_profit(self) -> float:
        """กำไรจริงของไลฟ์นี้ = ค่าคอมที่ได้ - ค่าแอดที่จ่าย

        ยอดขายไม่เกี่ยว เพราะนายหน้าไม่ได้เงินจากยอดขาย ได้จากค่าคอม
        """
        return self.state.baskets.commission_earned() - self.state.ads.spend

    async def _decide(self) -> None:
        """ค่าแอดต่อออเดอร์แพงเกินค่าคอมที่ได้หรือยัง"""
        cfg = self.settings.ads
        ads = self.state.ads

        # เบรกฉุกเฉิน — ตรวจก่อนทุกอย่าง ไม่มีช่วงผ่อนผัน
        # ขาดทุนสะสมถึงเพดานแล้วต้องหยุด ไม่ว่าเหตุผลอื่นจะว่ายังไง
        net = self._net_profit()
        if net <= -cfg.max_loss_baht:
            await self._emergency_stop(net)
            return

        # แอดเพิ่งขึ้น ตัวเลขยังไม่นิ่ง
        if (time.time() - self._launched_at) / 60.0 < cfg.grace_minutes_after_launch:
            return
        if ads.spend < cfg.min_spend_before_judging:
            return

        max_cpa = self._max_cpa()
        commission = self._commission()

        # ใช้เงินไปเยอะแล้วแต่ยังไม่มีออเดอร์เลย = แพงที่สุดเท่าที่จะเป็นไปได้
        if ads.orders < cfg.min_purchases_before_judging:
            if ads.spend >= max_cpa * cfg.min_purchases_before_judging:
                await self._replace(
                    f"ใช้ไป {ads.spend:.0f}฿ ได้แค่ {ads.orders} ออเดอร์ "
                    f"(ยังไงก็เกินเพดาน {max_cpa:.0f}฿ จากค่าคอม {commission:.0f}฿)"
                )
            return

        if ads.cpa > max_cpa:
            await self._replace(
                f"ค่าแอด {ads.cpa:.0f}฿/ออเดอร์ เกินเพดาน {max_cpa:.0f}฿ "
                f"(ค่าคอมได้แค่ {commission:.0f}฿/ชิ้น = ขาดทุน "
                f"{ads.cpa - commission:.0f}฿ ต่อออเดอร์)"
            )
        elif ads.cpa <= self._target_cpa() and ads.budget < cfg.max_budget:
            await self._scale(
                f"ค่าแอด {ads.cpa:.0f}฿/ออเดอร์ จากค่าคอม {commission:.0f}฿ "
                f"— เหลือกำไร {commission - ads.cpa:.0f}฿ ต่อออเดอร์"
            )

    async def _emergency_stop(self, net: float) -> None:
        """หยุดยิงแอดถาวรในไลฟ์นี้ — ขาดทุนสะสมถึงเพดานแล้ว"""
        if self._halted:
            return
        self._halted = True
        campaign = self.state.ads.campaign_id
        if campaign:
            await self.ads.pause_campaign(campaign)
        self.state.ads.campaign_id = ""
        self.state.ads.last_action = "halted"
        self.emit(
            AdAction(
                action="halt",
                campaign_id=campaign,
                reason=f"ขาดทุนสะสม {abs(net):.0f}฿",
            )
        )
        self.say(
            f"หยุดยิงแอดทั้งหมด — ขาดทุนสะสม {abs(net):.0f}฿ "
            f"(ค่าคอมได้ {self.state.baskets.commission_earned():.0f}฿ "
            f"ค่าแอดจ่าย {self.state.ads.spend:.0f}฿)",
            "error",
        )
        self.notify(
            "หยุดยิงแอดฉุกเฉิน — ขาดทุนถึงเพดาน",
            f"ขาดทุนสะสมในไลฟ์นี้ {abs(net):.0f}฿ ถึงเพดานที่ตั้งไว้\n"
            f"ค่าคอมที่ได้ {self.state.baskets.commission_earned():.0f}฿ "
            f"แต่จ่ายค่าแอดไป {self.state.ads.spend:.0f}฿\n"
            "ไลฟ์ยังรันต่อแบบไม่มีแอด (ยอด organic ยังเข้าได้)",
            Severity.CRITICAL,
            needs_human=True,
        )

    async def _scale(self, reason: str) -> None:
        cfg = self.settings.ads
        ads = self.state.ads
        new_budget = min(cfg.max_budget, ads.budget * cfg.scale_step)
        if new_budget <= ads.budget:
            return

        if not await self.ads.set_budget(ads.campaign_id, new_budget):
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
        self.say(f"สเกลงบเป็น {new_budget:.0f}฿ — {reason}")

    async def _replace(self, reason: str) -> None:
        """ปิดแอดที่แพง แล้วขึ้นตัวใหม่ในไลฟ์เดิม"""
        cfg = self.settings.ads
        ads = self.state.ads

        cooldown_left = (
            cfg.relaunch_cooldown_minutes - (time.time() - self._killed_at) / 60.0
        )
        if self._killed_at and cooldown_left > 0:
            return  # เพิ่งเปลี่ยนไป รอให้ครบ cooldown ก่อน

        old = ads.campaign_id
        await self.ads.pause_campaign(old)
        ads.last_action = "killed"
        self._killed_at = time.time()
        self.emit(AdAction(action="kill", campaign_id=old, reason=reason))
        self.say(f"ปิดแอด {old} — {reason}", "warn")

        if self._relaunches >= cfg.max_relaunches_per_live:
            ads.campaign_id = ""
            self.say(
                f"เปลี่ยนแอดครบ {cfg.max_relaunches_per_live} ครั้งแล้วยังแพงอยู่ "
                "— หยุดยิงอัตโนมัติ รอคนตัดสินใจ",
                "error",
            )
            self.notify(
                "แอดแพงต่อเนื่อง หยุดยิงอัตโนมัติแล้ว",
                f"{reason}\n"
                f"เปลี่ยนแอดไปแล้ว {self._relaunches} ครั้งในไลฟ์นี้ ยังไม่ดีขึ้น\n"
                "น่าจะเป็นที่คลิปหรือตัวสินค้า ไม่ใช่ที่แอด",
                Severity.HIGH,
                needs_human=True,
            )
            return

        self._relaunches += 1
        await self._launch(f"ขึ้นแทนตัวที่แพง (ครั้งที่ {self._relaunches})")
        self.notify(
            "เปลี่ยนแอดเพราะต้นทุนการซื้อสูงเกิน",
            f"{reason}\nขึ้นแคมเปญใหม่แล้ว งบเริ่ม {cfg.starting_budget:.0f}฿",
            Severity.MEDIUM,
        )
