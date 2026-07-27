"""พนักงานตอบคอมเมนท์

หลักการทำงาน:
- เก็บคอมเมนท์เป็นชุด (batch) แล้วส่งให้ Claude ทีเดียว ประหยัดกว่ายิงทีละอัน
- ความรู้สินค้า/นโยบายร้าน ใส่ไว้ในบล็อกที่ถูก cache — จ่ายเต็มครั้งเดียว
- คำถามซ้ำ ๆ (ราคา/ส่งกี่วัน/ไซซ์) ตอบจากคลังคำตอบเลย ไม่ต้องเรียก AI
- อะไรที่ไม่มั่นใจ ไม่ตอบมั่ว แต่ส่งต่อให้คน
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..adapters.base import CommentSender
from ..events import CommentEscalation, CommentIn, CommentReply, Event
from ..llm import LLMClient
from ..settings import Settings
from ..state import ShiftState
from .base import Agent

REPLY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "replies": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "comment_id": {"type": "string"},
                    "reply": {"type": "string"},
                    "intent": {
                        "type": "string",
                        "enum": [
                            "price",
                            "shipping",
                            "stock",
                            "size",
                            "howto",
                            "promo",
                            "order_status",
                            "complaint",
                            "greeting",
                            "other",
                        ],
                    },
                    "confidence": {"type": "number"},
                    "escalate": {"type": "boolean"},
                    "escalate_reason": {"type": "string"},
                },
                "required": [
                    "comment_id",
                    "reply",
                    "intent",
                    "confidence",
                    "escalate",
                    "escalate_reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["replies"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """คุณคือพนักงานตอบคอมเมนท์ในไลฟ์ขายของบน TikTok Shop ประเทศไทย

กติกาที่ห้ามฝ่าฝืน:
- ตอบสั้น กระชับ เหมือนคนพิมพ์สดในไลฟ์ ไม่เกิน 1-2 บรรทัด
- ตอบจากข้อมูลสินค้าที่ให้ไว้เท่านั้น ห้ามเดาราคา สเปก หรือโปรโมชั่น
- ห้ามชวนไปคุยนอกแพลตฟอร์ม ห้ามให้ไลน์/เบอร์/เลขบัญชี ห้ามให้โอนเงินนอกระบบ
- ห้ามกล่าวอ้างสรรพคุณทางการแพทย์ ห้ามพูดว่า "รักษาหาย" "ได้ผล 100%"
- ถ้าไม่มีข้อมูล หรือเป็นเรื่องเคลม/ของเสีย/เงินคืน/ด่าทอ ให้ escalate=true และเขียนคำตอบกลาง ๆ ที่ปลอดภัย
- ไม่ต้องตอบทุกคอมเมนท์ คอมเมนท์ทักทายเฉย ๆ หรือสแปม ให้ confidence ต่ำไว้

confidence คือความมั่นใจว่าคำตอบถูกต้องและปลอดภัย (0.0-1.0)
ตอบเป็น JSON ตาม schema เท่านั้น"""


class CommentAgent(Agent):
    """แทนคนที่นั่งตอบคอมเมนท์ลูกค้าทั้งกะ"""

    name = "comment_agent"
    subscribes = ("comment.in",)

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        llm: LLMClient,
        sender: CommentSender,
        knowledge: dict[str, Any],
    ) -> None:
        super().__init__(bus, state, settings)
        self.llm = llm
        self.sender = sender
        self.knowledge = knowledge
        self.tick_interval = settings.comments.batch_seconds
        self._buffer: list[CommentIn] = []
        self._lock = asyncio.Lock()
        self._last_reply_at: dict[str, float] = {}
        self._cached_context = self._build_knowledge_block()

    # ---------------- รับคอมเมนท์ ----------------

    async def handle(self, event: Event) -> None:
        if not isinstance(event, CommentIn):
            return
        self.state.comment_count += 1
        async with self._lock:
            self._buffer.append(event)
            if len(self._buffer) >= self.settings.comments.max_batch:
                batch, self._buffer = self._buffer, []
                await self._process(batch)

    async def tick(self) -> None:
        async with self._lock:
            if not self._buffer:
                return
            batch, self._buffer = self._buffer, []
        await self._process(batch)

    # ---------------- ประมวลผลชุดคอมเมนท์ ----------------

    async def _process(self, batch: list[CommentIn]) -> None:
        pending: list[CommentIn] = []

        # ด่านที่ 1: คำถามซ้ำซากตอบจากคลังคำตอบ ไม่ต้องจ่ายค่า AI
        for comment in batch:
            canned = self._match_canned(comment.text)
            if canned:
                await self._send_reply(comment, canned, intent="faq", confidence=0.95)
            else:
                pending.append(comment)

        if not pending:
            return

        # ด่านที่ 2: ที่เหลือส่งให้ Claude
        if not self.llm.available:
            for comment in pending[: self.settings.comments.max_replies_per_batch]:
                self._escalate(comment, "ยังไม่ได้ตั้งค่า AI — ต้องให้คนตอบ")
            return

        result = await self.llm.ask_json(
            system=SYSTEM_PROMPT,
            cached_context=self._cached_context,
            user=self._build_user_prompt(pending),
            schema=REPLY_SCHEMA,
            effort="low",
            max_tokens=1200,
        )

        if not result.ok:
            self.say(f"AI ตอบคอมเมนท์ไม่สำเร็จ ({result.error}) ส่งต่อให้คน", "warn")
            for comment in pending[:3]:
                self._escalate(comment, f"AI ใช้งานไม่ได้: {result.error}")
            return

        by_id = {c.id: c for c in pending}
        sent = 0
        for item in result.data.get("replies", []):
            comment = by_id.get(str(item.get("comment_id", "")))
            if comment is None:
                continue

            confidence = float(item.get("confidence", 0.0))
            reply_text = str(item.get("reply", "")).strip()

            if item.get("escalate") or confidence < self.settings.comments.min_confidence_to_send:
                self._escalate(
                    comment,
                    str(item.get("escalate_reason") or f"ความมั่นใจต่ำ ({confidence:.2f})"),
                )
                continue

            if not reply_text or sent >= self.settings.comments.max_replies_per_batch:
                continue

            await self._send_reply(
                comment,
                reply_text,
                intent=str(item.get("intent", "other")),
                confidence=confidence,
            )
            sent += 1

    # ---------------- ส่งคำตอบ ----------------

    async def _send_reply(
        self, comment: CommentIn, text: str, *, intent: str, confidence: float
    ) -> None:
        now = time.time()
        cooldown = self.settings.comments.cooldown_per_user_seconds
        if now - self._last_reply_at.get(comment.user_id, 0.0) < cooldown:
            return  # กันตอบคนเดิมรัว ๆ จนดูเหมือนบอท

        text = text.strip()[: self.settings.comments.max_reply_chars]
        ok = await self.sender.send(text, reply_to=comment.nickname)
        if not ok:
            self.say(f"ส่งคำตอบไม่สำเร็จ: {text[:40]}", "warn")
            return

        self._last_reply_at[comment.user_id] = now
        self.state.reply_count += 1
        self.emit(
            CommentReply(
                reply_to_user=comment.user_id,
                reply_to_comment_id=comment.id,
                text=text,
                intent=intent,
                confidence=confidence,
            )
        )
        self.say(f"ตอบ @{comment.nickname}: {text}")

    def _escalate(self, comment: CommentIn, reason: str) -> None:
        self.state.escalation_count += 1
        self.emit(
            CommentEscalation(
                comment_id=comment.id,
                nickname=comment.nickname,
                text=comment.text,
                reason=reason,
            )
        )
        self.say(f"ส่งต่อให้คน — @{comment.nickname}: {comment.text} ({reason})", "warn")

    # ---------------- คลังความรู้ ----------------

    def _match_canned(self, text: str) -> str | None:
        """จับคู่คำถามซ้ำซากกับคำตอบสำเร็จรูปจาก config"""
        lowered = text.lower().strip()
        if not lowered:
            return None
        for entry in self.knowledge.get("faq", []):
            for keyword in entry.get("keywords", []):
                if keyword.lower() in lowered:
                    return str(entry.get("answer", "")).strip() or None
        return None

    def _build_knowledge_block(self) -> str:
        """สร้างบล็อกความรู้ที่จะถูก cache — ต้องนิ่ง ห้ามมีเวลา/ตัวเลขที่เปลี่ยนทุกครั้ง"""
        shop = self.settings.shop
        lines = [
            "# ข้อมูลร้าน",
            f"ชื่อร้าน: {shop.name}",
            f"โทนการพูด: {shop.tone}",
            f"การจัดส่ง: {shop.shipping_note}",
            f"การเปลี่ยน/คืน: {shop.return_policy}",
            "",
            "# รายการสินค้า",
        ]
        for product in self.knowledge.get("products", []):
            lines.append(
                f"- [{product.get('sku', '')}] {product.get('name', '')} "
                f"ราคา {product.get('price', 0)} บาท"
            )
            if product.get("detail"):
                lines.append(f"  รายละเอียด: {product['detail']}")
            if product.get("options"):
                lines.append(f"  ตัวเลือก: {', '.join(map(str, product['options']))}")
            if product.get("promo"):
                lines.append(f"  โปรโมชั่น: {product['promo']}")

        faq = self.knowledge.get("faq", [])
        if faq:
            lines += ["", "# คำถามที่เจอบ่อยและคำตอบมาตรฐาน"]
            for entry in faq:
                lines.append(f"- ถาม: {'/'.join(entry.get('keywords', []))}")
                lines.append(f"  ตอบ: {entry.get('answer', '')}")
        return "\n".join(lines)

    def _build_user_prompt(self, comments: list[CommentIn]) -> str:
        pinned = self.state.baskets.pinned
        others = [b for b in self.state.baskets.baskets if b is not pinned]
        context = [
            "สินค้าที่ปักแสดงอยู่ตอนนี้ (คนในคลิปกำลังพูดถึงตัวนี้): "
            + (
                f"[{pinned.sku}] {pinned.name} ราคา {pinned.price:.0f} บาท"
                if pinned
                else "ยังไม่ได้ปักตะกร้า"
            ),
            "",
            "ตะกร้าอื่นที่อยู่ในไลฟ์นี้ด้วย (ลูกค้าถามถึงได้ตลอด):",
        ]
        context += [
            f"- [{b.sku}] {b.name} ราคา {b.price:.0f} บาท" for b in others
        ] or ["- (ไม่มี)"]
        context += [
            "",
            f"จำนวนคนดูตอนนี้: {self.state.stream.viewers}",
            "",
            "คอมเมนท์ที่ต้องพิจารณา:",
        ]
        for c in comments:
            tag = " [ผู้ติดตาม]" if c.is_follower else ""
            context.append(f'- id={c.id} @{c.nickname}{tag}: "{c.text}"')
        context.append("")
        context.append(
            "ตอบเฉพาะคอมเมนท์ที่ควรตอบจริง ๆ "
            f"(ตอบได้ไม่เกิน {self.settings.comments.max_replies_per_batch} อัน)"
        )
        return "\n".join(context)
