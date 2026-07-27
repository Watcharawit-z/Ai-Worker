"""พนักงานเฝ้าไลฟ์ — คนที่ต่อจิ๊กซอว์

หน้าที่:
1. เฝ้าสตรีมว่ายังไหลอยู่ไหม (bitrate/เฟรมตก/ค้าง)
2. ต่อท่อนวิดีโอถัดไปก่อนท่อนปัจจุบันจะหมด — ห้ามให้จอดำแม้แต่วินาทีเดียว
3. เลือกท่อนให้ตรงกับตะกร้าที่ขึ้นอยู่ และไม่ซ้ำจนคนดูจับได้
4. สั่งสลับท่อน/หยุดสตรีม เมื่อฝ่ายตรวจการละเมิดสั่งมา
"""

from __future__ import annotations

import time

from ..adapters.base import PlayerAdapter
from ..events import (
    BasketActivated,
    ComplianceAction,
    Event,
    SegmentChanged,
    Severity,
    StreamAlert,
    StreamHealth,
)
from ..settings import Settings
from ..state import ShiftState
from .base import Agent


class LiveWatcherAgent(Agent):
    """แทน 'คนเฝ้าไลฟ์' ที่ต้องนั่งดูจอทั้งกะ"""

    name = "live_watcher"
    subscribes = ("basket.activated", "compliance.action")

    # เริ่มหาท่อนถัดไปก่อนท่อนปัจจุบันจบกี่วินาที
    PREROLL_SECONDS = 8.0

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        player: PlayerAdapter,
    ) -> None:
        super().__init__(bus, state, settings)
        self.player = player
        self.tick_interval = settings.stream.health_interval_seconds
        self._last_healthy_at = time.time()
        self._segment_started_at = 0.0
        self._alerted: dict[str, float] = {}

    async def on_start(self) -> None:
        await self.player.connect()
        await self._advance_segment(reason="เริ่มกะ")

    async def on_stop(self) -> None:
        await self.player.disconnect()

    # ---------------- ตอบสนอง event ----------------

    async def handle(self, event: Event) -> None:
        if isinstance(event, BasketActivated):
            # ขึ้นตะกร้าใหม่ = ต้องสลับไปท่อนที่พูดถึงสินค้าตัวนั้น
            await self._advance_segment(
                reason=f"ขึ้นตะกร้าใหม่: {event.name}", sku=event.sku, force=True
            )
        elif isinstance(event, ComplianceAction):
            await self._handle_compliance(event)

    async def _handle_compliance(self, event: ComplianceAction) -> None:
        if event.action == "switch_segment":
            await self._advance_segment(
                reason=f"เลี่ยงความเสี่ยง: {event.reason}", force=True
            )
            self.say(f"สลับท่อนวิดีโอหนีความเสี่ยง ({event.violation_code})", "warn")
        elif event.action == "stop_stream":
            await self.player.stop()
            self.state.stream.is_live = False
            self.say("หยุดสตรีมตามคำสั่งฝ่ายตรวจการละเมิด", "error")
            self.notify(
                "หยุดสตรีมฉุกเฉิน",
                f"เหตุผล: {event.reason}",
                Severity.CRITICAL,
                needs_human=True,
            )

    # ---------------- งานตามรอบ ----------------

    async def tick(self) -> None:
        health = await self.player.health()
        st = self.state.stream
        st.is_live = health.is_live
        st.bitrate_kbps = health.bitrate_kbps
        st.dropped_frames = health.dropped_frames
        st.fps = health.fps
        st.viewers = health.viewers
        st.current_segment = health.current_segment
        st.seconds_remaining = health.seconds_remaining
        st.last_update = time.time()

        self.emit(
            StreamHealth(
                is_live=health.is_live,
                bitrate_kbps=health.bitrate_kbps,
                dropped_frames=health.dropped_frames,
                fps=health.fps,
                viewers=health.viewers,
                current_segment=health.current_segment,
                seconds_into_segment=time.time() - self._segment_started_at,
                seconds_remaining=health.seconds_remaining,
            )
        )

        await self._check_health(health)
        await self._maybe_advance(health)

    async def _check_health(self, health) -> None:
        cfg = self.settings.stream
        now = time.time()

        if not health.is_live:
            self._raise_alert(
                "stream_down",
                Severity.CRITICAL,
                "สตรีมไม่ออกอากาศ — จอดำ ต้องรีบดู",
                notify=True,
            )
            await self._try_recover()
            return

        if health.bitrate_kbps < cfg.min_bitrate_kbps:
            self._raise_alert(
                "low_bitrate",
                Severity.HIGH,
                f"bitrate ต่ำผิดปกติ ({health.bitrate_kbps} kbps) ภาพอาจกระตุก",
            )
        elif health.dropped_frames > cfg.max_dropped_frames:
            self._raise_alert(
                "dropped_frames",
                Severity.MEDIUM,
                f"เฟรมตกสะสม {health.dropped_frames} เฟรม เน็ตอาจไม่นิ่ง",
            )
        else:
            self._last_healthy_at = now

        if now - self._last_healthy_at > cfg.stall_alert_after_seconds:
            self._raise_alert(
                "stalled",
                Severity.HIGH,
                f"สตรีมมีปัญหาต่อเนื่องเกิน {cfg.stall_alert_after_seconds:.0f} วินาที",
                notify=True,
            )

    async def _try_recover(self) -> None:
        """พยายามกู้เองก่อนค่อยเรียกคน — ส่วนใหญ่แค่สั่งเล่นใหม่ก็กลับมา"""
        ok = await self.player.start()
        if ok:
            self.say("สตรีมหลุด — สั่งเริ่มใหม่อัตโนมัติแล้ว", "warn")
            await self._advance_segment(reason="กู้สตรีมหลังหลุด", force=True)
        else:
            self.say("สั่งเริ่มสตรีมใหม่ไม่สำเร็จ ต้องให้คนเข้ามาดู", "error")

    async def _maybe_advance(self, health) -> None:
        """ต่อจิ๊กซอว์: ต่อท่อนถัดไปก่อนท่อนนี้จะหมด"""
        if not health.is_live:
            return
        elapsed = time.time() - self._segment_started_at
        if elapsed < self.settings.stream.segment_min_seconds:
            return
        if health.seconds_remaining <= self.PREROLL_SECONDS:
            await self._advance_segment(reason="ท่อนเดิมใกล้จบ")

    async def _advance_segment(
        self, *, reason: str, sku: str | None = None, force: bool = False
    ) -> None:
        live_basket = self.state.baskets.live
        target_sku = sku if sku is not None else (live_basket.sku if live_basket else None)

        segment = self.state.playlist.next_segment(target_sku)
        if segment is None:
            self.say("ไม่มีท่อนวิดีโอให้เล่นต่อ — ต้องเติมคลิปด่วน", "error")
            self.notify(
                "คลิปหมด",
                "คิวท่อนวิดีโอว่าง ระบบต่อจิ๊กซอว์ต่อไม่ได้",
                Severity.CRITICAL,
                needs_human=True,
            )
            return

        previous = self.state.stream.current_segment
        ok = await self.player.play_segment(segment)
        if not ok:
            self.say(f"สั่งเล่นท่อน {segment.id} ไม่สำเร็จ", "error")
            return

        self._segment_started_at = time.time()
        self.state.stream.current_segment = segment.id
        self.emit(
            SegmentChanged(from_segment=previous, to_segment=segment.id, reason=reason)
        )
        verb = "สลับ" if force else "ต่อ"
        self.say(f"{verb}ไปท่อน {segment.id} ({segment.duration_seconds:.0f} วิ) — {reason}")

    # ---------------- ตัวช่วย ----------------

    def _raise_alert(
        self, kind: str, severity: Severity, detail: str, *, notify: bool = False
    ) -> None:
        """กันสแปม: เตือนเรื่องเดิมซ้ำได้ไม่ถี่กว่า 60 วินาที"""
        now = time.time()
        if now - self._alerted.get(kind, 0.0) < 60.0:
            return
        self._alerted[kind] = now
        self.state.stream.last_alert = detail
        self.emit(StreamAlert(severity=severity, kind=kind, detail=detail))
        self.say(detail, "warn" if severity.rank < Severity.HIGH.rank else "error")
        if notify:
            self.notify("สตรีมมีปัญหา", detail, severity, needs_human=True)
