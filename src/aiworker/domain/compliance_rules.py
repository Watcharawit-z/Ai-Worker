"""กฎตรวจการละเมิดแบบเร็ว (ไม่ต้องเรียก LLM)

ใช้เป็นด่านแรก: จับได้ทันทีในระดับมิลลิวินาที ไม่มีค่าใช้จ่าย
ส่วนที่ต้องใช้วิจารณญาณค่อยส่งต่อให้ LLM ตรวจซ้ำเป็นรอบ ๆ
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..events import Severity

# คำ/วลีที่ TikTok Shop ไทยมักตีว่าเป็นการกล่าวอ้างเกินจริง
DEFAULT_RISKY_CLAIMS: list[tuple[str, str, Severity]] = [
    (r"รักษา(หาย|ได้|โรค)", "medical_claim", Severity.HIGH),
    (r"หายขาด", "medical_claim", Severity.HIGH),
    (r"ปลอดภัย\s*100\s*%", "absolute_claim", Severity.HIGH),
    (r"ได้ผล\s*100\s*%", "absolute_claim", Severity.HIGH),
    (r"อย\.?\s*รับรอง", "authority_claim", Severity.MEDIUM),
    (r"ดีที่สุดในโลก", "superlative_claim", Severity.MEDIUM),
    (r"ลดน้ำหนัก.*(สัปดาห์|วัน|กิโล)", "weight_loss_claim", Severity.HIGH),
    (r"ขาว(ใส)?ภายใน\s*\d+\s*วัน", "cosmetic_claim", Severity.HIGH),
    (r"ของแท้\s*100\s*%\s*ถ้าปลอมยินดีคืนเงิน", "guarantee_claim", Severity.LOW),
]

# ชวนไปปิดการขายนอกแพลตฟอร์ม = ผิดกฎชัดเจน
OFFPLATFORM_PATTERNS: list[tuple[str, str, Severity]] = [
    (r"(แอด|เพิ่ม|ทัก|ขอ|มี|ส่ง)\s*(ไลน์|line)", "offplatform_sale", Severity.HIGH),
    (r"(ไลน์|line)\s*(ไอดี|id|มั้ย|ไหม|หน่อย)", "offplatform_sale", Severity.HIGH),
    (r"(ทักแชท|inbox|อินบ็อกซ์)\s*(เฟส|facebook|fb)", "offplatform_sale", Severity.HIGH),
    (r"โอนเงินเข้าบัญชี", "offplatform_payment", Severity.CRITICAL),
    (r"เลขบัญชี\s*\d", "offplatform_payment", Severity.CRITICAL),
    (r"\b0[689]\d{8}\b", "contact_leak", Severity.MEDIUM),
]

SPAM_PATTERNS: list[tuple[str, str, Severity]] = [
    (r"(https?://|www\.)\S+", "external_link", Severity.MEDIUM),
    (r"(รับสมัคร|หารายได้เสริม|งานออนไลน์)", "spam_recruit", Severity.LOW),
]


@dataclass(slots=True)
class RuleHit:
    code: str
    severity: Severity
    matched: str
    detail: str
    source: str


class ComplianceRules:
    """รวมกฎทั้งหมดไว้ที่เดียว เพิ่มคำต้องห้ามเองได้จาก config"""

    def __init__(
        self,
        banned_words: list[str] | None = None,
        extra_claim_patterns: list[str] | None = None,
    ) -> None:
        self._banned = [w.strip() for w in (banned_words or []) if w.strip()]
        self._compiled: list[tuple[re.Pattern[str], str, Severity]] = []

        for pattern, code, sev in (
            DEFAULT_RISKY_CLAIMS + OFFPLATFORM_PATTERNS + SPAM_PATTERNS
        ):
            self._compiled.append((re.compile(pattern, re.IGNORECASE), code, sev))

        for pattern in extra_claim_patterns or []:
            try:
                self._compiled.append(
                    (re.compile(pattern, re.IGNORECASE), "custom_claim", Severity.MEDIUM)
                )
            except re.error:  # กัน config พิมพ์ regex ผิดแล้วระบบล้ม
                continue

    def check(self, text: str, *, source: str = "unknown") -> list[RuleHit]:
        """ตรวจข้อความหนึ่งชิ้น คืนรายการสิ่งที่เจอ"""
        if not text:
            return []
        hits: list[RuleHit] = []
        lowered = text.lower()

        for word in self._banned:
            if word.lower() in lowered:
                hits.append(
                    RuleHit(
                        code="banned_word",
                        severity=Severity.HIGH,
                        matched=word,
                        detail=f"พบคำต้องห้ามที่ร้านตั้งไว้: '{word}'",
                        source=source,
                    )
                )

        # หลาย pattern อาจชี้เรื่องเดียวกัน (เช่น "ขอไลน์" กับ "ไลน์หน่อย")
        # นับเป็นความผิดครั้งเดียวพอ ไม่งั้น strike จะพุ่งเกินจริง
        seen_codes = {h.code for h in hits}
        for pattern, code, sev in self._compiled:
            if code in seen_codes:
                continue
            m = pattern.search(text)
            if m:
                seen_codes.add(code)
                hits.append(
                    RuleHit(
                        code=code,
                        severity=sev,
                        matched=m.group(0),
                        detail=_describe(code, m.group(0)),
                        source=source,
                    )
                )
        return hits

    @staticmethod
    def worst(hits: list[RuleHit]) -> Severity:
        if not hits:
            return Severity.INFO
        return max((h.severity for h in hits), key=lambda s: s.rank)


_DESCRIPTIONS = {
    "medical_claim": "กล่าวอ้างสรรพคุณทางการแพทย์ เสี่ยงโดนระงับช่อง",
    "absolute_claim": "กล่าวอ้างแบบสัมบูรณ์ (100%) ซึ่งพิสูจน์ไม่ได้",
    "authority_claim": "อ้างหน่วยงานรับรอง ต้องมีเลขทะเบียนจริงประกอบ",
    "superlative_claim": "ใช้คำเปรียบเทียบขั้นสูงสุดโดยไม่มีหลักฐาน",
    "weight_loss_claim": "อ้างผลลดน้ำหนักตามระยะเวลา เป็นหมวดที่ถูกจับตาหนัก",
    "cosmetic_claim": "อ้างผลลัพธ์ความงามตามระยะเวลา",
    "guarantee_claim": "การรับประกันแบบนี้ควรเลี่ยงหรือใส่เงื่อนไขให้ชัด",
    "offplatform_sale": "ชวนปิดการขายนอกแพลตฟอร์ม ผิดกฎ TikTok Shop ชัดเจน",
    "offplatform_payment": "ให้โอนเงินนอกระบบ เป็นเหตุระงับช่องทันที",
    "contact_leak": "มีเบอร์โทรในข้อความ เสี่ยงถูกตีความว่าซื้อขายนอกระบบ",
    "external_link": "มีลิงก์ภายนอกในคอมเมนท์",
    "spam_recruit": "คอมเมนท์ชวนหารายได้/สแปม",
    "banned_word": "คำต้องห้ามที่ร้านกำหนดเอง",
}


def _describe(code: str, matched: str) -> str:
    base = _DESCRIPTIONS.get(code, "พบข้อความเสี่ยง")
    return f"{base} (เจอ: '{matched}')"
