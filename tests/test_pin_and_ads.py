"""เทสสองเรื่องที่แก้ผิดแล้วเสียเงินจริง:
1. ปักตะกร้าให้ตรงกับสิ่งที่คนในคลิปพูด
2. แอดผูกกับไลฟ์ ไม่ใช่ตะกร้า และคุมต้นทุนต่อการซื้อ
"""

from __future__ import annotations

import asyncio
import time

import pytest

from aiworker.adapters.base import AdMetricsSnapshot, SalesSnapshot
from aiworker.agents.ads_agent import AdsAgent
from aiworker.agents.basket_agent import BasketAgent
from aiworker.bus import EventBus
from aiworker.domain.basket import BasketBoard
from aiworker.domain.segments import SegmentPlaylist
from aiworker.events import BasketPinned, SegmentChanged
from aiworker.settings import Settings
from aiworker.state import ShiftState

BASKETS = [
    # ค่าคอมนายหน้าจริง 10-40 บาท/ชิ้น — ตัวเลขนี้กำหนดเพดานค่าแอดทั้งหมด
    {"id": "b1", "name": "เซรั่ม", "sku": "S1", "price": 390, "commission": 40, "stock": 100},
    {"id": "b2", "name": "กันแดด", "sku": "S2", "price": 290, "commission": 20, "stock": 100},
    {"id": "b3", "name": "ลิป", "sku": "S3", "price": 199, "commission": 10, "stock": 100},
]

LONG_CLIP = [
    {
        "id": "full",
        "path": "/m/full.mp4",
        "duration_seconds": 3600,
        "cues": [
            {"at_seconds": 0, "sku": "S1"},
            {"at_seconds": 1200, "sku": "S2"},
            {"at_seconds": 2400, "sku": "S3"},
        ],
    }
]


class FakeShop:
    def __init__(self) -> None:
        self.pins: list[str] = []
        self.sales = SalesSnapshot(orders=0, revenue=0.0, stock=None)
        self.fail_next = False

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def pin_basket(self, basket_id: str, sku: str) -> bool:
        if self.fail_next:
            self.fail_next = False
            return False
        self.pins.append(sku)
        return True

    async def fetch_sales(self, basket_id: str):
        return self.sales


class FakeAds:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.paused: list[str] = []
        self.budgets: list[float] = []
        self.metrics = AdMetricsSnapshot()

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def create_campaign(self, *, basket_id, sku, budget, campaign_type="gmv_max_live"):
        cid = f"cmp{len(self.created) + 1}"
        self.created.append({"id": cid, "budget": budget, "type": campaign_type})
        return cid

    async def set_budget(self, campaign_id: str, budget: float) -> bool:
        self.budgets.append(budget)
        return True

    async def pause_campaign(self, campaign_id: str) -> bool:
        self.paused.append(campaign_id)
        return True

    async def fetch_metrics(self, campaign_id: str):
        return self.metrics


def make_state(segments=None) -> ShiftState:
    playlist = SegmentPlaylist.from_config(segments or LONG_CLIP)
    state = ShiftState("test", BasketBoard.from_config(BASKETS), playlist)
    playlist.next_segment()  # เริ่มเล่นท่อนแรก
    return state


def seek_to(state: ShiftState, seconds: float) -> None:
    """แกล้งว่าเล่นคลิปมาถึงวินาทีที่กำหนด

    ต้องขยับทั้งค่าจากตัวเล่นและเวลาที่เริ่มท่อน ให้สองอย่างสอดคล้องกัน
    เหมือนตอนเล่นจริง ไม่งั้นจะกลายเป็นการจำลองสถานการณ์ที่เป็นไปไม่ได้
    """
    duration = state.playlist.current.duration_seconds
    state.stream.seconds_remaining = duration - seconds
    state.stream.segment_started_at = time.time() - seconds


# ---------------------------------------------------------------- ปักตะกร้า


@pytest.mark.asyncio
async def test_pin_follows_what_the_host_is_talking_about():
    state = make_state()
    shop = FakeShop()
    agent = BasketAgent(EventBus(), state, Settings(), shop)
    await agent.start()
    try:
        seek_to(state, 10)
        await agent._sync_pin()
        assert state.baskets.pinned.sku == "S1"

        seek_to(state, 1300)
        await agent._sync_pin()
        assert state.baskets.pinned.sku == "S2", "คลิปเปลี่ยนไปพูดกันแดดแล้ว ต้องปักตาม"

        seek_to(state, 2500)
        await agent._sync_pin()
        assert shop.pins == ["S1", "S2", "S3"]
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_pin_does_not_thrash_while_the_host_stays_on_one_product():
    state = make_state()
    shop = FakeShop()
    agent = BasketAgent(EventBus(), state, Settings(), shop)
    await agent.start()
    try:
        for position in (10, 100, 500, 900, 1100):
            seek_to(state, position)
            await agent._sync_pin()
        assert shop.pins == ["S1"], "พูดเรื่องเดิมอยู่ ไม่ควรปักซ้ำ"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_pin_switches_a_little_early_not_late():
    """ปักช้ากว่าปากคือคนดูหาของไม่เจอ — ต้องปักล่วงหน้านิดหน่อย"""
    state = make_state()
    shop = FakeShop()
    settings = Settings()
    settings.baskets.pin_lead_seconds = 5
    agent = BasketAgent(EventBus(), state, settings, shop)
    await agent.start()
    try:
        seek_to(state, 1196)  # เหลืออีก 4 วินาทีจะถึงจุดเปลี่ยน
        await agent._sync_pin()
        assert state.baskets.pinned.sku == "S2"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_stale_player_reading_right_after_a_switch_is_ignored():
    """บั๊กจริงที่เจอตอนรัน: เปลี่ยนท่อนแล้วค่าเวลาที่เหลือยังเป็นของท่อนเก่า

    ผลคือคำนวณว่าเล่นมาเกือบจบท่อนใหม่แล้ว เลยปักตะกร้าของช่วงท้ายคลิป
    ทั้งที่เพิ่งเริ่มเล่นวินาทีแรก
    """
    state = make_state()
    shop = FakeShop()
    agent = BasketAgent(EventBus(), state, Settings(), shop)
    await agent.start()
    try:
        # เพิ่งเริ่มท่อนใหม่ แต่ค่าจากตัวเล่นยังค้างของท่อนเก่า (เหลือ 6 วิ)
        state.stream.segment_started_at = time.time()
        state.stream.seconds_remaining = 6.0
        await agent._sync_pin()
        assert state.baskets.pinned.sku == "S1", "ต้องปักของช่วงต้นคลิป ไม่ใช่ช่วงท้าย"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_pin_is_not_applied_twice_when_tick_and_event_collide():
    state = make_state()
    shop = FakeShop()
    bus = EventBus()
    agent = BasketAgent(bus, state, Settings(), shop)
    await agent.start()
    try:
        seek_to(state, 10)
        await agent._sync_pin()
        await agent._sync_pin(force=True)  # event เข้ามาทีหลังในวินาทีเดียวกัน
        assert shop.pins == ["S1"]
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_segment_change_repins_immediately():
    state = make_state()
    shop = FakeShop()
    bus = EventBus()
    agent = BasketAgent(bus, state, Settings(), shop)
    pins = bus.subscribe("basket.pinned", name="pins")
    await agent.start()
    try:
        seek_to(state, 0)
        bus.publish(SegmentChanged(to_segment="full", reason="เริ่มกะ"))
        await asyncio.sleep(0.05)
        event = pins.queue.get_nowait()
        assert isinstance(event, BasketPinned)
        assert event.sku == "S1"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_missing_basket_warns_a_human_once():
    state = make_state(
        [
            {
                "id": "s",
                "path": "/a.mp4",
                "duration_seconds": 600,
                "cues": [{"at_seconds": 0, "sku": "ไม่มีจริง"}],
            }
        ]
    )
    shop = FakeShop()
    bus = EventBus()
    notes = bus.subscribe("notify", name="n")
    agent = BasketAgent(bus, state, Settings(), shop)
    await agent.start()
    try:
        for _ in range(5):
            seek_to(state, 10)
            await agent._sync_pin()
        assert shop.pins == []
        assert notes.queue.qsize() == 1, "เตือนครั้งเดียวพอ ไม่ใช่ทุกรอบ"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_failed_pin_is_retried_on_the_next_check():
    state = make_state()
    shop = FakeShop()
    agent = BasketAgent(EventBus(), state, Settings(), shop)
    await agent.start()
    try:
        seek_to(state, 10)
        shop.fail_next = True
        await agent._sync_pin()
        assert state.baskets.pinned is None

        await agent._sync_pin()
        assert state.baskets.pinned.sku == "S1", "ครั้งก่อนพลาด รอบถัดไปต้องลองใหม่"
    finally:
        await agent.stop()


# ---------------------------------------------------------------- แอด


def ads_agent(state: ShiftState, ads: FakeAds, **overrides) -> AdsAgent:
    settings = Settings()
    settings.ads.grace_minutes_after_launch = 0
    settings.ads.min_spend_before_judging = 100
    settings.ads.min_purchases_before_judging = 2
    settings.ads.max_loss_baht = 100_000  # ปิดเบรกฉุกเฉินไว้ก่อนถ้าไม่ได้เทสมัน
    for key, value in overrides.items():
        setattr(settings.ads, key, value)
    return AdsAgent(EventBus(), state, settings, ads)


# ---------------------------------------------------------------- เพดานจากค่าคอม


def test_ceiling_is_derived_from_the_commission_actually_earned():
    """หัวใจของไลฟ์นายหน้า: จ่ายค่าแอดเกินค่าคอม = ยิ่งขายยิ่งเจ๊ง"""
    state = make_state()
    agent = ads_agent(state, FakeAds(), cpa_ceiling_ratio=0.6)

    # ยังไม่มีออเดอร์ → ใช้ค่าคอมต่ำสุด (10฿) คิดแบบระวังไว้ก่อน
    assert agent._commission() == pytest.approx(10.0)
    assert agent._max_cpa() == pytest.approx(6.0)

    # ขายตัวค่าคอมสูงได้ → เพดานขยับขึ้นตามจริง
    state.baskets.record_sale("S1", 10, 3900.0)
    assert agent._commission() == pytest.approx(40.0)
    assert agent._max_cpa() == pytest.approx(24.0)


def test_blended_commission_weights_by_orders_not_by_price():
    state = make_state()
    agent = ads_agent(state, FakeAds())
    state.baskets.record_sale("S1", 1, 390.0)   # คอม 40
    state.baskets.record_sale("S3", 9, 1791.0)  # คอม 10
    # ขายตัวคอมต่ำเยอะกว่า ค่าเฉลี่ยต้องเอนไปทางต่ำ
    assert agent._commission() == pytest.approx(13.0)


def test_override_wins_when_the_owner_sets_it_manually():
    state = make_state()
    agent = ads_agent(state, FakeAds(), max_cpa_override=15.0)
    state.baskets.record_sale("S1", 10, 3900.0)
    assert agent._max_cpa() == pytest.approx(15.0)


def test_net_profit_counts_commission_not_revenue():
    """ยอดขาย 3,900฿ ฟังดูดี แต่นายหน้าได้จริงแค่ค่าคอม"""
    state = make_state()
    agent = ads_agent(state, FakeAds())
    state.baskets.record_sale("S1", 10, 3900.0)  # ค่าคอม 400฿
    state.ads.spend = 500.0
    assert state.baskets.summary()["total_revenue"] == 3900.0
    assert agent._net_profit() == pytest.approx(-100.0), "ขายได้เยอะแต่ขาดทุนจริง"


# ---------------------------------------------------------------- แอด


@pytest.mark.asyncio
async def test_one_campaign_per_live_not_per_basket():
    state = make_state()
    ads = FakeAds()
    agent = ads_agent(state, ads)
    await agent.start()
    try:
        assert len(ads.created) == 1
        assert ads.created[0]["type"] == "gmv_max_live"

        # ปักตะกร้าเปลี่ยนไปมาสามครั้ง แอดต้องไม่ถูกแตะเลย
        for sku in ("S1", "S2", "S3"):
            state.baskets.mark_pinned(state.baskets.by_sku(sku).id)
            await asyncio.sleep(0.02)
        assert len(ads.created) == 1, "เปลี่ยนตะกร้าไม่ควรขึ้นแอดใหม่"
        assert ads.paused == []
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_ad_costing_more_than_the_commission_is_replaced():
    state = make_state()
    ads = FakeAds()
    agent = ads_agent(state, ads, cpa_ceiling_ratio=0.6)
    await agent.start()
    try:
        # ขายตัวคอม 20฿ → เพดาน 12฿ แต่จ่ายจริง 25฿/ออเดอร์
        state.baskets.record_sale("S2", 10, 2900.0)
        ads.metrics = AdMetricsSnapshot(spend=250.0, orders=10, revenue=2900.0)
        await agent.tick()
        assert ads.paused == ["cmp1"]
        assert len(ads.created) == 2
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_ad_well_under_the_commission_gets_scaled():
    state = make_state()
    ads = FakeAds()
    agent = ads_agent(state, ads, cpa_target_ratio=0.35, scale_step=1.3, starting_budget=300)
    await agent.start()
    try:
        # คอม 40฿ → เป้า 14฿ จ่ายจริง 10฿/ออเดอร์ = คุ้มมาก
        state.baskets.record_sale("S1", 20, 7800.0)
        ads.metrics = AdMetricsSnapshot(spend=200.0, orders=20, revenue=7800.0)
        await agent.tick()
        assert ads.budgets == [pytest.approx(390.0)]
        assert ads.paused == []
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_burning_money_with_zero_orders_is_caught():
    state = make_state()
    ads = FakeAds()
    agent = ads_agent(state, ads, min_purchases_before_judging=2)
    await agent.start()
    try:
        ads.metrics = AdMetricsSnapshot(spend=250.0, orders=0, revenue=0.0)
        await agent.tick()
        assert ads.paused == ["cmp1"]
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_fresh_ad_is_left_alone_during_the_grace_period():
    state = make_state()
    ads = FakeAds()
    agent = ads_agent(state, ads, grace_minutes_after_launch=20)
    await agent.start()
    try:
        ads.metrics = AdMetricsSnapshot(spend=900.0, orders=1, revenue=300.0)
        await agent.tick()
        assert ads.paused == [], "แอดเพิ่งขึ้น ตัวเลขยังไม่นิ่ง อย่าเพิ่งตัดสิน"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_cooldown_stops_launch_kill_launch_loops():
    state = make_state()
    ads = FakeAds()
    agent = ads_agent(state, ads, relaunch_cooldown_minutes=30)
    await agent.start()
    try:
        state.baskets.record_sale("S2", 10, 2900.0)
        ads.metrics = AdMetricsSnapshot(spend=250.0, orders=10, revenue=2900.0)
        await agent.tick()
        assert len(ads.created) == 2

        await agent.tick()
        assert len(ads.created) == 2, "ต้องรอ cooldown ก่อนเปลี่ยนอีกครั้ง"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_gives_up_and_calls_a_human_after_repeated_failures():
    state = make_state()
    ads = FakeAds()
    bus = EventBus()
    settings = Settings()
    settings.ads.grace_minutes_after_launch = 0
    settings.ads.min_spend_before_judging = 100
    settings.ads.max_loss_baht = 100_000
    settings.ads.relaunch_cooldown_minutes = 0
    settings.ads.max_relaunches_per_live = 2
    agent = AdsAgent(bus, state, settings, ads)
    notes = bus.subscribe("notify", name="n")
    await agent.start()
    try:
        state.baskets.record_sale("S2", 10, 2900.0)
        ads.metrics = AdMetricsSnapshot(spend=250.0, orders=10, revenue=2900.0)
        for _ in range(4):
            agent._killed_at = 0.0
            await agent.tick()

        assert state.ads.campaign_id == ""
        bodies = [notes.queue.get_nowait() for _ in range(notes.queue.qsize())]
        assert any(n.needs_human for n in bodies), "ยอมแพ้แล้วต้องเรียกคน"
    finally:
        await agent.stop()


# ---------------------------------------------------------------- เบรกฉุกเฉิน


@pytest.mark.asyncio
async def test_cumulative_loss_halts_ads_even_if_cpa_looks_fine():
    """กันเจ๊ง: ต่อให้ CPA ผ่านเกณฑ์ ถ้าขาดทุนสะสมถึงเพดานต้องหยุด"""
    state = make_state()
    ads = FakeAds()
    bus = EventBus()
    settings = Settings()
    settings.ads.grace_minutes_after_launch = 0
    settings.ads.min_spend_before_judging = 100
    settings.ads.max_loss_baht = 500
    agent = AdsAgent(bus, state, settings, ads)
    notes = bus.subscribe("notify", name="n")
    await agent.start()
    try:
        # คอม 40฿ x 5 ออเดอร์ = ได้ 200฿ แต่จ่ายค่าแอดไป 800฿
        state.baskets.record_sale("S1", 5, 1950.0)
        ads.metrics = AdMetricsSnapshot(spend=800.0, orders=5, revenue=1950.0)
        await agent.tick()

        assert state.ads.campaign_id == ""
        assert ads.paused == ["cmp1"]
        assert len(ads.created) == 1, "หยุดแล้วต้องไม่ขึ้นตัวใหม่"
        bodies = [notes.queue.get_nowait() for _ in range(notes.queue.qsize())]
        assert any(n.needs_human for n in bodies)
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_halt_is_permanent_for_the_rest_of_the_live():
    state = make_state()
    ads = FakeAds()
    agent = ads_agent(state, ads, max_loss_baht=300)
    await agent.start()
    try:
        state.baskets.record_sale("S3", 2, 398.0)  # คอม 10 x 2 = 20฿
        ads.metrics = AdMetricsSnapshot(spend=500.0, orders=2, revenue=398.0)
        await agent.tick()
        assert agent._halted is True

        # รอบถัดไปต้องไม่ทำอะไรอีก แม้ตัวเลขจะดูดีขึ้น
        ads.metrics = AdMetricsSnapshot(spend=500.0, orders=200, revenue=39800.0)
        await agent.tick()
        assert len(ads.created) == 1
        assert ads.budgets == []
    finally:
        await agent.stop()
