"""ตะกร้าที่อยู่ในไลฟ์ — ตรรกะล้วน ไม่มี I/O เพื่อให้เทสง่าย

ในไลฟ์นายหน้าแบบรีรัน ตะกร้าทุกใบอยู่ในไลฟ์พร้อมกันตั้งแต่ต้น
สิ่งที่เปลี่ยนไปมาคือ "ใบไหนถูกปักขึ้นแสดง" ตามที่คนในคลิปกำลังพูดถึง
ไม่ใช่การหมุนคิวเข้า-ออกตามเวลา
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(slots=True)
class Basket:
    id: str
    name: str
    sku: str
    price: float
    cost: float = 0.0
    stock: int = 0
    orders: int = 0
    revenue: float = 0.0
    notes: str = ""

    pinned_seconds: float = 0.0
    """รวมเวลาที่ถูกปักแสดงทั้งกะ — ใช้ดูว่าคลิปให้เวลาสินค้าตัวไหนมากน้อย"""

    pin_count: int = 0
    last_pinned_at: float | None = None

    @property
    def is_pinned(self) -> bool:
        return self.last_pinned_at is not None

    @property
    def gross_margin(self) -> float:
        """กำไรขั้นต้นก่อนหักค่าแอด (ค่าแอดเป็นของทั้งไลฟ์ ไม่ใช่ของตะกร้าใบเดียว)"""
        return self.revenue - (self.cost * self.orders)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "sku": self.sku,
            "price": self.price,
            "orders": self.orders,
            "revenue": round(self.revenue, 2),
            "gross_margin": round(self.gross_margin, 2),
            "stock": self.stock,
            "pinned_minutes": round(self.pinned_seconds / 60.0, 1),
            "pin_count": self.pin_count,
            "is_pinned": self.is_pinned,
        }


class BasketBoard:
    """ตะกร้าทั้งหมดที่อยู่ในไลฟ์ พร้อมตัวชี้ว่าใบไหนถูกปักอยู่"""

    def __init__(self, baskets: list[Basket]) -> None:
        self.baskets = baskets
        self._by_sku = {b.sku: b for b in baskets if b.sku}
        self._pinned_id: str | None = None
        self._pinned_at: float | None = None

    @classmethod
    def from_config(cls, rows: list[dict]) -> BasketBoard:
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
        return cls(baskets)

    # ---------------- ค้นหา ----------------

    def by_sku(self, sku: str) -> Basket | None:
        return self._by_sku.get(sku)

    def by_id(self, basket_id: str) -> Basket | None:
        return next((b for b in self.baskets if b.id == basket_id), None)

    @property
    def pinned(self) -> Basket | None:
        return self.by_id(self._pinned_id) if self._pinned_id else None

    def known_skus(self) -> set[str]:
        return set(self._by_sku)

    # ---------------- ปักตะกร้า ----------------

    def mark_pinned(self, basket_id: str) -> Basket | None:
        """บันทึกว่าตะกร้าใบนี้ถูกปักขึ้นแสดงแล้ว"""
        target = self.by_id(basket_id)
        if target is None:
            return None

        now = time.time()
        previous = self.pinned
        if previous is not None and self._pinned_at is not None:
            previous.pinned_seconds += now - self._pinned_at
            previous.last_pinned_at = None

        target.pin_count += 1
        target.last_pinned_at = now
        self._pinned_id = target.id
        self._pinned_at = now
        return target

    def settle_pinned_time(self) -> None:
        """ปิดยอดเวลาของใบที่ปักอยู่ — เรียกตอนจบกะเพื่อให้ตัวเลขครบ"""
        current = self.pinned
        if current is not None and self._pinned_at is not None:
            current.pinned_seconds += time.time() - self._pinned_at
            self._pinned_at = time.time()

    # ---------------- ยอดขาย ----------------

    def record_sale(self, sku: str, orders: int, revenue: float) -> None:
        basket = self.by_sku(sku)
        if basket is None:
            return
        basket.orders += orders
        basket.revenue += revenue
        if basket.stock > 0:
            basket.stock = max(0, basket.stock - orders)

    def out_of_stock(self) -> list[Basket]:
        return [b for b in self.baskets if b.stock == 0 and b.pin_count > 0]

    def never_pinned(self) -> list[Basket]:
        """ตะกร้าที่ไม่เคยถูกพูดถึงเลยทั้งกะ — คลิปอาจไม่ครอบคลุม"""
        return [b for b in self.baskets if b.pin_count == 0]

    # ---------------- สรุป ----------------

    def summary(self) -> dict:
        pinned = self.pinned
        return {
            "total": len(self.baskets),
            "pinned": pinned.as_dict() if pinned else None,
            "all": [b.as_dict() for b in self.baskets],
            "total_revenue": round(sum(b.revenue for b in self.baskets), 2),
            "total_orders": sum(b.orders for b in self.baskets),
            "never_pinned": [b.name for b in self.never_pinned()],
        }
