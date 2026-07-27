"""คิวตะกร้า 10 ใบ — ตรรกะล้วน ไม่มี I/O เพื่อให้เทสง่าย"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum


class BasketState(str, Enum):
    QUEUED = "queued"
    LIVE = "live"
    DONE = "done"
    SKIPPED = "skipped"


@dataclass(slots=True)
class Basket:
    id: str
    name: str
    sku: str
    price: float
    cost: float = 0.0
    stock: int = 0
    state: BasketState = BasketState.QUEUED
    started_at: float | None = None
    ended_at: float | None = None
    orders: int = 0
    revenue: float = 0.0
    ad_spend: float = 0.0
    notes: str = ""

    @property
    def minutes_live(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.ended_at or time.time()
        return (end - self.started_at) / 60.0

    @property
    def margin(self) -> float:
        """กำไรขั้นต้นหลังหักค่าแอด"""
        return self.revenue - (self.cost * self.orders) - self.ad_spend

    @property
    def roas(self) -> float:
        return self.revenue / self.ad_spend if self.ad_spend > 0 else 0.0

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "sku": self.sku,
            "price": self.price,
            "state": self.state.value,
            "minutes_live": round(self.minutes_live, 1),
            "orders": self.orders,
            "revenue": round(self.revenue, 2),
            "ad_spend": round(self.ad_spend, 2),
            "roas": round(self.roas, 2),
            "margin": round(self.margin, 2),
            "stock": self.stock,
        }


class Verdict(str, Enum):
    """คำตัดสินว่าตะกร้านี้ไปต่อหรือพอ"""

    TOO_EARLY = "too_early"
    KEEP_GOING = "keep_going"
    SCALE = "scale"
    ROTATE = "rotate"
    OUT_OF_STOCK = "out_of_stock"


@dataclass(slots=True)
class BasketQueue:
    """คิวตะกร้าของหนึ่งช่อง

    งานของ 'พนักงานดูแลช่องและจัดการตะกร้า' ในสไลด์:
    เตรียมตะกร้าไว้ 10 ใบ แล้วหมุนตามคิว
    """

    baskets: list[Basket] = field(default_factory=list)
    _live_index: int | None = field(default=None, init=False)

    @classmethod
    def from_config(cls, rows: list[dict]) -> BasketQueue:
        baskets = [
            Basket(
                id=str(r.get("id") or f"b{i + 1:02d}"),
                name=r.get("name", "ไม่ระบุชื่อ"),
                sku=str(r.get("sku", "")),
                price=float(r.get("price", 0)),
                cost=float(r.get("cost", 0)),
                stock=int(r.get("stock", 0)),
                notes=r.get("notes", ""),
            )
            for i, r in enumerate(rows)
        ]
        return cls(baskets=baskets)

    @property
    def live(self) -> Basket | None:
        if self._live_index is None:
            return None
        return self.baskets[self._live_index]

    @property
    def position(self) -> int:
        """ลำดับตะกร้าที่กำลังไลฟ์ (เริ่มที่ 1) — ใช้บอกพนักงานไลฟ์ว่าคิวถึงไหน"""
        return 0 if self._live_index is None else self._live_index + 1

    def next_queued_index(self) -> int | None:
        start = 0 if self._live_index is None else self._live_index + 1
        for i in range(start, len(self.baskets)):
            if self.baskets[i].state == BasketState.QUEUED and self.baskets[i].stock != 0:
                return i
        return None

    def upcoming(self, count: int = 3) -> list[Basket]:
        """ตะกร้าถัดไปในคิว — เอาไว้แจ้งพนักงานไลฟ์ล่วงหน้า

        ข้ามใบที่ของหมด เพราะแจ้งไปแล้วพนักงานก็ขายไม่ได้
        """
        start = 0 if self._live_index is None else self._live_index + 1
        return [
            b
            for b in self.baskets[start:]
            if b.state == BasketState.QUEUED and b.stock != 0
        ][:count]

    def activate_next(self, *, reason: str = "") -> Basket | None:
        """ปิดตะกร้าปัจจุบันแล้วขึ้นใบถัดไป"""
        idx = self.next_queued_index()
        if idx is None:
            return None
        current = self.live
        if current is not None:
            current.state = BasketState.DONE
            current.ended_at = time.time()
            if reason:
                current.notes = (current.notes + f" | ปิดเพราะ: {reason}").strip(" |")
        nxt = self.baskets[idx]
        nxt.state = BasketState.LIVE
        nxt.started_at = time.time()
        self._live_index = idx
        return nxt

    def record_sale(self, orders: int, revenue: float) -> None:
        b = self.live
        if b is None:
            return
        b.orders += orders
        b.revenue += revenue
        if b.stock > 0:
            b.stock = max(0, b.stock - orders)

    def record_ad_spend(self, amount: float) -> None:
        if self.live is not None:
            self.live.ad_spend += amount

    def evaluate(
        self,
        *,
        min_minutes: float,
        max_minutes: float,
        evaluate_after_minutes: float,
        min_orders: int,
        target_roas: float,
    ) -> Verdict:
        """ตัดสินว่าตะกร้าที่ไลฟ์อยู่ควรไปต่อหรือเปลี่ยน

        ตรงกับกฎในสไลด์: "สังเกตยอดขายภายใน 1 ชม. มียอดขายแค่ไหน
        สินค้าที่ไลฟ์ไปต่อได้มั้ย"
        """
        b = self.live
        if b is None:
            return Verdict.ROTATE
        if b.stock == 0:
            return Verdict.OUT_OF_STOCK

        mins = b.minutes_live
        if mins >= max_minutes:
            return Verdict.ROTATE
        if mins < min_minutes:
            return Verdict.TOO_EARLY
        if mins < evaluate_after_minutes:
            # ยังไม่ถึงเวลาตัดสิน แต่ถ้าติดแรงมากก็สเกลเลย
            if b.orders >= min_orders * 3 and (b.ad_spend == 0 or b.roas >= target_roas):
                return Verdict.SCALE
            return Verdict.TOO_EARLY

        if b.orders < min_orders:
            return Verdict.ROTATE
        if b.ad_spend > 0 and b.roas < target_roas * 0.5:
            return Verdict.ROTATE
        if b.orders >= min_orders * 2 and (b.ad_spend == 0 or b.roas >= target_roas):
            return Verdict.SCALE
        return Verdict.KEEP_GOING

    def summary(self) -> dict:
        return {
            "position": self.position,
            "total": len(self.baskets),
            "live": self.live.as_dict() if self.live else None,
            "upcoming": [b.as_dict() for b in self.upcoming(3)],
            "all": [b.as_dict() for b in self.baskets],
            "total_revenue": round(sum(b.revenue for b in self.baskets), 2),
            "total_orders": sum(b.orders for b in self.baskets),
            "total_ad_spend": round(sum(b.ad_spend for b in self.baskets), 2),
        }
