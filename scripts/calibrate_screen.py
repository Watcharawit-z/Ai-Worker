#!/usr/bin/env python3
"""ตั้งค่าตัวเฝ้าจอสำหรับจับปริศนาจิ๊กซอว์

เครื่องมือนี้ช่วยเก็บ "ภาพตัวอย่าง" ของปริศนา เพื่อให้ระบบรู้จักหน้าตามัน
ต้องทำครั้งเดียวต่อเครื่อง (ถ้าเปลี่ยนความละเอียดจอต้องทำใหม่)

    pip install mss pillow
    python scripts/calibrate_screen.py

หมายเหตุ: เครื่องมือนี้และตัวเฝ้าจอ **อ่านภาพอย่างเดียว**
ไม่มีส่วนไหนควบคุมเมาส์หรือคีย์บอร์ด — ปริศนายังต้องให้คนเลื่อนเอง
สิ่งที่ระบบทำคือปลุกคนให้ทันภายในเวลาที่ TikTok ให้
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

REFERENCE_DIR = Path("config/reference")


def main() -> int:
    try:
        import mss
        from PIL import Image
    except ImportError:
        print("ต้องติดตั้งก่อน:  pip install mss pillow")
        return 1

    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    with mss.mss() as sct:
        monitors = sct.monitors[1:]
        print("\nจอที่เจอ:")
        for i, mon in enumerate(monitors, 1):
            print(f"  [{i}] {mon['width']}x{mon['height']} ที่ ({mon['left']},{mon['top']})")

        choice = input(f"\nเลือกจอที่เปิด TikTok Live Studio อยู่ [1-{len(monitors)}]: ").strip()
        try:
            monitor = monitors[int(choice) - 1]
        except (ValueError, IndexError):
            print("เลือกไม่ถูกต้อง")
            return 1

        print(
            "\n─────────────────────────────────────────────\n"
            "ขั้นตอนต่อไป:\n"
            "  1. รอจนปริศนาจิ๊กซอว์เด้งขึ้นมาบนจอ\n"
            "  2. กลับมาที่หน้าต่างนี้แล้วกด Enter (อย่าเพิ่งเลื่อนปริศนา)\n"
            "  3. ระบบจะถ่ายภาพเก็บไว้ แล้วค่อยไปเลื่อนให้ทัน\n"
            "\nเก็บไว้ 3-5 ภาพจากคนละครั้งจะแม่นที่สุด\n"
            "กด Ctrl+C เมื่อพอแล้ว\n"
            "─────────────────────────────────────────────\n"
        )

        shots = sorted(REFERENCE_DIR.glob("puzzle_*.png"))
        count = len(shots)
        if count:
            print(f"(มีภาพเก่าอยู่แล้ว {count} ภาพ)\n")

        try:
            while True:
                input(f"เห็นปริศนาแล้วกด Enter เพื่อเก็บภาพที่ {count + 1} > ")
                raw = sct.grab(monitor)
                image = Image.frombytes("RGB", raw.size, raw.rgb)
                count += 1
                path = REFERENCE_DIR / f"puzzle_{count:02d}.png"
                image.save(path)
                print(f"  เก็บแล้ว: {path}  ({image.width}x{image.height})")
                print("  ไปเลื่อนปริศนาให้ทันก่อนนะ แล้วค่อยกลับมา\n")
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass

    if count == 0:
        print("\nยังไม่ได้เก็บภาพเลย")
        return 1

    templates = [str(p) for p in sorted(REFERENCE_DIR.glob("puzzle_*.png"))]
    print(
        "\n─────────────────────────────────────────────\n"
        f"เก็บครบ {len(templates)} ภาพ — เอาค่านี้ไปใส่ใน config.yaml:\n\n"
        "verification:\n"
        "  watcher: template\n\n"
        "adapters:\n"
        "  options:\n"
        "    screen:\n"
        f"      region: [{monitor['left']}, {monitor['top']}, "
        f"{monitor['width']}, {monitor['height']}]\n"
        "      threshold: 0.82\n"
        "      templates:\n"
        + "".join(f'        - "{t}"\n' for t in templates)
        + "\n"
        "จากนั้นทดสอบด้วย:  python -m aiworker --minutes 10\n"
        "แล้วรอดูว่าปริศนาครั้งถัดไปถูกจับได้ไหม\n"
        "ถ้าจับไม่ได้ ให้ลด threshold ลงทีละ 0.05\n"
        "ถ้าเตือนผิดบ่อย (ไม่มีปริศนาแต่แจ้ง) ให้เพิ่ม threshold ขึ้น\n"
        "─────────────────────────────────────────────\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
