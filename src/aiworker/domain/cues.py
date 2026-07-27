"""คิวชีต — บอกว่านาทีไหนของคลิป คนไลฟ์กำลังพูดถึงสินค้าตัวไหน

นี่คือหัวใจของการปักตะกร้าให้ถูก:
รีรันคือการเล่นคลิปที่อัดไว้ คนในคลิปพูดถึงสินค้าอะไร เราต้องปักตะกร้านั้น
ไม่ใช่เปลี่ยนตะกร้าตามเวลาหรือตามยอดขาย (นั่นคือไลฟ์สดคนละแบบ)

รองรับสองวิธีเตรียมข้อมูล:
1. ตัดคลิปเป็นท่อน ๆ ท่อนละสินค้า  → ใส่ `sku` ที่ตัวท่อน
2. คลิปยาวไฟล์เดียวทั้งกะ           → ใส่ `cues` ระบุวินาทีที่เปลี่ยนสินค้า

วิธีที่ 2 ตรงกับการรีรันจริงมากกว่า เพราะส่วนใหญ่อัดไลฟ์มาทั้งกะแล้วเปิดวน
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field


@dataclass(slots=True, order=True)
class Cue:
    """จุดที่คนในคลิปเริ่มพูดถึงสินค้าตัวใหม่"""

    at_seconds: float
    sku: str = field(compare=False)
    note: str = field(default="", compare=False)


class CueSheet:
    """แปลง 'เล่นคลิปมาถึงวินาทีที่เท่าไหร่' เป็น 'ต้องปักตะกร้าไหน'"""

    def __init__(self, cues: list[Cue], default_sku: str = "") -> None:
        self.cues = sorted(cues)
        self.default_sku = default_sku
        self._marks = [c.at_seconds for c in self.cues]

    @classmethod
    def from_config(cls, rows: list[dict] | None, default_sku: str = "") -> CueSheet:
        cues = [
            Cue(
                at_seconds=float(r.get("at_seconds", 0)),
                sku=str(r.get("sku", "")),
                note=str(r.get("note", "")),
            )
            for r in (rows or [])
        ]
        return cls(cues, default_sku)

    def __bool__(self) -> bool:
        return bool(self.cues)

    def sku_at(self, seconds: float) -> str:
        """สินค้าที่กำลังถูกนำเสนอ ณ วินาทีนั้นของคลิป

        ก่อนถึง cue แรก ใช้ default_sku (สินค้าประจำท่อน)
        """
        if not self.cues:
            return self.default_sku
        idx = bisect_right(self._marks, seconds) - 1
        if idx < 0:
            return self.default_sku
        return self.cues[idx].sku or self.default_sku

    def next_change(self, seconds: float) -> Cue | None:
        """cue ถัดไป — ใช้เตือนล่วงหน้าว่าอีกไม่กี่วินาทีจะต้องเปลี่ยนตะกร้า"""
        idx = bisect_right(self._marks, seconds)
        return self.cues[idx] if idx < len(self.cues) else None

    def skus(self) -> list[str]:
        """SKU ทั้งหมดที่ปรากฏในคลิปนี้ (เรียงตามลำดับที่ถูกพูดถึง)"""
        seen: list[str] = []
        for cue in self.cues:
            if cue.sku and cue.sku not in seen:
                seen.append(cue.sku)
        if self.default_sku and self.default_sku not in seen:
            seen.insert(0, self.default_sku)
        return seen

    def validate_against(self, known_skus: set[str]) -> list[str]:
        """เช็คว่า cue ชี้ไปยัง SKU ที่ไม่มีในตะกร้าหรือเปล่า

        เจอตอนตรวจก่อนเข้ากะดีกว่าไปเจอตอนไลฟ์แล้วปักตะกร้าผิด
        """
        problems: list[str] = []
        for cue in self.cues:
            if cue.sku and cue.sku not in known_skus:
                problems.append(
                    f"นาทีที่ {cue.at_seconds / 60:.1f} ชี้ไปที่ {cue.sku} "
                    "ซึ่งไม่มีในรายการตะกร้า"
                )
        return problems
