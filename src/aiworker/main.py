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

from .fleet import FleetHub, FleetReporter
from .runtime import Shift, build_shift
from .settings import load_settings
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
    line("เครื่อง", s.verification.machine_name or "(ยังไม่ตั้งชื่อ)", True)
    line(
        "ตะกร้าในไลฟ์",
        f"{len(shift.state.baskets.baskets)} ใบ",
        len(shift.state.baskets.baskets) > 0,
    )
    segs = shift.state.playlist.segments
    minutes = shift.state.playlist.total_duration() / 60.0
    line("คลิปสำหรับรีรัน", f"{len(segs)} ท่อน รวม {minutes:.0f} นาที", len(segs) > 0)

    # ตรวจว่าคิวชีตชี้ไปยังตะกร้าที่มีจริงหรือเปล่า — พลาดตรงนี้คือปักตะกร้าผิดทั้งกะ
    known = shift.state.baskets.known_skus()
    cue_problems: list[str] = []
    cued = 0
    for seg in segs:
        cue_problems += [f"[{seg.id}] {p}" for p in seg.cues.validate_against(known)]
        cued += len(seg.cues.cues)
        if not seg.cues and seg.sku and seg.sku not in known:
            cue_problems.append(f"[{seg.id}] sku {seg.sku} ไม่มีในรายการตะกร้า")
    line("คิวชีต (จุดเปลี่ยนตะกร้า)", f"{cued} จุด", not cue_problems)
    for problem in cue_problems[:8]:
        print(f"       ↳ {problem}")

    # ── เพดานค่าแอด: ตัวเลขที่ผิดแล้วเผาเงินเร็วที่สุดในระบบ ──
    board = shift.state.baskets
    missing = board.missing_commission()
    commissions = [b.commission for b in board.baskets if b.commission > 0]
    if commissions:
        lowest = min(commissions)
        ceiling = lowest * s.ads.cpa_ceiling_ratio
        line(
            "ค่าคอมต่อชิ้น",
            f"{lowest:.0f}-{max(commissions):.0f} บาท "
            f"→ เพดานค่าแอดเริ่มต้น {ceiling:.0f} บาท/ออเดอร์",
            not missing,
        )
    else:
        line("ค่าคอมต่อชิ้น", "ยังไม่ได้กรอกเลยสักใบ", False)

    if missing:
        print(f"       ↳ ยังไม่ได้กรอก commission {len(missing)} ใบ:")
        for basket in missing[:5]:
            print(f"          {basket.sku:<12} {basket.name}")
        print(
            f"       ↳ ระบบจะใช้ค่าเริ่มต้น {s.ads.default_commission:.0f} บาทแทน "
            "ซึ่งอาจสูงกว่าจริง แล้วยอมจ่ายค่าแอดแพงเกินไป"
        )

    line(
        "เบรกกันขาดทุน",
        f"หยุดยิงแอดเมื่อขาดทุนสะสมถึง {s.ads.max_loss_baht:.0f} บาท",
        s.ads.max_loss_baht > 0,
    )

    line(
        "ตัวเฝ้าจิ๊กซอว์",
        f"{s.verification.watcher} (ให้เวลา {s.verification.deadline_seconds / 60:.0f} นาที)"
        if s.verification.enabled
        else "ปิดอยู่ — ต้องมีคนเฝ้าจอเองตลอด",
        s.verification.enabled,
    )
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

    covered = {sku for seg in segs for sku in seg.all_skus() if sku}
    never = [b for b in shift.state.baskets.baskets if b.sku and b.sku not in covered]
    if never:
        print("\n  ตะกร้าที่ไม่มีคลิปพูดถึงเลย (จะไม่ถูกปักทั้งกะ):")
        for basket in never:
            print(f"     {basket.sku:<12} {basket.name}")

    if commissions:
        print("\n  เพดานค่าแอดของสินค้าแต่ละตัว (จ่ายเกินนี้ = ขาดทุนต่อออเดอร์):")
        for basket in sorted(shift.state.baskets.baskets, key=lambda b: b.commission):
            if not basket.commission:
                continue
            cap = basket.commission * s.ads.cpa_ceiling_ratio
            print(
                f"     {basket.sku:<12} คอม {basket.commission:>3.0f}฿ "
                f"→ ค่าแอดไม่เกิน {cap:>5.1f}฿/ออเดอร์   {basket.name}"
            )
        print(
            "\n   ระบบใช้ค่าคอมของสินค้าที่ขายได้จริงมาคิดเพดานแบบถ่วงน้ำหนัก\n"
            "   ตอนยังไม่มีออเดอร์จะใช้ตัวที่ค่าคอมต่ำสุด (ระวังไว้ก่อน)"
        )

    print(
        "\n" + ("พร้อมเข้ากะ" if ok else "ยังขาดบางอย่าง — ดูรายการ ✗ ด้านบน") + "\n"
    )
    return 0 if ok else 1


async def _run_hub(settings) -> None:
    """โหมด hub — ไม่ไลฟ์เอง แค่รวมจอของเครื่องอื่น"""
    hub = FleetHub(
        settings.fleet.hub_host,
        settings.fleet.hub_port,
        settings.fleet.offline_after_seconds,
    )
    if not hub.start():
        return
    print(
        f"\nจอรวมพร้อมแล้ว เปิดที่ http://{settings.fleet.hub_host}:"
        f"{settings.fleet.hub_port}\nกด Ctrl+C เพื่อปิด\n",
        flush=True,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())
    try:
        await stop.wait()
    finally:
        hub.stop()


async def _run(shift: Shift, minutes: float | None) -> None:
    dashboard: Dashboard | None = None
    if shift.settings.web.enabled:
        dashboard = Dashboard(
            shift.snapshot, shift.settings.web.host, shift.settings.web.port
        )
        dashboard.start()

    reporter: FleetReporter | None = None
    fleet = shift.settings.fleet
    if fleet.role == "worker" and fleet.hub_url:
        reporter = FleetReporter(
            fleet.hub_url,
            fleet.machine_name or shift.settings.shop.channel_id,
            shift.snapshot,
            fleet.report_seconds,
        )
        reporter.start()

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
        if reporter:
            await reporter.stop()
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
    parser.add_argument(
        "--hub", action="store_true", help="รันเป็นจอรวม ไม่ไลฟ์เอง (คุมหลายเครื่อง)"
    )
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)

    if args.hub:
        try:
            asyncio.run(_run_hub(load_settings(args.config)))
        except KeyboardInterrupt:
            pass
        return 0

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
