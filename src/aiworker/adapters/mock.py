"""Adapter จำลอง — ใช้ทดสอบทั้งระบบโดยไม่ต้องต่อ TikTok หรือ OBS จริง

รันด้วยชุดนี้ก่อนเสมอ เพื่อดูว่าตรรกะการตัดสินใจถูกใจหรือยัง
ค่อยสลับไปของจริงทีละตัวใน config
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

from ..domain.segments import Segment
from .base import AdMetricsSnapshot, HealthSnapshot, SalesSnapshot

# ตัวอย่างคอมเมนท์ที่เจอจริงในไลฟ์ไทย
SAMPLE_COMMENTS = [
    "ราคาเท่าไหร่คะ",
    "มีสีอื่นมั้ย",
    "ส่งกี่วันถึงคะ",
    "ไซซ์ M เหลือมั้ยคะ",
    "ของแท้ปะเนี่ย",
    "เก็บเงินปลายทางได้ไหม",
    "สั่งไปเมื่อวานยังไม่ได้เลยค่ะ",
    "สวยมากกก",
    "ตัวนี้ใช้แล้วผิวขาวขึ้นจริงไหมคะ",
    "ขอไลน์หน่อยค่ะ",
    "แม่ค้าน่ารักจัง",
    "โปรวันนี้มีอะไรบ้างคะ",
    "กดตะกร้าไม่ได้ค่ะ",
    "ลดอีกได้ไหมคะ",
    "รีวิวหน่อยค่า",
    "ส่งฟรีไหมคะ",
    "ใช้กับผิวแพ้ง่ายได้มั้ย",
    "สั่ง 2 ชิ้นลดเพิ่มมั้ยคะ",
]

NICKNAMES = [
    "ploy_p", "namtan99", "beer.k", "mook_mk", "fah__",
    "nong_yim", "aey2540", "praew.s", "tonz", "bam_bam",
]


class MockPlayer:
    """จำลองโปรแกรมเล่นวิดีโอ — มีโอกาสสตรีมสะดุดเหมือนของจริง"""

    def __init__(self, glitch_chance: float = 0.02, **_: Any) -> None:
        self.connected = False
        self.playing = False
        self.current: Segment | None = None
        self._started_at = 0.0
        self._dropped = 0
        self._viewers = 120
        self._glitch_chance = glitch_chance
        self._rng = random.Random(7)

    async def connect(self) -> bool:
        self.connected = True
        self.playing = True
        return True

    async def disconnect(self) -> None:
        self.connected = False
        self.playing = False

    async def play_segment(self, segment: Segment) -> bool:
        self.current = segment
        self._started_at = time.time()
        self.playing = True
        return True

    async def start(self) -> bool:
        self.playing = True
        return True

    async def stop(self) -> bool:
        self.playing = False
        return True

    async def health(self) -> HealthSnapshot:
        if not self.playing:
            return HealthSnapshot(is_live=False)

        # คนดูแกว่งไปมาแบบสุ่มเดินสุ่ม
        self._viewers = max(10, self._viewers + self._rng.randint(-12, 15))

        glitching = self._rng.random() < self._glitch_chance
        if glitching:
            self._dropped += self._rng.randint(50, 200)

        elapsed = time.time() - self._started_at
        duration = self.current.duration_seconds if self.current else 0.0
        return HealthSnapshot(
            is_live=True,
            bitrate_kbps=self._rng.randint(400, 700) if glitching else self._rng.randint(2200, 3500),
            dropped_frames=self._dropped,
            fps=self._rng.uniform(18, 24) if glitching else 30.0,
            viewers=self._viewers,
            current_segment=self.current.id if self.current else None,
            seconds_remaining=max(0.0, duration - elapsed),
        )


class MockCommentSource:
    """ปล่อยคอมเมนท์ปลอมเข้ามาเรื่อย ๆ"""

    def __init__(self, rate_per_minute: float = 40.0, seed: int = 11, **_: Any) -> None:
        self._interval = 60.0 / max(1.0, rate_per_minute)
        self._handler = None
        self._rng = random.Random(seed)
        self._running = False

    def set_handler(self, handler) -> None:
        self._handler = handler

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        self._running = False

    async def run(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(self._rng.expovariate(1.0 / self._interval))
            if self._handler is None:
                continue
            nickname = self._rng.choice(NICKNAMES)
            self._handler(
                {
                    "user_id": f"u_{nickname}",
                    "nickname": nickname,
                    "text": self._rng.choice(SAMPLE_COMMENTS),
                    "is_follower": self._rng.random() < 0.4,
                }
            )


class MockCommentSender:
    """เก็บคำตอบไว้ในหน่วยความจำ ให้ dashboard/เทสอ่านได้"""

    def __init__(self, **_: Any) -> None:
        self.sent: list[dict[str, Any]] = []

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def send(self, text: str, *, reply_to: str = "") -> bool:
        self.sent.append({"ts": time.time(), "to": reply_to, "text": text})
        return True


class MockShop:
    """จำลองการปักตะกร้าและยอดขาย

    ตะกร้าทุกใบอยู่ในไลฟ์ตลอด — ใบที่ถูกปักจะขายดีกว่าใบอื่นมาก
    เพราะคนดูเห็นเด่นที่สุด
    """

    def __init__(self, seed: int = 3, **_: Any) -> None:
        self._rng = random.Random(seed)
        self._pinned: str | None = None
        self._pinned_at = 0.0
        self._sales: dict[str, dict[str, float]] = {}
        self._rates: dict[str, float] = {}

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def pin_basket(self, basket_id: str, sku: str) -> bool:
        self._pinned = basket_id
        self._pinned_at = time.time()
        self._sales.setdefault(basket_id, {"orders": 0.0, "revenue": 0.0})
        if basket_id not in self._rates:
            # ตะกร้าบางใบ "ติด" บางใบเงียบ
            self._rates[basket_id] = self._rng.choice([0.3, 0.8, 1.5, 3.0, 5.0])
        return True

    async def fetch_sales(self, basket_id: str) -> SalesSnapshot | None:
        row = self._sales.get(basket_id)
        if row is None:
            return None
        if basket_id == self._pinned:
            minutes = (time.time() - self._pinned_at) / 60.0
            target = minutes * self._rates.get(basket_id, 1.0)
            while row["orders"] < int(target):
                row["orders"] += 1
                row["revenue"] += self._rng.choice([199.0, 290.0, 390.0, 590.0])
        return SalesSnapshot(
            orders=int(row["orders"]), revenue=row["revenue"], stock=None
        )


class MockAds:
    """จำลอง GMV Max Live — ต้นทุนต่อการซื้อของแต่ละแคมเปญไม่เท่ากัน"""

    def __init__(self, seed: int = 5, **_: Any) -> None:
        self._rng = random.Random(seed)
        self._campaigns: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def create_campaign(
        self,
        *,
        basket_id: str,
        sku: str,
        budget: float,
        campaign_type: str = "gmv_max_live",
    ) -> str | None:
        self._counter += 1
        campaign_id = f"cmp_{self._counter:03d}"
        self._campaigns[campaign_id] = {
            "type": campaign_type,
            "budget": budget,
            "started_at": time.time(),
            "spend": 0.0,
            # ต้นทุนต่อการซื้อจริงของแคมเปญนี้ — บางตัวถูก บางตัวแพงจนต้องปิด
            "cpa": self._rng.choice([45.0, 65.0, 90.0, 140.0, 220.0]),
            "aov": self._rng.uniform(250.0, 450.0),
            "active": True,
        }
        return campaign_id

    async def set_budget(self, campaign_id: str, budget: float) -> bool:
        if campaign_id not in self._campaigns:
            return False
        self._campaigns[campaign_id]["budget"] = budget
        return True

    async def pause_campaign(self, campaign_id: str) -> bool:
        if campaign_id in self._campaigns:
            self._campaigns[campaign_id]["active"] = False
        return True

    async def fetch_metrics(self, campaign_id: str) -> AdMetricsSnapshot | None:
        c = self._campaigns.get(campaign_id)
        if c is None:
            return None
        if c["active"]:
            minutes = (time.time() - c["started_at"]) / 60.0
            # ใช้งบหมดในหนึ่งชั่วโมงโดยประมาณ
            c["spend"] = min(c["budget"], c["budget"] * minutes / 60.0)
        spend = c["spend"]
        orders = int(spend / c["cpa"]) if spend else 0
        return AdMetricsSnapshot(
            spend=spend, orders=orders, revenue=orders * c["aov"]
        )
