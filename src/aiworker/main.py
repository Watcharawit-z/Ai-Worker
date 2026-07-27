"""จุดเริ่มโปรแกรม

    python -m aiworker                 # เข้ากะ (ใช้ config/config.yaml)
    python -m aiworker --config x.yaml # ระบุ config เอง
    python -m aiworker --check         # ตรวจความพร้อมแล้วออก ไม่เข้ากะ
    python -m aiworker --minutes 5     # ทดลองรัน 5 นาทีแล้วหยุดเอง
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from .runtime import Shift, build_shift
from .web.server import Dashboard


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)-28s %(message)s",
        datefmt="%H:%M:%S",
    )
    # ลด noise จากไลบรารีภายนอก
    for noisy in ("httpx", "httpcore", "anthropic", "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _check(shift: Shift) -> int:
    """ตรวจความพร้อมก่อนใช้งานจริง"""
    print("\n=== ตรวจความพร้อมของแผนก ===\n")
    s = shift.settings
    ok = True

    def line(label: str, value: str, good: bool) -> None:
        nonlocal ok
        print(f"  {'✓' if good else '✗'}  {label:<28} {value}")
        if not good:
            ok = False

    line("ช่อง", s.shop.channel_id, bool(s.shop.channel_id))
    line(
        "ตะกร้าในคิว",
        f"{len(shift.state.baskets.baskets)} ใบ",
        len(shift.state.baskets.baskets) > 0,
    )
    segs = shift.state.playlist.segments
    minutes = shift.state.playlist.total_duration() / 60.0
    line("คลิปสำหรับรีรัน", f"{len(segs)} ท่อน รวม {minutes:.0f} นาที", len(segs) > 0)
    line(
        "ข้อมูลสินค้า",
        f"{len(shift.knowledge.get('products', []))} รายการ",
        bool(shift.knowledge.get("products")),
    )
    line(
        "คำตอบสำเร็จรูป",
        f"{len(shift.knowledge.get('faq', []))} ข้อ",
        bool(shift.knowledge.get("faq")),
    )
    line(
        "AI ตอบคอมเมนท์",
        s.llm.model if shift.llm.available else "ปิด (ไม่พบ ANTHROPIC_API_KEY)",
        shift.llm.available,
    )
    print()
    line("ตัวเล่นวิดีโอ", s.adapters.player, True)
    line("แหล่งคอมเมนท์", s.adapters.comments, True)
    line("ระบบร้าน", s.adapters.shop, True)
    line("ระบบแอด", s.adapters.ads, True)
    line("แจ้งเตือน", ", ".join(s.adapters.notify), bool(s.adapters.notify))

    if segs:
        print("\n  ความยาวคลิปแยกตามสินค้า:")
        for sku, seconds in sorted(shift.state.playlist.coverage_by_sku().items()):
            mark = "!" if seconds < 600 else " "
            label = sku if sku != "_generic" else "(ท่อนกลาง ไม่ผูกสินค้า)"
            print(f"   {mark} {label:<24} {seconds / 60:.0f} นาที")
        print("\n   ! = คลิปน้อยกว่า 10 นาที จะวนซ้ำเร็ว คนดูจับได้ง่าย")

    print(
        "\n" + ("พร้อมเข้ากะ" if ok else "ยังขาดบางอย่าง — ดูรายการ ✗ ด้านบน") + "\n"
    )
    return 0 if ok else 1


async def _run(shift: Shift, minutes: float | None) -> None:
    dashboard: Dashboard | None = None
    if shift.settings.web.enabled:
        dashboard = Dashboard(
            shift.snapshot, shift.settings.web.host, shift.settings.web.port
        )
        dashboard.start()

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _request_stop() -> None:
        print("\nได้รับสัญญาณปิด — กำลังปิดกะ…", flush=True)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # Windows
            signal.signal(sig, lambda *_: _request_stop())

    await shift.start()
    try:
        if minutes:
            await asyncio.wait_for(stop.wait(), timeout=minutes * 60)
        else:
            await stop.wait()
    except asyncio.TimeoutError:
        print(f"\nครบ {minutes:.0f} นาทีตามที่สั่ง — ปิดกะ", flush=True)
    finally:
        await shift.stop()
        if dashboard:
            dashboard.stop()
        print("\n=== สรุปกะ ===")
        print(" ", shift.summary_line())
        usage = shift.llm.usage_summary()
        if usage["enabled"]:
            print(
                f"  ค่า AI ประมาณ ${usage['estimated_cost_usd']:.4f} "
                f"จาก {usage['calls']} ครั้ง"
            )
        print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="aiworker", description="พนักงาน AI สำหรับแผนกรีรันไลฟ์ TikTok"
    )
    parser.add_argument("--config", help="ที่อยู่ไฟล์ config (ค่าเริ่มต้น: config/config.yaml)")
    parser.add_argument("--check", action="store_true", help="ตรวจความพร้อมแล้วออก")
    parser.add_argument("--minutes", type=float, help="รันกี่นาทีแล้วหยุดเอง (ใช้ทดลอง)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log ละเอียด")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    shift = build_shift(args.config)

    if args.check:
        return _check(shift)

    try:
        asyncio.run(_run(shift, args.minutes))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
