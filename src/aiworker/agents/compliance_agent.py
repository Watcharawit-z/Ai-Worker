"""พนักงานเฝ้าการละเมิด

สองด่าน:
- ด่านเร็ว: regex/คำต้องห้าม ตรวจทุกคอมเมนท์และทุกคำตอบทันที ไม่มีค่าใช้จ่าย
- ด่านลึก: ให้ Claude อ่านบริบทช่วงล่าสุดเป็นรอบ ๆ จับสิ่งที่ regex จับไม่ได้
  เช่น การพูดอ้อมให้ไปซื้อนอกแพลตฟอร์ม หรือเคลมสรรพคุณแบบเลี่ยงคำ

เจอแล้วทำอะไร: สะสม strike → สั่งสลับท่อนวิดีโอ → หนักสุดคือสั่งหยุดสตรีมและเรียกคน
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from ..domain.compliance_rules import ComplianceRules, RuleHit
from ..events import (
    CommentIn,
    CommentReply,
    ComplianceAction,
    Event,
    SegmentChanged,
    Severity,
    ViolationDetected,
)
from ..llm import LLMClient
from ..settings import Settings
from ..state import ShiftState
from .base import Agent

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "risk_level": {
            "type": "string",
            "enum": ["none", "low", "medium", "high", "critical"],
        },
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "code": {"type": "string"},
                    "quote": {"type": "string"},
                    "why": {"type": "string"},
                    "severity": {
                        "type": "string",
                        "enum": ["low", "medium", "high", "critical"],
                    },
                },
                "required": ["code", "quote", "why", "severity"],
                "additionalProperties": False,
            },
        },
        "recommended_action": {
            "type": "string",
            "enum": ["none", "warn", "switch_segment", "stop_stream"],
        },
        "summary": {"type": "string"},
    },
    "required": ["risk_level", "findings", "recommended_action", "summary"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """คุณคือเจ้าหน้าที่ตรวจสอบความเสี่ยงผิดกฎของไลฟ์ขายของบน TikTok Shop ประเทศไทย

สิ่งที่ต้องจับ (เรียงตามความร้ายแรง):
1. critical — ชวนโอนเงิน/ซื้อขายนอกแพลตฟอร์ม, ขายสินค้าต้องห้าม, เนื้อหาผู้ใหญ่
2. high — กล่าวอ้างสรรพคุณทางการแพทย์, อ้างผลลัพธ์แบบรับประกัน, อ้างหน่วยงานรับรองแบบเท็จ
3. medium — โฆษณาเกินจริง, เปรียบเทียบโจมตีคู่แข่ง, ให้ช่องทางติดต่อภายนอก
4. low — คำหยาบเบา ๆ, สแปมในคอมเมนท์

สำคัญ: อย่าตื่นตูม การขายของปกติ พูดคุยทั่วไป เชียร์สินค้าตามจริง ไม่ใช่การละเมิด
ถ้าไม่เจออะไรจริง ๆ ให้ risk_level="none" และ findings=[]

recommended_action:
- none = ปกติดี
- warn = บันทึกไว้เฉย ๆ
- switch_segment = ควรสลับไปเล่นท่อนอื่นเพื่อเลี่ยงเนื้อหาเสี่ยง
- stop_stream = ต้องหยุดสตรีมทันที

ตอบเป็น JSON ตาม schema เท่านั้น"""

_SEVERITY_MAP = {
    "none": Severity.INFO,
    "low": Severity.LOW,
    "medium": Severity.MEDIUM,
    "high": Severity.HIGH,
    "critical": Severity.CRITICAL,
}


class ComplianceAgent(Agent):
    """แทนคนที่ต้องคอยสังเกตว่าไลฟ์กำลังจะผิดกฎหรือยัง"""

    name = "compliance"
    subscribes = ("comment.in", "comment.reply", "stream.segment_changed")

    def __init__(
        self,
        bus,
        state: ShiftState,
        settings: Settings,
        llm: LLMClient,
    ) -> None:
        super().__init__(bus, state, settings)
        self.llm = llm
        self.rules = ComplianceRules(
            banned_words=settings.compliance.banned_words,
            extra_claim_patterns=settings.compliance.risky_claim_patterns,
        )
        self.tick_interval = settings.compliance.llm_review_interval_seconds
        self._window: deque[str] = deque(maxlen=80)
        self._last_action_at = 0.0
        self._viewer_flagged_at: dict[str, float] = {}
        self._viewer_flag_counts: dict[str, int] = {}
        self._switch_at = _SEVERITY_MAP.get(
            settings.compliance.auto_switch_segment_at, Severity.HIGH
        )
        self._stop_at = _SEVERITY_MAP.get(
            settings.compliance.auto_stop_at, Severity.CRITICAL
        )

    # ---------------- ด่านเร็ว ----------------

    async def handle(self, event: Event) -> None:
        if isinstance(event, CommentIn):
            self._window.append(f"[คอมเมนท์ @{event.nickname}] {event.text}")
            self._scan(event.text, source=f"comment:@{event.nickname}", ours=False)
        elif isinstance(event, CommentReply):
            self._window.append(f"[เราตอบ] {event.text}")
            # คำตอบของเราเองก็ต้องตรวจ — AI ตอบผิดกฎก็โดนระงับได้เหมือนกัน
            self._scan(event.text, source="our_reply", ours=True)
        elif isinstance(event, SegmentChanged):
            self._window.append(f"[เปลี่ยนไปท่อน {event.to_segment}] {event.reason}")

    def _scan(self, text: str, *, source: str, ours: bool) -> None:
        """ตรวจข้อความหนึ่งชิ้น

        `ours` สำคัญมาก: ถ้าลูกค้าเป็นคนพิมพ์ "ขอไลน์" คนผิดคือลูกค้า ไม่ใช่เรา
        การไปสลับคลิปหรือหยุดสตรีมจึงไม่ช่วยอะไร — แค่บันทึกไว้แล้วบอกคนดูแล
        จะสลับคลิป/หยุดสตรีมเฉพาะตอนที่ *เนื้อหาของเราเอง* มีปัญหาเท่านั้น
        """
        hits = self.rules.check(text, source=source)
        if not hits:
            return
        worst = ComplianceRules.worst(hits)
        for hit in hits:
            self._report(hit, ours=ours)
        if ours:
            self._react(worst, hits[0].code, hits[0].detail)
        else:
            self._flag_viewer(worst, hits[0])

    def _report(self, hit: RuleHit, *, ours: bool) -> None:
        comp = self.state.compliance
        if ours:
            # นับ strike เฉพาะความผิดของเราเอง — ตัวเลขนี้คือความเสี่ยงโดนระงับช่องจริง
            comp.strikes += 1
            comp.last_violation = hit.detail
            comp.last_violation_at = time.time()
            if hit.severity.rank > comp.worst_severity.rank:
                comp.worst_severity = hit.severity

        comp.recent.append(
            {
                "ts": time.time(),
                "code": hit.code,
                "severity": hit.severity.value,
                "detail": hit.detail,
                "source": hit.source,
                "ours": ours,
            }
        )
        self.emit(
            ViolationDetected(
                severity=hit.severity,
                code=hit.code,
                detail=hit.detail,
                source=hit.source,
                evidence=hit.matched,
                suggested_action=self._action_for(hit.severity) if ours else "warn",
            )
        )
        if ours:
            level = "error" if hit.severity.rank >= Severity.HIGH.rank else "warn"
            self.say(f"เนื้อหาของเราเสี่ยง [{hit.code}] {hit.detail}", level)

    def _flag_viewer(self, severity: Severity, hit: RuleHit) -> None:
        """คอมเมนท์ของผู้ชมที่มีปัญหา — เราสั่งลบผ่าน API ไม่ได้ จึงได้แค่บอกคน

        กันสแปม: เรื่องเดิมรายงานได้ไม่ถี่กว่า 2 นาที เพราะคำถามแบบ "ขอไลน์"
        ในไลฟ์หนึ่งกะอาจมีเป็นร้อยครั้ง
        """
        now = time.time()
        if now - self._viewer_flagged_at.get(hit.code, 0.0) < 120.0:
            self._viewer_flag_counts[hit.code] = (
                self._viewer_flag_counts.get(hit.code, 0) + 1
            )
            return

        repeats = self._viewer_flag_counts.pop(hit.code, 0)
        self._viewer_flagged_at[hit.code] = now
        suffix = f" (ซ้ำอีก {repeats} ครั้งใน 2 นาทีที่ผ่านมา)" if repeats else ""
        self.say(f"คอมเมนท์ผู้ชมมีปัญหา [{hit.code}] {hit.matched}{suffix}", "warn")

        if severity.rank >= Severity.HIGH.rank:
            self.notify(
                "คอมเมนท์ผู้ชมเสี่ยงทำให้ช่องโดนตรวจสอบ",
                f"{hit.detail}{suffix}\n"
                "แนะนำให้ซ่อนคอมเมนท์หรือแบนผู้ใช้ในแอป TikTok "
                "(ระบบสั่งลบคอมเมนท์แทนไม่ได้)",
                Severity.MEDIUM,
                needs_human=True,
            )

    # ---------------- ด่านลึก (LLM) ----------------

    async def tick(self) -> None:
        if not self.llm.available or not self._window:
            return

        transcript = "\n".join(list(self._window)[-40:])
        basket = self.state.baskets.live
        user = (
            f"สินค้าที่ไลฟ์อยู่: {basket.name if basket else 'ไม่ระบุ'}\n\n"
            "ประเมินเฉพาะสิ่งที่ *ฝั่งร้าน* พูดหรือตอบ (บรรทัดที่ขึ้นต้นด้วย [เราตอบ] "
            "และ [เปลี่ยนไปท่อน]) — คอมเมนท์ของผู้ชมใช้เป็นบริบทเท่านั้น "
            "ผู้ชมพิมพ์อะไรมาไม่ถือเป็นความผิดของร้าน\n\n"
            f"บทสนทนาในไลฟ์ช่วงล่าสุด:\n{transcript}"
        )

        result = await self.llm.ask_json(
            system=SYSTEM_PROMPT,
            user=user,
            schema=REVIEW_SCHEMA,
            effort="low",
            max_tokens=1500,
        )
        if not result.ok:
            return

        risk = _SEVERITY_MAP.get(str(result.data.get("risk_level", "none")), Severity.INFO)
        findings = result.data.get("findings", []) or []
        if risk == Severity.INFO or not findings:
            return

        for finding in findings:
            severity = _SEVERITY_MAP.get(str(finding.get("severity", "low")), Severity.LOW)
            self._report(
                RuleHit(
                    code=f"ai:{finding.get('code', 'unknown')}",
                    severity=severity,
                    matched=str(finding.get("quote", ""))[:120],
                    detail=str(finding.get("why", "")),
                    source="ai_review",
                ),
                ours=True,
            )

        action = str(result.data.get("recommended_action", "none"))
        summary = str(result.data.get("summary", ""))
        if action in ("switch_segment", "stop_stream"):
            self._dispatch(action, summary, "ai_review")
        else:
            self._react(risk, "ai_review", summary)

    # ---------------- ตัดสินใจลงมือ ----------------

    def _action_for(self, severity: Severity) -> str:
        if severity.rank >= self._stop_at.rank:
            return "stop_stream"
        if severity.rank >= self._switch_at.rank:
            return "switch_segment"
        return "warn"

    def _react(self, severity: Severity, code: str, detail: str) -> None:
        action = self._action_for(severity)
        if action == "warn":
            return
        self._dispatch(action, detail, code)

    def _dispatch(self, action: str, reason: str, code: str) -> None:
        # กันสั่งรัว: เว้นอย่างน้อย 30 วินาทีระหว่างการลงมือแต่ละครั้ง
        now = time.time()
        if now - self._last_action_at < 30.0:
            return
        self._last_action_at = now

        self.emit(ComplianceAction(action=action, reason=reason, violation_code=code))

        if action == "stop_stream":
            self.state.compliance.paused = True
            self.notify(
                "หยุดไลฟ์ฉุกเฉิน — พบความเสี่ยงร้ายแรง",
                f"{reason}\n(รหัส: {code})",
                Severity.CRITICAL,
                needs_human=True,
            )
            self.say(f"สั่งหยุดสตรีม: {reason}", "error")
        else:
            self.notify(
                "สั่งสลับท่อนวิดีโอเพื่อเลี่ยงความเสี่ยง",
                f"{reason}\n(รหัส: {code})",
                Severity.HIGH,
            )
            self.say(f"สั่งสลับท่อนวิดีโอ: {reason}", "warn")
