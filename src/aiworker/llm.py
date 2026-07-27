"""ตัวห่อการเรียก Claude

จุดสำคัญ 3 อย่าง:
1. ถ้าไม่มี ANTHROPIC_API_KEY ระบบยังรันได้ (โหมดกฎล้วน) ไม่ล้ม
2. ใช้ prompt caching กับความรู้สินค้าที่ไม่เปลี่ยน — ประหยัดค่า token ~90%
3. บังคับ output เป็น JSON schema เพื่อไม่ต้องมานั่ง parse ข้อความอิสระ
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MODEL = "claude-opus-5"


@dataclass(slots=True)
class LLMResult:
    ok: bool
    data: dict[str, Any]
    raw_text: str = ""
    error: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


class LLMClient:
    """เรียก Claude แบบ async พร้อม retry และนับ token ที่ใช้"""

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str = MODEL,
        effort: str = "medium",
        max_tokens: int = 2000,
        enabled: bool = True,
    ) -> None:
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self.enabled = enabled and bool(api_key)
        self._client: Any = None
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cache_read_tokens = 0
        self.call_count = 0
        self.error_count = 0

        if self.enabled:
            try:
                from anthropic import AsyncAnthropic

                self._client = AsyncAnthropic(api_key=api_key)
            except ImportError:
                log.warning("ไม่พบไลบรารี anthropic — ทำงานในโหมดกฎล้วน")
                self.enabled = False

    @property
    def available(self) -> bool:
        return self.enabled and self._client is not None

    async def ask_json(
        self,
        *,
        system: str,
        cached_context: str = "",
        user: str,
        schema: dict[str, Any],
        effort: str | None = None,
        max_tokens: int | None = None,
        retries: int = 2,
    ) -> LLMResult:
        """ถาม Claude แล้วบังคับให้ตอบเป็น JSON ตาม schema

        `cached_context` = ส่วนที่ยาวและไม่เปลี่ยน (ความรู้สินค้า/นโยบายร้าน)
        จะถูกใส่ cache_control ไว้ให้ ทำให้เรียกซ้ำถูกลงมาก
        """
        if not self.available:
            return LLMResult(ok=False, data={}, error="llm_disabled")

        system_blocks: list[dict[str, Any]] = [{"type": "text", "text": system}]
        if cached_context:
            # breakpoint อยู่ท้ายบล็อกที่นิ่ง — ทุกอย่างก่อนหน้านี้จะถูก cache
            system_blocks.append(
                {
                    "type": "text",
                    "text": cached_context,
                    "cache_control": {"type": "ephemeral"},
                }
            )

        last_error = ""
        for attempt in range(retries + 1):
            try:
                response = await self._client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens or self.max_tokens,
                    system=system_blocks,
                    output_config={
                        "effort": effort or self.effort,
                        "format": {"type": "json_schema", "schema": schema},
                    },
                    messages=[{"role": "user", "content": user}],
                )
            except Exception as exc:  # noqa: BLE001 - ต้องกันทุกกรณีไม่ให้ agent ตาย
                last_error = f"{type(exc).__name__}: {exc}"
                self.error_count += 1
                if attempt < retries:
                    await asyncio.sleep(1.5 * (2**attempt))
                    continue
                log.warning("เรียก LLM ไม่สำเร็จ: %s", last_error)
                return LLMResult(ok=False, data={}, error=last_error)

            self.call_count += 1
            usage = getattr(response, "usage", None)
            in_tok = getattr(usage, "input_tokens", 0) or 0
            out_tok = getattr(usage, "output_tokens", 0) or 0
            cache_tok = getattr(usage, "cache_read_input_tokens", 0) or 0
            self.total_input_tokens += in_tok
            self.total_output_tokens += out_tok
            self.total_cache_read_tokens += cache_tok

            if getattr(response, "stop_reason", None) == "refusal":
                return LLMResult(ok=False, data={}, error="refusal")

            text = next(
                (b.text for b in response.content if getattr(b, "type", "") == "text"), ""
            )
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                last_error = "โมเดลตอบกลับไม่ใช่ JSON ที่อ่านได้"
                if attempt < retries:
                    continue
                return LLMResult(ok=False, data={}, raw_text=text, error=last_error)

            return LLMResult(
                ok=True,
                data=data,
                raw_text=text,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_tok,
            )

        return LLMResult(ok=False, data={}, error=last_error or "unknown")

    def usage_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.available,
            "model": self.model if self.available else None,
            "calls": self.call_count,
            "errors": self.error_count,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read_tokens,
            "estimated_cost_usd": round(
                self.total_input_tokens / 1_000_000 * 5.0
                + self.total_output_tokens / 1_000_000 * 25.0
                + self.total_cache_read_tokens / 1_000_000 * 0.5,
                4,
            ),
        }

    async def aclose(self) -> None:
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if close is not None:
                await close()
