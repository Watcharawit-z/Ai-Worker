"""ประกอบร่างทั้งแผนก แล้วสั่งเข้ากะ"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import yaml

from .adapters import (
    build_ads,
    build_comment_sender,
    build_comment_source,
    build_notifiers,
    build_player,
    build_shop,
)
from .agents import (
    AdsAgent,
    Agent,
    BasketAgent,
    CommentAgent,
    ComplianceAgent,
    LiveWatcherAgent,
    NotifierAgent,
)
from .bus import EventBus
from .domain.basket import BasketQueue
from .domain.segments import SegmentPlaylist
from .events import CommentIn, Severity
from .llm import LLMClient
from .settings import Settings, load_settings, load_yaml_list
from .state import ShiftState

log = logging.getLogger(__name__)


class Shift:
    """หนึ่งกะไลฟ์ — คุมวงจรชีวิตของพนักงาน AI ทุกคน"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bus = EventBus()
        self.llm = LLMClient(
            api_key=settings.api_key,
            model=settings.llm.model,
            effort=settings.llm.effort,
            max_tokens=settings.llm.max_tokens,
            enabled=settings.llm.enabled,
        )

        baskets = BasketQueue.from_config(
            load_yaml_list(settings.baskets_path, "baskets")
        )
        playlist = SegmentPlaylist.from_config(
            load_yaml_list(settings.segments_path, "segments"),
            shuffle=settings.stream.shuffle_segments,
            avoid_repeat_within=settings.stream.avoid_repeat_within,
        )
        self.state = ShiftState(settings.shop.channel_id, baskets, playlist)
        self.knowledge = _load_knowledge(settings.products_path)

        # adapters
        self.player = build_player(settings)
        self.comment_source = build_comment_source(settings)
        self.comment_sender = build_comment_sender(settings)
        self.shop = build_shop(settings)
        self.ads = build_ads(settings)
        self.notifiers = build_notifiers(settings)

        self.agents: list[Agent] = [
            NotifierAgent(self.bus, self.state, settings, self.notifiers),
            LiveWatcherAgent(self.bus, self.state, settings, self.player),
            BasketAgent(self.bus, self.state, settings, self.shop),
            AdsAgent(self.bus, self.state, settings, self.ads),
            ComplianceAgent(self.bus, self.state, settings, self.llm),
            CommentAgent(
                self.bus, self.state, settings, self.llm, self.comment_sender, self.knowledge
            ),
        ]
        self._tasks: list[asyncio.Task] = []

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        self._preflight()

        await self.comment_source.connect()
        self.comment_source.set_handler(self._on_raw_comment)
        self._tasks.append(
            asyncio.create_task(self.comment_source.run(), name="comment-source")
        )

        # ลำดับสำคัญ: notifier ต้องพร้อมก่อน เผื่อ agent อื่นแจ้งเตือนตอน start
        for agent in self.agents:
            await agent.start()

        log.info(
            "เข้ากะแล้ว — ช่อง %s | ตะกร้า %d ใบ | คลิป %d ท่อน | AI %s",
            self.state.channel_id,
            len(self.state.baskets.baskets),
            len(self.state.playlist.segments),
            "เปิด" if self.llm.available else "ปิด (โหมดกฎล้วน)",
        )

    async def stop(self) -> None:
        self.state.running = False
        for task in self._tasks:
            task.cancel()
        for agent in reversed(self.agents):
            await agent.stop()
        await self.comment_source.disconnect()
        await self.llm.aclose()
        log.info("ปิดกะเรียบร้อย — %s", self.summary_line())

    async def run_forever(self) -> None:
        await self.start()
        try:
            while self.state.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # ---------------- คอมเมนท์เข้า ----------------

    def _on_raw_comment(self, payload: dict[str, Any]) -> None:
        """callback จาก comment source — ต้องไม่บล็อกและไม่ throw"""
        try:
            self.bus.publish(
                CommentIn(
                    channel_id=self.state.channel_id,
                    user_id=str(payload.get("user_id", "")),
                    nickname=str(payload.get("nickname", "ผู้ชม")),
                    text=str(payload.get("text", "")),
                    is_follower=bool(payload.get("is_follower", False)),
                    is_subscriber=bool(payload.get("is_subscriber", False)),
                )
            )
        except Exception:  # noqa: BLE001
            log.exception("รับคอมเมนท์เข้าระบบไม่สำเร็จ")

    # ---------------- ตรวจความพร้อมก่อนเข้ากะ ----------------

    def _preflight(self) -> None:
        """เช็คของที่ขาดตั้งแต่ต้น ดีกว่าไปพังตอนไลฟ์"""
        problems: list[str] = []
        if not self.state.baskets.baskets:
            problems.append(f"ไม่มีตะกร้าใน {self.settings.baskets_path}")
        if not self.state.playlist.segments:
            problems.append(f"ไม่มีคลิปใน {self.settings.segments_path}")
        if not self.knowledge.get("products"):
            problems.append(f"ไม่มีข้อมูลสินค้าใน {self.settings.products_path}")

        for problem in problems:
            log.warning("ตรวจก่อนเข้ากะ: %s", problem)
        if problems:
            log.warning("ระบบยังรันได้ แต่ควรเติมข้อมูลให้ครบก่อนใช้งานจริง")

        if not self.llm.available:
            log.warning(
                "ไม่พบ ANTHROPIC_API_KEY — ปิดส่วนตอบคอมเมนท์ด้วย AI "
                "เหลือเฉพาะกฎอัตโนมัติ (เฝ้าไลฟ์/ตะกร้า/แอด/คำต้องห้าม ยังทำงานปกติ)"
            )

        # เตือนถ้าคลิปมีไม่พอสำหรับความยาวกะ
        total_minutes = self.state.playlist.total_duration() / 60.0
        if 0 < total_minutes < 60:
            log.warning(
                "คลิปทั้งหมดรวมกันแค่ %.0f นาที — จะวนซ้ำเร็วมาก คนดูจับได้ง่าย",
                total_minutes,
            )

    # ---------------- สรุป ----------------

    def summary_line(self) -> str:
        b = self.state.baskets.summary()
        return (
            f"ยอดขาย {b['total_revenue']:.0f}฿ / {b['total_orders']} ออเดอร์ | "
            f"ค่าแอด {b['total_ad_spend']:.0f}฿ | "
            f"คอมเมนท์ {self.state.comment_count} ตอบ {self.state.reply_count} "
            f"ส่งต่อคน {self.state.escalation_count} | "
            f"ความเสี่ยงที่เจอ {self.state.compliance.strikes} ครั้ง"
        )

    def snapshot(self) -> dict[str, Any]:
        snap = self.state.snapshot()
        snap["llm"] = self.llm.usage_summary()
        snap["bus"] = self.bus.stats()
        snap["pending_replies"] = getattr(self.comment_sender, "sent", None) or list(
            getattr(self.comment_sender, "pending", []) or []
        )
        return snap


def _load_knowledge(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"products": [], "faq": []}
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {"products": data.get("products", []), "faq": data.get("faq", [])}


def build_shift(config_path: str | None = None) -> Shift:
    return Shift(load_settings(config_path))
