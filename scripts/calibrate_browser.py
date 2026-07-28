#!/usr/bin/env python3
"""สอนระบบว่าปุ่มไหนอยู่ตรงไหนในหน้าเว็บ TikTok

ทำครั้งเดียวต่อเครื่อง (ต้องทำใหม่เมื่อ TikTok เปลี่ยนหน้าตาเว็บ)

    pip install playwright
    playwright install chromium
    python scripts/calibrate_browser.py

วิธีใช้:
1. เบราว์เซอร์จะเปิดขึ้นมา — ล็อกอิน TikTok ให้เรียบร้อย
   (ล็อกอินค้างไว้ในโปรไฟล์ ครั้งหน้าไม่ต้องล็อกอินอีก)
2. เปิดหน้าที่ปักตะกร้าและตอบคอมเมนท์ได้
3. **กด Alt ค้างแล้วคลิกที่ปุ่ม** ที่ต้องการสอน — สคริปต์จะพิมพ์ selector ออกมา
4. ทำครบทุกช่องตามที่มันถามแล้วเอาผลลัพธ์ไปใส่ config.yaml

เก็บให้ครบ:
  - แถวสินค้าหนึ่งรายการ (คลิกที่กรอบของแถว ไม่ใช่ปุ่ม)
  - ปุ่มปักหมุดในแถวนั้น
  - ช่องพิมพ์คอมเมนท์
  - กรอบปริศนาจิ๊กซอว์ (รอให้มันเด้งแล้วค่อยคลิกกรอบ — สำคัญ)

ปริศนาจิ๊กซอว์เก็บ selector ไว้เพื่อให้ระบบ **หยุดมือ** เมื่อมันโผล่
ไม่ใช่เพื่อไปเลื่อนมัน — ดู docs/JIGSAW.md
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROFILE_DIR = Path("runtime/browser-profile")

# หา selector ที่ทนต่อการเปลี่ยนหน้าเว็บที่สุดเท่าที่จะหาได้
PICKER_JS = r"""
() => {
  const stable = ['data-testid','data-e2e','data-tid','data-sku','data-id','name','role','aria-label'];
  function selectorFor(el) {
    if (el.id && !/^[0-9]/.test(el.id)) return '#' + CSS.escape(el.id);
    for (const attr of stable) {
      const v = el.getAttribute(attr);
      if (v) return `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(v)}"]`;
    }
    const cls = (el.className || '').toString().trim().split(/\s+/)
      .filter(c => c && !/\d{4,}|^css-|^sc-/.test(c));
    if (cls.length) return el.tagName.toLowerCase() + '.' + cls.map(CSS.escape).join('.');
    return null;
  }
  function path(el) {
    const parts = [];
    let node = el;
    for (let i = 0; node && node.nodeType === 1 && i < 5; i++) {
      const own = selectorFor(node);
      parts.unshift(own || node.tagName.toLowerCase());
      if (own && own.startsWith('#')) break;
      node = node.parentElement;
    }
    return parts.join(' > ');
  }
  document.addEventListener('click', (ev) => {
    if (!ev.altKey) return;
    ev.preventDefault();
    ev.stopPropagation();
    const el = ev.target;
    const text = (el.innerText || '').trim().split('\n')[0].slice(0, 40);
    window.__aiworker_pick(JSON.stringify({
      selector: path(el),
      text,
      tag: el.tagName.toLowerCase(),
    }));
  }, true);
  return true;
}
"""

WANTED = [
    ("product_row", "กรอบของ 'แถวสินค้าหนึ่งรายการ' ในลิสต์ตะกร้า (คลิกที่แถว ไม่ใช่ปุ่ม)"),
    ("pin_button", "ปุ่มปักหมุด/ปักตะกร้า ที่อยู่ในแถวนั้น"),
    ("pinned_marker", "เครื่องหมายว่าแถวนี้ปักอยู่ (ไม่มีก็ข้ามได้ — พิมพ์ skip)"),
    ("comment_box", "ช่องพิมพ์คอมเมนท์"),
    ("comment_send", "ปุ่มส่งคอมเมนท์ (ถ้าใช้ Enter อย่างเดียว พิมพ์ skip)"),
    ("captcha", "กรอบปริศนาจิ๊กซอว์ — ต้องรอให้มันเด้งก่อน (ข้ามได้แต่ไม่ควรข้าม)"),
]


async def run(url: str) -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("ต้องติดตั้งก่อน:  pip install playwright && playwright install chromium")
        return 1

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    picked: dict[str, str] = {}
    last: dict[str, str] = {}
    loop = asyncio.get_running_loop()

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, viewport={"width": 1440, "height": 900}
        )
        page = context.pages[0] if context.pages else await context.new_page()

        def on_pick(payload: str) -> None:
            import json

            data = json.loads(payload)
            last.clear()
            last.update(data)
            print(f"\n  เลือก: <{data['tag']}> {data['text']!r}")
            print(f"  selector: {data['selector']}\n")

        await context.expose_function("__aiworker_pick", on_pick)
        await context.add_init_script(f"({PICKER_JS})()")

        if url:
            await page.goto(url, wait_until="domcontentloaded")
        await page.evaluate(PICKER_JS)  # หน้าที่เปิดค้างอยู่แล้วก็ให้ใช้ได้

        print("=" * 62)
        print("  ล็อกอินและเปิดหน้าที่ปักตะกร้าได้ก่อน")
        print("  แล้ว 'กด Alt ค้าง + คลิก' ที่ปุ่มตามที่ถามทีละข้อ")
        print("=" * 62)

        for key, prompt in WANTED:
            print(f"\n[{key}] {prompt}")
            while True:
                answer = await loop.run_in_executor(
                    None, input, "  คลิกแล้วกด Enter (หรือพิมพ์ skip): "
                )
                if answer.strip().lower() == "skip":
                    break
                if last.get("selector"):
                    picked[key] = last["selector"]
                    last.clear()
                    break
                print("  ยังไม่ได้คลิกอะไรเลย — กด Alt ค้างแล้วคลิกที่ปุ่มก่อน")

        await context.close()

    print("\n" + "=" * 62)
    print("เอาส่วนนี้ไปวางใน config/config.yaml ใต้ adapters.options:")
    print("=" * 62)
    print("    browser:")
    print(f'      console_url: "{url or "<ใส่ URL หน้าที่ปักตะกร้า>"}"')
    print("      dry_run: true          # ซ้อมก่อน ดูว่ามันหาปุ่มเจอไหม")
    print("      selectors:")
    for key, _ in WANTED:
        if key in picked:
            print(f'        {key}: "{picked[key]}"')
    if "captcha" not in picked:
        print("\n⚠ ยังไม่ได้เก็บ selector ของปริศนาจิ๊กซอว์")
        print("  ระบบจะไม่รู้ว่าต้องหยุดมือตอนไหน — ควรรันใหม่ตอนที่ปริศนาเด้ง")
    print("\nอย่าเพิ่งเปลี่ยน dry_run เป็น false จนกว่าจะเห็นในล็อกว่ามันหาปุ่มถูกทุกครั้ง")
    return 0


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        return asyncio.run(run(url))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
