"""ต่อจิ๊กซอว์ — เลือกท่อนวิดีโอถัดไปให้รีรันดูเหมือนไลฟ์สด

หัวใจคือ "อย่าให้ซ้ำจนคนดูจับได้" และ "ท่อนที่เล่นต้องตรงกับตะกร้าที่ขึ้นอยู่"
"""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from .cues import CueSheet


@dataclass(slots=True)
class Segment:
    """หนึ่งท่อนของคลิปไลฟ์ที่อัดไว้"""

    id: str
    path: str
    duration_seconds: float
    sku: str = ""
    """สินค้าหลักของท่อนนี้ — ใช้เมื่อไม่ได้ทำคิวชีตละเอียด"""

    cues: CueSheet = field(default_factory=lambda: CueSheet([]))
    """คิวชีตในท่อน — บอกว่านาทีไหนพูดถึงสินค้าตัวไหน (ละเอียดกว่า sku)"""

    tags: list[str] = field(default_factory=list)
    weight: float = 1.0
    """น้ำหนักการสุ่ม — ท่อนที่ขายดีตั้งสูงไว้ให้ออกบ่อยขึ้น"""

    def sku_at(self, seconds: float) -> str:
        """สินค้าที่กำลังถูกนำเสนอ ณ วินาทีนั้นของท่อนนี้"""
        return self.cues.sku_at(seconds) if self.cues else self.sku

    def all_skus(self) -> list[str]:
        return self.cues.skus() if self.cues else ([self.sku] if self.sku else [])

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "duration_seconds": self.duration_seconds,
            "sku": self.sku,
            "tags": list(self.tags),
            "cue_count": len(self.cues.cues),
        }


class SegmentPlaylist:
    """คิวท่อนวิดีโอ พร้อมกันซ้ำ

    - `avoid_repeat_within` = จำนวนท่อนล่าสุดที่จะไม่หยิบซ้ำ
    - ถ้าระบุ sku จะเลือกเฉพาะท่อนของสินค้านั้น (ให้ตรงกับตะกร้าที่ขึ้น)
    """

    def __init__(
        self,
        segments: list[Segment],
        *,
        shuffle: bool = True,
        avoid_repeat_within: int = 3,
        rng: random.Random | None = None,
    ) -> None:
        self.segments = segments
        self.shuffle = shuffle
        self._recent: deque[str] = deque(maxlen=max(0, avoid_repeat_within))
        self._rng = rng or random.Random()
        self._cursor = 0
        self.current: Segment | None = None

    @classmethod
    def from_config(cls, rows: list[dict], **kwargs) -> SegmentPlaylist:
        segments = [
            Segment(
                id=str(r.get("id") or f"seg{i + 1:03d}"),
                path=str(r.get("path", "")),
                duration_seconds=float(r.get("duration_seconds", 0)),
                sku=str(r.get("sku", "")),
                cues=CueSheet.from_config(r.get("cues"), str(r.get("sku", ""))),
                tags=list(r.get("tags", []) or []),
                weight=float(r.get("weight", 1.0)),
            )
            for i, r in enumerate(rows)
        ]
        return cls(segments, **kwargs)

    def candidates(self, sku: str | None = None) -> list[Segment]:
        pool = self.segments
        if sku:
            matched = [s for s in pool if s.sku == sku]
            # ถ้ายังไม่ได้อัดคลิปของ SKU นี้ ใช้ท่อนกลาง (ไม่ระบุ sku) แทน
            pool = matched or [s for s in pool if not s.sku] or pool
        fresh = [s for s in pool if s.id not in self._recent]
        return fresh or pool

    def next_segment(self, sku: str | None = None) -> Segment | None:
        """เลือกท่อนถัดไป — นี่คือ 'การต่อจิ๊กซอว์'"""
        pool = self.candidates(sku)
        if not pool:
            return None

        if self.shuffle:
            weights = [max(0.01, s.weight) for s in pool]
            chosen = self._rng.choices(pool, weights=weights, k=1)[0]
        else:
            chosen = pool[self._cursor % len(pool)]
            self._cursor += 1

        if self._recent.maxlen:
            self._recent.append(chosen.id)
        self.current = chosen
        return chosen

    def total_duration(self) -> float:
        return sum(s.duration_seconds for s in self.segments)

    def coverage_by_sku(self) -> dict[str, float]:
        """รวมความยาวคลิปต่อ SKU — ใช้เช็คว่าสินค้าไหนคลิปน้อยเกินจะรีรันทั้งกะ"""
        out: dict[str, float] = {}
        for s in self.segments:
            key = s.sku or "_generic"
            out[key] = out.get(key, 0.0) + s.duration_seconds
        return out
