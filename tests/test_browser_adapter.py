"""เทสตัวปักตะกร้า/ตอบคอมเมนท์ผ่านเบราว์เซอร์ โดยไม่ต้องเปิดเบราว์เซอร์จริง

เรื่องที่ต้องไม่พลาดเด็ดขาด: ถ้าปริศนาจิ๊กซอว์โผล่ ต้องไม่แตะหน้าเว็บเลย
"""

from __future__ import annotations

import pytest

from aiworker.adapters.browser import BrowserCommentSender, BrowserShop

# ---------------------------------------------------------------- ของปลอม


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str, needle: str = "") -> None:
        self.page = page
        self.selector = selector
        self.needle = needle

    @property
    def first(self) -> "FakeLocator":
        return self

    def locator(self, selector: str) -> "FakeLocator":
        return FakeLocator(self.page, selector)

    def filter(self, has_text: str = "") -> "FakeLocator":
        return FakeLocator(self.page, self.selector, has_text)

    async def count(self) -> int:
        if self.needle:
            return sum(1 for row in self.page.rows if self.needle in row)
        return self.page.counts.get(self.selector, 0)

    async def is_visible(self, timeout: float = 0) -> bool:
        return self.page.visible.get(self.selector, False)

    async def click(self) -> None:
        self.page.clicks.append(self.selector)

    async def fill(self, value: str) -> None:
        self.page.typed.append("")

    async def type(self, text: str, delay: float = 0) -> None:
        self.page.typed.append(text)

    async def press(self, key: str) -> None:
        self.page.keys.append(key)


class FakePage:
    def __init__(self, rows: list[str] | None = None) -> None:
        self.rows = rows or []
        self.counts: dict[str, int] = {}
        self.visible: dict[str, bool] = {}
        self.clicks: list[str] = []
        self.typed: list[str] = []
        self.keys: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


SELECTORS = {
    "product_row": ".row",
    "pin_button": "button.pin",
    "comment_box": "div.chat-input",
    "captcha": "div.captcha",
}


def make_shop(page: FakePage, *, tag: str, dry_run: bool = False, **extra) -> BrowserShop:
    shop = BrowserShop(
        profile_dir=f"runtime/test-{tag}",
        dry_run=dry_run,
        selectors={**SELECTORS, **extra.get("selectors", {})},
    )
    shop.session.page = page
    return shop


def make_sender(page: FakePage, *, tag: str, dry_run: bool = False, **opts) -> BrowserCommentSender:
    sender = BrowserCommentSender(
        profile_dir=f"runtime/test-{tag}",
        dry_run=dry_run,
        selectors=SELECTORS,
        **opts,
    )
    sender.session.page = page
    return sender


# ---------------------------------------------------------------- ขอบเขต


async def test_ไม่แตะอะไรเลยเมื่อจิ๊กซอว์โผล่():
    page = FakePage(rows=["SKU-A ครีมกันแดด"])
    page.counts[".row"] = 1
    page.visible["div.captcha"] = True

    shop = make_shop(page, tag="captcha")
    assert await shop.pin_basket("b1", "SKU-A", "ครีมกันแดด") is False

    sender = make_sender(page, tag="captcha")
    assert await sender.send("สวัสดีค่ะ", reply_to="ploy") is False

    assert page.clicks == []
    assert page.typed == []
    assert page.keys == []


async def test_กลับมาทำงานต่อเมื่อจิ๊กซอว์หายไป():
    page = FakePage(rows=["SKU-A ครีมกันแดด"])
    page.counts[".row"] = 1
    page.visible["div.captcha"] = True

    shop = make_shop(page, tag="captcha-gone")
    assert await shop.pin_basket("b1", "SKU-A") is False

    page.visible["div.captcha"] = False
    assert await shop.pin_basket("b1", "SKU-A") is True
    assert page.clicks == ["button.pin"]


# ---------------------------------------------------------------- ปักตะกร้า


async def test_หาแถวสินค้าจาก_sku_แล้วกดปุ่มปัก():
    page = FakePage(rows=["SKU-A ครีมกันแดด", "SKU-B เซรั่ม"])
    page.counts[".row"] = 2

    shop = make_shop(page, tag="pin-sku")
    assert await shop.pin_basket("b2", "SKU-B", "เซรั่ม") is True
    assert page.clicks == ["button.pin"]


async def test_ถ้าไม่มี_sku_ในหน้าเว็บให้หาจากชื่อสินค้าแทน():
    page = FakePage(rows=["ครีมกันแดด SPF50", "เซรั่มวิตซี"])
    page.counts[".row"] = 2

    shop = make_shop(page, tag="pin-name")
    assert await shop.pin_basket("b2", "SKU-B", "เซรั่มวิตซี") is True
    assert page.clicks == ["button.pin"]


async def test_หาแถวไม่เจอต้องตอบว่าไม่สำเร็จ_ไม่ใช่แกล้งทำเป็นสำเร็จ():
    page = FakePage(rows=["ครีมกันแดด"])
    page.counts[".row"] = 1

    shop = make_shop(page, tag="pin-miss")
    assert await shop.pin_basket("b9", "SKU-Z", "ของที่ไม่มีในหน้าเว็บ") is False
    assert page.clicks == []


async def test_โหมดซ้อมไม่กดจริง():
    page = FakePage(rows=["SKU-A ครีมกันแดด"])
    page.counts[".row"] = 1

    shop = make_shop(page, tag="pin-dry", dry_run=True)
    assert await shop.pin_basket("b1", "SKU-A") is True
    assert page.clicks == []


async def test_ยังไม่ได้_calibrate_ต้องต่อไม่ผ่าน():
    shop = BrowserShop(profile_dir="runtime/test-uncalibrated", selectors={})
    assert await shop.connect() is False

    sender = BrowserCommentSender(profile_dir="runtime/test-uncalibrated2", selectors={})
    assert await sender.connect() is False


# ---------------------------------------------------------------- คอมเมนท์


async def test_ส่งคอมเมนท์พิมพ์แล้วกด_enter():
    page = FakePage()
    sender = make_sender(page, tag="say")
    assert await sender.send("ราคา 390 ค่ะ", reply_to="ploy") is True
    assert page.typed[-1] == "@ploy ราคา 390 ค่ะ"
    assert page.keys == ["Enter"]


async def test_ส่งถี่เกินไปต้องถูกกั้น():
    page = FakePage()
    sender = make_sender(page, tag="rate", min_send_interval_seconds=60.0)
    assert await sender.send("อันแรก") is True
    assert await sender.send("อันที่สอง") is False
    assert page.typed.count("อันที่สอง") == 0


async def test_ปิด_mention_prefix_ได้():
    page = FakePage()
    sender = make_sender(page, tag="nomention", mention_prefix=False)
    assert await sender.send("ราคา 390 ค่ะ", reply_to="ploy") is True
    assert page.typed[-1] == "ราคา 390 ค่ะ"


# ---------------------------------------------------------------- ยอดขาย


async def test_ยอดขายจากเบราว์เซอร์ยังไม่อ่าน_ต้องตอบว่าไม่รู้():
    page = FakePage()
    shop = make_shop(page, tag="sales")
    assert await shop.fetch_sales("b1") is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
