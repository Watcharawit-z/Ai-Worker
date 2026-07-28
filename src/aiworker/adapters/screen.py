"""เฝ้าหน้าจอเพื่อจับว่าปริศนายืนยันตัวตน (จิ๊กซอว์) โผล่ขึ้นมาหรือยัง

ขอบเขตที่ตั้งใจไว้ชัดเจน: **ดูอย่างเดียว ไม่แตะเมาส์ ไม่แตะคีย์บอร์ด**
ตัวนี้ไม่มีความสามารถกดหรือลากอะไรบนหน้าจอเลย มีแต่การอ่านภาพ
เพราะปริศนานี้มีไว้ยืนยันว่ามีคนอยู่ — ถ้าให้โปรแกรมเลื่อนแทน
การตรวจสอบนั้นก็ไม่เหลือความหมาย และเป็นเหตุให้โดนระงับถ้าโดนจับได้

วิธีตรวจจับ: เทียบภาพบริเวณที่ปริศนามักโผล่ กับภาพตัวอย่างที่ผู้ใช้เก็บไว้เอง
ต้อง calibrate ก่อนใช้ (ดู scripts/calibrate_screen.py)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ScreenReading:
    """ผลการอ่านหน้าจอหนึ่งครั้ง"""

    challenge_visible: bool = False
    confidence: float = 0.0
    detail: str = ""
    snapshot_path: str = ""
    """ภาพหน้าจอตอนเจอปริศนา — แนบไปกับการแจ้งเตือนให้ดูจากมือถือได้"""


class MockScreenWatcher:
    """จำลองปริศนาโผล่ตามรอบ — ใช้ทดสอบว่าสายการปลุกคนทำงานครบไหม"""

    def __init__(
        self,
        every_seconds: float = 120.0,
        visible_seconds: float = 45.0,
        **_: Any,
    ) -> None:
        self.every = every_seconds
        self.visible = visible_seconds
        self._started = time.time()

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def read(self) -> ScreenReading:
        phase = (time.time() - self._started) % self.every
        if phase < self.visible:
            return ScreenReading(True, 0.95, "ปริศนาจำลอง")
        return ScreenReading(False, 0.0, "")


class TemplateScreenWatcher:
    """เทียบภาพบริเวณที่กำหนด กับภาพตัวอย่างของปริศนา

    ต้องติดตั้ง:  pip install mss pillow

    ตั้งค่าใน config:
        region: [x, y, width, height]   บริเวณที่ปริศนาโผล่
        templates: ["config/reference/puzzle_1.png", ...]
        threshold: 0.82                 ความคล้ายขั้นต่ำที่ถือว่าเจอ

    เก็บภาพตัวอย่างด้วย:  python scripts/calibrate_screen.py
    """

    def __init__(
        self,
        region: list[int] | None = None,
        templates: list[str] | None = None,
        threshold: float = 0.82,
        downscale: int = 4,
        snapshot_dir: str = "runtime/snapshots",
        **_: Any,
    ) -> None:
        self.region = tuple(region) if region else None
        self.template_paths = list(templates or [])
        self.threshold = threshold
        self.downscale = max(1, downscale)
        self.snapshot_dir = Path(snapshot_dir)
        self._last_snapshot = ""
        self._sct: Any = None
        self._templates: list[tuple[str, list[float]]] = []
        self._warned = False

    async def connect(self) -> bool:
        try:
            import mss  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            log.error(
                "ต้องติดตั้งก่อน: pip install mss pillow "
                "(ตอนนี้จะข้ามการเฝ้าจอ — ต้องมีคนเฝ้าเองเต็มเวลา)"
            )
            return False

        import mss

        self._sct = mss.mss()

        for path in self.template_paths:
            sig = self._signature_from_file(path)
            if sig is not None:
                self._templates.append((path, sig))

        if not self._templates:
            log.error(
                "ยังไม่มีภาพตัวอย่างปริศนา — รัน python scripts/calibrate_screen.py ก่อน "
                "ไม่งั้นระบบจะจับปริศนาไม่ได้เลย"
            )
            return False
        if self.region is None:
            log.error("ยังไม่ได้ตั้งค่า region ว่าปริศนาโผล่ตรงไหนของจอ")
            return False

        log.info("เฝ้าจอด้วยภาพตัวอย่าง %d แบบ", len(self._templates))
        return True

    async def disconnect(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:  # noqa: BLE001
                pass
            self._sct = None

    async def read(self) -> ScreenReading:
        if self._sct is None or not self._templates or self.region is None:
            if not self._warned:
                self._warned = True
                log.warning("ตัวเฝ้าจอยังไม่พร้อม — ข้ามการตรวจ")
            return ScreenReading(False, 0.0, "ตัวเฝ้าจอไม่พร้อม")

        try:
            image = self._grab()
            current = _signature(image, self.downscale)
        except Exception as exc:  # noqa: BLE001
            log.warning("อ่านหน้าจอไม่สำเร็จ: %s", exc)
            return ScreenReading(False, 0.0, str(exc))

        best_score, best_name = 0.0, ""
        for name, template in self._templates:
            score = _similarity(current, template)
            if score > best_score:
                best_score, best_name = score, name

        if best_score < self.threshold:
            self._last_snapshot = ""
            return ScreenReading(False, best_score, "")

        # เก็บภาพครั้งเดียวต่อการโผล่หนึ่งรอบ ไม่ต้องเขียนดิสก์ทุก 3 วินาที
        if not self._last_snapshot:
            self._last_snapshot = self._save_snapshot(image)
        return ScreenReading(
            True, best_score, f"ตรงกับ {Path(best_name).name}", self._last_snapshot
        )

    def _save_snapshot(self, image: Any) -> str:
        """บันทึกภาพจอตอนเจอปริศนา เพื่อแนบไปกับการแจ้งเตือน"""
        try:
            self.snapshot_dir.mkdir(parents=True, exist_ok=True)
            path = self.snapshot_dir / f"challenge_{int(time.time())}.png"
            image.save(path)
            self._prune_snapshots()
            return str(path)
        except Exception as exc:  # noqa: BLE001
            log.warning("บันทึกภาพหน้าจอไม่สำเร็จ: %s", exc)
            return ""

    def _prune_snapshots(self, keep: int = 20) -> None:
        """เก็บแค่ภาพล่าสุด — ไลฟ์ยาว ๆ ไม่งั้นดิสก์เต็ม"""
        try:
            shots = sorted(self.snapshot_dir.glob("challenge_*.png"))
            for old in shots[:-keep]:
                old.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass

    # ---------------- ภายใน ----------------

    def _grab(self) -> Any:
        from PIL import Image

        x, y, w, h = self.region  # type: ignore[misc]
        raw = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        return Image.frombytes("RGB", raw.size, raw.rgb)

    def _signature_from_file(self, path: str) -> list[float] | None:
        try:
            from PIL import Image

            with Image.open(path) as img:
                return _signature(img.convert("RGB"), self.downscale)
        except Exception as exc:  # noqa: BLE001
            log.warning("อ่านภาพตัวอย่าง %s ไม่ได้: %s", path, exc)
            return None


def _signature(image: Any, downscale: int) -> list[float]:
    """ย่อภาพเป็นเทาแล้วแปลงเป็นเวกเตอร์ — ทนต่อการขยับเล็กน้อยและ noise"""
    width = max(8, image.width // downscale)
    height = max(8, image.height // downscale)
    small = image.convert("L").resize((width, height))
    pixels = list(small.getdata())
    mean = sum(pixels) / len(pixels) if pixels else 0.0
    return [p - mean for p in pixels]


def _similarity(a: list[float], b: list[float]) -> float:
    """สหสัมพันธ์แบบ normalize — 1.0 คือเหมือนกันเป๊ะ"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return max(0.0, dot / (norm_a * norm_b))
