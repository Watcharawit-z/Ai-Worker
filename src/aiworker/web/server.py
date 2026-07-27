"""หน้าจอเฝ้าดู — เปิดในเบราว์เซอร์เพื่อดูว่าพนักงาน AI กำลังทำอะไรอยู่

ตั้งใจใช้ http.server ของ Python เอง ไม่พึ่ง framework
เพราะเป้าหมายคือให้ติดตั้งง่ายที่สุด (ไม่ต้อง pip install อะไรเพิ่ม)
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def _make_handler(snapshot_fn: Callable[[], dict[str, Any]]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:  # ปิด log ของ http.server ไม่ให้รก
            pass

        def _respond(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - ชื่อบังคับโดย BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                html = (STATIC_DIR / "dashboard.html").read_bytes()
                self._respond(200, html, "text/html; charset=utf-8")
            elif path == "/api/state":
                try:
                    payload = json.dumps(
                        snapshot_fn(), ensure_ascii=False, default=str
                    ).encode()
                except Exception as exc:  # noqa: BLE001
                    payload = json.dumps({"error": str(exc)}).encode()
                self._respond(200, payload, "application/json; charset=utf-8")
            elif path == "/healthz":
                self._respond(200, b"ok", "text/plain")
            else:
                self._respond(404, b"not found", "text/plain")

    return Handler


class Dashboard:
    """รัน HTTP server ใน thread แยก ไม่รบกวน event loop ของ agent"""

    def __init__(
        self, snapshot_fn: Callable[[], dict[str, Any]], host: str, port: int
    ) -> None:
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._snapshot_fn = snapshot_fn

    def start(self) -> bool:
        try:
            self._server = ThreadingHTTPServer(
                (self.host, self.port), _make_handler(self._snapshot_fn)
            )
        except OSError as exc:
            log.warning("เปิดหน้า dashboard ไม่ได้ (%s) — ระบบยังทำงานปกติ", exc)
            return False
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="dashboard", daemon=True
        )
        self._thread.start()
        log.info("เปิดหน้าเฝ้าดูที่ http://%s:%s", self.host, self.port)
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
