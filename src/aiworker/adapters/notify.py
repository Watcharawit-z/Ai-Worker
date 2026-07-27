"""ช่องทางแจ้งเตือนคน

เรื่องที่ AI ตัดสินใจเองไม่ได้ต้องถึงมือคนภายในไม่กี่วินาที
รองรับ console / Telegram / webhook ทั่วไป (เช่น Discord, Slack, n8n)
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_ICON = {
    "info": "ℹ️",
    "low": "🔔",
    "medium": "⚠️",
    "high": "🚨",
    "critical": "🆘",
}


class ConsoleNotifier:
    """พิมพ์ลง terminal — ใช้ตอนพัฒนา"""

    async def send(self, title: str, body: str, severity: str) -> bool:
        icon = _ICON.get(severity, "•")
        print(f"\n{icon}  [{severity.upper()}] {title}\n    {body}\n", flush=True)
        return True


class TelegramNotifier:
    """ส่งเข้า Telegram — ตั้ง TELEGRAM_BOT_TOKEN และ TELEGRAM_CHAT_ID ใน .env"""

    def __init__(self, token: str = "", chat_id: str = "", **_: Any) -> None:
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    async def send(self, title: str, body: str, severity: str) -> bool:
        if not self.token or not self.chat_id:
            return False
        try:
            import httpx
        except ImportError:
            log.warning("ต้องติดตั้ง httpx ก่อนถึงจะส่ง Telegram ได้")
            return False

        icon = _ICON.get(severity, "•")
        text = f"{icon} *{title}*\n{body}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self.token}/sendMessage",
                    json={"chat_id": self.chat_id, "text": text, "parse_mode": "Markdown"},
                )
                return resp.status_code == 200
        except Exception as exc:  # noqa: BLE001
            log.warning("ส่ง Telegram ไม่สำเร็จ: %s", exc)
            return False


class WebhookNotifier:
    """ยิง JSON เข้า URL ที่กำหนด — ใช้กับ Discord/Slack/n8n/Make ได้หมด"""

    def __init__(self, url: str = "", **_: Any) -> None:
        self.url = url or os.environ.get("NOTIFY_WEBHOOK_URL", "")

    async def send(self, title: str, body: str, severity: str) -> bool:
        if not self.url:
            return False
        try:
            import httpx
        except ImportError:
            return False
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self.url,
                    json={
                        "severity": severity,
                        "title": title,
                        "body": body,
                        "content": f"[{severity.upper()}] {title}\n{body}",
                    },
                )
                return 200 <= resp.status_code < 300
        except Exception as exc:  # noqa: BLE001
            log.warning("ยิง webhook ไม่สำเร็จ: %s", exc)
            return False


class NotifierGroup:
    """ส่งพร้อมกันหลายช่องทาง ช่องไหนพังก็ไม่ลากช่องอื่นล่ม"""

    def __init__(self, channels: list[Any]) -> None:
        self.channels = channels

    async def send(self, title: str, body: str, severity: str) -> bool:
        results = []
        for channel in self.channels:
            try:
                results.append(await channel.send(title, body, severity))
            except Exception as exc:  # noqa: BLE001
                log.warning("ช่องทางแจ้งเตือน %s พัง: %s", type(channel).__name__, exc)
                results.append(False)
        return any(results)
