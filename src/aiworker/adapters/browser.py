"""ทำงานผ่านเบราว์เซอร์ — ปักตะกร้า และส่งคอมเมนท์ตอบกลับ

ทำไมต้องผ่านเบราว์เซอร์:
TikTok ไม่เปิด API ให้ปักตะกร้าหรือส่งคอมเมนท์ แต่ทั้งสองอย่างกดได้ในหน้าเว็บ
ตัวนี้จึงเปิด Chromium ค้างไว้หนึ่งหน้าต่าง ล็อกอินค้างไว้ในโปรไฟล์เดียว
แล้วกดปุ่มเดิมที่คนเคยกด — ไม่ได้แฮ็กอะไร แค่กดแทน

ขอบเขตที่ตัวนี้ไม่ข้าม:
**ถ้าปริศนายืนยันตัวตน (จิ๊กซอว์) โผล่อยู่ ตัวนี้จะหยุดมือทันที ไม่แตะอะไรทั้งสิ้น**
ปริศนานั้นมีไว้ตรวจว่ามีคนอยู่จริง ระบบนี้จึงถอยออกมาแล้วปลุกคนแทน
ดู docs/JIGSAW.md

ต้องติดตั้งก่อน:
    pip install playwright
    playwright install chromium

ก่อนใช้ครั้งแรกต้อง calibrate:
    python scripts/calibrate_browser.py
เพราะหน้าตาเว็บ TikTok เปลี่ยนบ่อย selector ตายตัวจะพังในไม่กี่เดือน
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .base import SalesSnapshot

log = logging.getLogger(__name__)

DEFAULT_PROFILE_DIR = "runtime/browser-profile"


@dataclass(slots=True)
class BrowserSelectors:
    """ที่อยู่ของปุ่มต่าง ๆ บนหน้าเว็บ — เก็บมาจาก scripts/calibrate_browser.py

    ว่างไว้ = ยังไม่ได้ calibrate ระบบจะไม่กดอะไรเลยและบอกให้คนทำเอง
    """

    product_row: str = ""
    """แถวสินค้าหนึ่งรายการในลิสต์ตะกร้า เช่น ".product-list-item" """

    product_row_template: str = ""
    """ถ้าหน้าเว็บมี attribute ที่ระบุ SKU ตรง ๆ ใส่แบบมี {sku} เช่น "[data-sku='{sku}']"
    ถ้ามีอันนี้จะแม่นกว่าการไล่หาจากข้อความ"""

    pin_button: str = ""
    """ปุ่มปักหมุดภายในแถวสินค้า เช่น "button:has-text('ปักหมุด')" """

    pinned_marker: str = ""
    """เครื่องหมายว่าแถวนี้ถูกปักอยู่ (ไม่ใส่ก็ได้ แค่จะตรวจซ้ำไม่ได้)"""

    comment_box: str = ""
    """ช่องพิมพ์คอมเมนท์"""

    comment_send: str = ""
    """ปุ่มส่ง — ถ้าเว้นว่างจะใช้กด Enter แทน"""

    captcha: str = ""
    """หน้าตาปริศนาจิ๊กซอว์ — ใช้ตรวจว่าต้องหยุดมือ ควรใส่เสมอ"""


def _selectors(opts: dict[str, Any]) -> BrowserSelectors:
    known = set(BrowserSelectors.__dataclass_fields__)
    return BrowserSelectors(**{k: str(v) for k, v in opts.items() if k in known})


# ---------------------------------------------------------------- session


@dataclass
class _SessionConfig:
    profile_dir: str = DEFAULT_PROFILE_DIR
    console_url: str = ""
    headless: bool = False
    timeout_ms: int = 8000
    slow_mo_ms: int = 120
    dry_run: bool = True
    selectors: BrowserSelectors = field(default_factory=BrowserSelectors)


class BrowserSession:
    """หน้าต่างเบราว์เซอร์หนึ่งบาน ใช้ร่วมกันทั้งการปักตะกร้าและการตอบคอมเมนท์

    ล็อกอินค้างไว้ในโปรไฟล์ ไม่ต้องกรอกรหัสใหม่ทุกครั้งที่เปิดระบบ
    และไม่มีรหัสผ่านอยู่ในไฟล์ config เลย
    """

    def __init__(self, cfg: _SessionConfig) -> None:
        self.cfg = cfg
        self.selectors = cfg.selectors
        self.page: Any = None
        self._playwright: Any = None
        self._context: Any = None
        self._users = 0
        self._lock = asyncio.Lock()
        self._challenge_logged = False

    # ---------------- lifecycle ----------------

    async def connect(self) -> bool:
        async with self._lock:
            self._users += 1
            if self.page is not None:
                return True
            try:
                from playwright.async_api import async_playwright
            except ImportError:
                log.error(
                    "ยังไม่ได้ติดตั้ง playwright — "
                    "pip install playwright && playwright install chromium"
                )
                return False

            profile = Path(self.cfg.profile_dir)
            profile.mkdir(parents=True, exist_ok=True)
            try:
                self._playwright = await async_playwright().start()
                self._context = await self._playwright.chromium.launch_persistent_context(
                    str(profile),
                    headless=self.cfg.headless,
                    slow_mo=self.cfg.slow_mo_ms,
                    viewport={"width": 1440, "height": 900},
                )
                self._context.set_default_timeout(self.cfg.timeout_ms)
                pages = self._context.pages
                self.page = pages[0] if pages else await self._context.new_page()
                if self.cfg.console_url:
                    await self.page.goto(self.cfg.console_url, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001
                log.error("เปิดเบราว์เซอร์ไม่สำเร็จ: %s", exc)
                await self._teardown()
                return False

            log.info(
                "เบราว์เซอร์พร้อมแล้ว (โปรไฟล์ %s) โหมด%s",
                profile,
                "ซ้อม — ยังไม่กดจริง" if self.cfg.dry_run else "กดจริง",
            )
            return True

    async def disconnect(self) -> None:
        async with self._lock:
            self._users = max(0, self._users - 1)
            if self._users == 0:
                await self._teardown()

    async def _teardown(self) -> None:
        for closer in (
            getattr(self._context, "close", None),
            getattr(self._playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                await closer()
            except Exception:  # noqa: BLE001
                pass
        self.page = None
        self._context = None
        self._playwright = None

    # ---------------- ขอบเขต ----------------

    async def hands_off(self) -> bool:
        """ตอนนี้ห้ามแตะหน้าเว็บหรือเปล่า

        ถ้าปริศนายืนยันตัวตนโผล่อยู่ ตอบ True — ทุกอย่างต้องหยุดรอคน
        การกดอะไรระหว่างนั้นคือการเข้าไปยุ่งกับกลไกตรวจว่ามีคนอยู่จริง
        """
        if self.page is None:
            return True
        if not self.selectors.captcha:
            return False
        try:
            visible = await self.page.locator(self.selectors.captcha).first.is_visible(
                timeout=800
            )
        except Exception:  # noqa: BLE001
            return False
        if visible and not self._challenge_logged:
            self._challenge_logged = True
            log.warning("ปริศนายืนยันตัวตนโผล่อยู่ — เบราว์เซอร์หยุดมือ รอคนมาเลื่อน")
        elif not visible:
            self._challenge_logged = False
        return bool(visible)

    async def ready(self) -> bool:
        return self.page is not None and not await self.hands_off()


_SESSIONS: dict[str, BrowserSession] = {}


def get_session(opts: dict[str, Any]) -> BrowserSession:
    """เบราว์เซอร์บานเดียวใช้ร่วมกัน — ปักตะกร้ากับตอบคอมเมนท์อยู่หน้าเดียวกันอยู่แล้ว"""
    cfg = _SessionConfig(
        profile_dir=str(opts.get("profile_dir", DEFAULT_PROFILE_DIR)),
        console_url=str(opts.get("console_url", "")),
        headless=bool(opts.get("headless", False)),
        timeout_ms=int(opts.get("timeout_ms", 8000)),
        slow_mo_ms=int(opts.get("slow_mo_ms", 120)),
        dry_run=bool(opts.get("dry_run", True)),
        selectors=_selectors(opts.get("selectors") or {}),
    )
    session = _SESSIONS.get(cfg.profile_dir)
    if session is None:
        session = BrowserSession(cfg)
        _SESSIONS[cfg.profile_dir] = session
    return session


# ---------------------------------------------------------------- ปักตะกร้า


class BrowserShop:
    """ปักตะกร้าด้วยการกดปุ่มเดิมที่คนเคยกด"""

    def __init__(self, **opts: Any) -> None:
        self.session = get_session(opts)
        self.sel = self.session.selectors
        self._warned = False
        self._pinned_id = ""

    async def connect(self) -> bool:
        if not (self.sel.pin_button and (self.sel.product_row or self.sel.product_row_template)):
            log.error(
                "ยังไม่ได้ calibrate ปุ่มปักตะกร้า — ระบบจะไม่ปักให้ ต้องปักเอง "
                "(รัน python scripts/calibrate_browser.py)"
            )
            return False
        return await self.session.connect()

    async def disconnect(self) -> None:
        await self.session.disconnect()

    async def pin_basket(self, basket_id: str, sku: str, name: str = "") -> bool:
        if not await self.session.ready():
            self._warn_once()
            return False

        row = await self._find_row(sku, name)
        if row is None:
            log.warning("หาแถวสินค้า %s (%s) ในหน้าเว็บไม่เจอ", sku, name or "-")
            return False

        button = row.locator(self.sel.pin_button).first
        if self.session.cfg.dry_run:
            log.info("[ซ้อม] จะกดปักตะกร้า %s (%s) — ยังไม่กดจริง", name or sku, sku)
            self._pinned_id = basket_id
            return True

        try:
            await button.click()
        except Exception as exc:  # noqa: BLE001
            log.warning("กดปักตะกร้า %s ไม่สำเร็จ: %s", sku, exc)
            return False

        self._pinned_id = basket_id
        if self.sel.pinned_marker:
            await self._verify_pinned(row, sku)
        return True

    async def _verify_pinned(self, row: Any, sku: str) -> None:
        """กดแล้วติดจริงไหม — หน้าเว็บกดติดบ้างไม่ติดบ้างเป็นเรื่องปกติ"""
        try:
            ok = await row.locator(self.sel.pinned_marker).first.is_visible(timeout=2500)
        except Exception:  # noqa: BLE001
            ok = False
        if not ok:
            log.warning("กดปักตะกร้า %s แล้วแต่ยังไม่เห็นเครื่องหมายว่าปักติด", sku)

    async def _find_row(self, sku: str, name: str) -> Any:
        page = self.session.page
        if self.sel.product_row_template:
            candidate = page.locator(self.sel.product_row_template.format(sku=sku)).first
            if await self._exists(candidate):
                return candidate
        if not self.sel.product_row:
            return None
        rows = page.locator(self.sel.product_row)
        for needle in (sku, name):
            if not needle:
                continue
            candidate = rows.filter(has_text=needle).first
            if await self._exists(candidate):
                return candidate
        return None

    @staticmethod
    async def _exists(locator: Any) -> bool:
        try:
            return await locator.count() > 0
        except Exception:  # noqa: BLE001
            return False

    async def fetch_sales(self, basket_id: str) -> SalesSnapshot | None:
        """ยอดขายยังไม่ได้อ่านจากหน้าเว็บ

        ตัวเลขบนหน้าไลฟ์อัปเดตช้าและกวาดมาผิดง่าย ยอมไม่รู้ดีกว่ารู้ผิด
        แล้วเอาไปคิดค่าแอด
        """
        return None

    def _warn_once(self) -> None:
        if not self._warned:
            self._warned = True
            log.warning("เบราว์เซอร์ยังไม่พร้อม (หรือติดปริศนาอยู่) — ปักตะกร้าเองไปก่อน")


# ---------------------------------------------------------------- ตอบคอมเมนท์


class BrowserCommentSender:
    """พิมพ์คำตอบลงช่องคอมเมนท์แล้วส่ง"""

    def __init__(self, **opts: Any) -> None:
        self.session = get_session(opts)
        self.sel = self.session.selectors
        self.min_interval = float(opts.get("min_send_interval_seconds", 3.0))
        self.mention_prefix = bool(opts.get("mention_prefix", True))
        self._last_sent_at = 0.0
        self._warned = False

    async def connect(self) -> bool:
        if not self.sel.comment_box:
            log.error(
                "ยังไม่ได้ calibrate ช่องคอมเมนท์ — ระบบจะร่างคำตอบไว้ให้คนกดส่งเอง "
                "(รัน python scripts/calibrate_browser.py)"
            )
            return False
        return await self.session.connect()

    async def disconnect(self) -> None:
        await self.session.disconnect()

    async def send(self, text: str, *, reply_to: str = "") -> bool:
        now = time.time()
        if now - self._last_sent_at < self.min_interval:
            return False  # พิมพ์รัวเกินคนจะดูเป็นบอททันที
        if not await self.session.ready():
            self._warn_once()
            return False

        message = f"@{reply_to} {text}".strip() if (self.mention_prefix and reply_to) else text

        if self.session.cfg.dry_run:
            log.info("[ซ้อม] จะส่งคอมเมนท์: %s — ยังไม่ส่งจริง", message)
            self._last_sent_at = now
            return True

        page = self.session.page
        try:
            box = page.locator(self.sel.comment_box).first
            await box.click()
            await box.fill("")
            await box.type(message, delay=45)
            if self.sel.comment_send:
                await page.locator(self.sel.comment_send).first.click()
            else:
                await box.press("Enter")
        except Exception as exc:  # noqa: BLE001
            log.warning("ส่งคอมเมนท์ไม่สำเร็จ: %s", exc)
            return False

        self._last_sent_at = now
        return True

    def _warn_once(self) -> None:
        if not self._warned:
            self._warned = True
            log.warning("เบราว์เซอร์ยังไม่พร้อม (หรือติดปริศนาอยู่) — คอมเมนท์รอบนี้ไม่ได้ตอบ")
