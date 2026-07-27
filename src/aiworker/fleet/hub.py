"""Hub — รวมสถานะของทุกเครื่องมาไว้ที่จอเดียว

ปัญหาที่แก้: รีรัน 4-5 เครื่องพร้อมกัน คนเดียวดูไม่ไหว
โดยเฉพาะปริศนาจิ๊กซอว์ที่เด้งมาเครื่องไหนก็ได้ และมีเวลาแค่ 5 นาที

วิธีทำงาน: แต่ละเครื่องรัน worker แล้วส่งสถานะเข้ามาที่ hub ทุกไม่กี่วินาที
hub รวมทุกอย่างไว้หน้าเดียว เรียงตามความด่วน — เครื่องที่มีจิ๊กซอว์ค้างขึ้นบนสุด

ทิศทางการเชื่อมต่อ: worker → hub เท่านั้น
hub ไม่ต้องรู้จัก IP ของเครื่องลูก และไม่ยิงคำสั่งกลับไป
เครื่องลูกจึงไม่ต้องเปิด port อะไรทิ้งไว้เลย
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "web" / "static"
MAX_BODY_BYTES = 1_000_000


class FleetRegistry:
    """เก็บสถานะล่าสุดของแต่ละเครื่อง"""

    def __init__(self, offline_after_seconds: float = 30.0) -> None:
        self.offline_after = offline_after_seconds
        self._machines: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def update(self, machine: str, snapshot: dict[str, Any]) -> None:
        with self._lock:
            self._machines[machine] = {"received_at": time.time(), "snapshot": snapshot}

    def overview(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            rows = [
                {
                    "machine": name,
                    "age_seconds": round(now - entry["received_at"], 1),
                    "online": (now - entry["received_at"]) <= self.offline_after,
                    "snapshot": entry["snapshot"],
                }
                for name, entry in self._machines.items()
            ]

        rows.sort(key=_urgency, reverse=True)

        pending = [
            r
            for r in rows
            if (r["snapshot"].get("verification") or {}).get("current")
        ]
        return {
            "machines": rows,
            "total": len(rows),
            "online": sum(1 for r in rows if r["online"]),
            "offline": [r["machine"] for r in rows if not r["online"]],
            "challenges_pending": len(pending),
            "totals": _totals(rows),
            "generated_at": now,
        }


def _urgency(row: dict[str, Any]) -> tuple:
    """เรียงลำดับความด่วน — เรื่องที่มีนาฬิกาเดินอยู่ต้องขึ้นก่อน"""
    snap = row["snapshot"]
    verification = snap.get("verification") or {}
    current = verification.get("current") or {}
    stream = snap.get("stream") or {}

    has_challenge = 1 if current else 0
    # เหลือเวลาน้อย = ด่วนกว่า จึงกลับเครื่องหมาย
    time_pressure = -float(current.get("seconds_left", 9999)) if current else -9999
    stream_down = 1 if not stream.get("is_live", False) else 0
    offline = 1 if not row["online"] else 0
    return (has_challenge, time_pressure, stream_down, offline)


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    revenue = orders = spend = 0.0
    missed = comments = 0
    for row in rows:
        snap = row["snapshot"]
        baskets = snap.get("baskets") or {}
        ads = snap.get("ads") or {}
        verification = snap.get("verification") or {}
        revenue += float(baskets.get("total_revenue", 0) or 0)
        orders += float(baskets.get("total_orders", 0) or 0)
        spend += float(ads.get("spend", 0) or 0)
        missed += int(verification.get("missed", 0) or 0)
        comments += int((snap.get("comments") or {}).get("received", 0) or 0)
    return {
        "revenue": round(revenue, 2),
        "orders": int(orders),
        "ad_spend": round(spend, 2),
        "cpa": round(spend / orders, 2) if orders else 0.0,
        "missed_challenges": missed,
        "comments": comments,
    }


def _make_handler(registry: FleetRegistry):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:
            pass

        def _respond(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/api/report":
                self._respond(404, b"not found", "text/plain")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._respond(400, b"bad length", "text/plain")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._respond(400, b"bad length", "text/plain")
                return
            try:
                payload = json.loads(self.rfile.read(length))
                machine = str(payload["machine"])
                registry.update(machine, payload.get("snapshot") or {})
            except Exception as exc:  # noqa: BLE001
                self._respond(400, str(exc).encode(), "text/plain")
                return
            self._respond(200, b'{"ok":true}', "application/json")

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                html = (STATIC_DIR / "fleet.html").read_bytes()
                self._respond(200, html, "text/html; charset=utf-8")
            elif path == "/api/fleet":
                body = json.dumps(
                    registry.overview(), ensure_ascii=False, default=str
                ).encode()
                self._respond(200, body, "application/json; charset=utf-8")
            elif path == "/healthz":
                self._respond(200, b"ok", "text/plain")
            else:
                self._respond(404, b"not found", "text/plain")

    return Handler


class FleetHub:
    """เซิร์ฟเวอร์รวมสถานะทุกเครื่อง"""

    def __init__(self, host: str, port: int, offline_after_seconds: float = 30.0) -> None:
        self.host = host
        self.port = port
        self.registry = FleetRegistry(offline_after_seconds)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        try:
            self._server = ThreadingHTTPServer(
                (self.host, self.port), _make_handler(self.registry)
            )
        except OSError as exc:
            log.error("เปิด hub ไม่ได้: %s", exc)
            return False
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fleet-hub", daemon=True
        )
        self._thread.start()
        log.info("Hub พร้อมแล้วที่ http://%s:%s", self.host, self.port)
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
