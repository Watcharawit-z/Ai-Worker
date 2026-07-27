"""ต่อกับ OBS Studio ผ่าน obs-websocket v5

ทำไมถึงเลือก OBS: คนรีรันไลฟ์ส่วนใหญ่ใช้ OBS อยู่แล้ว และมันเปิด websocket
ให้สั่งงานได้ฟรี ไม่ต้องพึ่ง API ที่ TikTok ไม่เปิดให้

การตั้งค่าฝั่ง OBS (ทำครั้งเดียว):
1. Tools → WebSocket Server Settings → เปิด Enable, ตั้งรหัสผ่าน
2. สร้าง Scene ชื่อ "RERUN" และใส่ Media Source ชื่อ "SEGMENT" ไว้ข้างใน
3. ใส่ host/port/password ลงใน config.yaml ที่ adapters.options.obs
"""

from __future__ import annotations

import logging
from typing import Any

from ..domain.segments import Segment
from .base import HealthSnapshot

log = logging.getLogger(__name__)


class OBSPlayer:
    """สั่ง OBS ให้เปลี่ยนไฟล์วิดีโอใน Media Source และอ่านสถานะสตรีม"""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 4455,
        password: str = "",
        scene: str = "RERUN",
        media_source: str = "SEGMENT",
        **_: Any,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self.scene = scene
        self.media_source = media_source
        self._ws: Any = None
        self._current: Segment | None = None

    async def connect(self) -> bool:
        try:
            import obsws_python as obs
        except ImportError:
            log.error(
                "ต้องติดตั้ง obsws-python ก่อน: pip install obsws-python "
                "(ตอนนี้ระบบจะใช้ adapter จำลองแทน)"
            )
            return False
        try:
            self._ws = obs.ReqClient(
                host=self.host, port=self.port, password=self.password, timeout=5
            )
            log.info("ต่อ OBS ที่ %s:%s สำเร็จ", self.host, self.port)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("ต่อ OBS ไม่สำเร็จ: %s", exc)
            self._ws = None
            return False

    async def disconnect(self) -> None:
        if self._ws is not None:
            try:
                self._ws.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    async def play_segment(self, segment: Segment) -> bool:
        if self._ws is None:
            return False
        try:
            self._ws.set_input_settings(
                self.media_source,
                {"local_file": segment.path, "restart_on_activate": True},
                overlay=True,
            )
            self._ws.set_current_program_scene(self.scene)
            self._current = segment
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("สั่งเล่นท่อน %s ไม่สำเร็จ: %s", segment.id, exc)
            return False

    async def start(self) -> bool:
        if self._ws is None:
            return False
        try:
            self._ws.start_stream()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("สั่งเริ่มสตรีมไม่สำเร็จ: %s", exc)
            return False

    async def stop(self) -> bool:
        if self._ws is None:
            return False
        try:
            self._ws.stop_stream()
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("สั่งหยุดสตรีมไม่สำเร็จ: %s", exc)
            return False

    async def health(self) -> HealthSnapshot:
        if self._ws is None:
            return HealthSnapshot(is_live=False)
        try:
            status = self._ws.get_stream_status()
            stats = self._ws.get_stats()
            remaining = 0.0
            if self._current is not None:
                try:
                    media = self._ws.get_media_input_status(self.media_source)
                    cursor_ms = getattr(media, "media_cursor", 0) or 0
                    duration_ms = getattr(media, "media_duration", 0) or 0
                    remaining = max(0.0, (duration_ms - cursor_ms) / 1000.0)
                except Exception:  # noqa: BLE001 - บาง source ไม่รายงานตำแหน่ง
                    remaining = self._current.duration_seconds

            bytes_sent = getattr(status, "output_bytes", 0) or 0
            duration_s = max(1.0, (getattr(status, "output_duration", 0) or 0) / 1000.0)
            return HealthSnapshot(
                is_live=bool(getattr(status, "output_active", False)),
                bitrate_kbps=int(bytes_sent * 8 / duration_s / 1000),
                dropped_frames=int(getattr(status, "output_skipped_frames", 0) or 0),
                fps=float(getattr(stats, "active_fps", 0.0) or 0.0),
                viewers=0,  # OBS ไม่รู้จำนวนคนดู ต้องดึงจากฝั่ง TikTok
                current_segment=self._current.id if self._current else None,
                seconds_remaining=remaining,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("อ่านสถานะ OBS ไม่ได้: %s", exc)
            return HealthSnapshot(is_live=False)
