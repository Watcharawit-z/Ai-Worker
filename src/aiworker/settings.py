"""โหลด config จาก YAML + environment variable"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/config.yaml")
DEFAULT_MODEL = "claude-opus-5"


@dataclass(slots=True)
class ShopSettings:
    name: str = "ร้านค้า"
    channel_id: str = "channel-01"
    tone: str = "สุภาพ เป็นกันเอง ใช้คำว่า 'ค่ะ' ลงท้าย"
    shipping_note: str = "ส่งภายใน 1-2 วันทำการ"
    return_policy: str = "เปลี่ยน/คืนภายใน 7 วัน สินค้าต้องอยู่ในสภาพเดิม"


@dataclass(slots=True)
class LLMSettings:
    model: str = DEFAULT_MODEL
    effort: str = "medium"
    max_tokens: int = 2000
    enabled: bool = True
    """ถ้าไม่มี API key จะ fallback ไปโหมด rule-based อัตโนมัติ"""


@dataclass(slots=True)
class CommentSettings:
    batch_seconds: float = 4.0
    max_batch: int = 12
    max_replies_per_batch: int = 4
    min_confidence_to_send: float = 0.6
    cooldown_per_user_seconds: float = 30.0
    max_reply_chars: int = 120


@dataclass(slots=True)
class ComplianceSettings:
    check_interval_seconds: float = 20.0
    llm_review_interval_seconds: float = 90.0
    auto_switch_segment_at: str = "high"
    auto_stop_at: str = "critical"
    banned_words: list[str] = field(default_factory=list)
    risky_claim_patterns: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BasketSettings:
    """ตะกร้าเดินตามสิ่งที่คนในคลิปพูด ไม่ได้เดินตามเวลา"""

    pin_check_seconds: float = 5.0
    """เช็คบ่อยแค่ไหนว่าตะกร้าที่ปักตรงกับที่พูดอยู่หรือยัง"""

    pin_lead_seconds: float = 2.0
    """ปักล่วงหน้ากี่วินาทีก่อนถึงจังหวะที่พูดถึงสินค้าตัวใหม่"""

    alert_on_missing_sku: bool = True
    """คิวชี้ไปยังสินค้าที่ไม่มีในตะกร้า → เตือนคน"""


@dataclass(slots=True)
class AdsSettings:
    """ไลฟ์นายหน้า — เพดานค่าแอดคิดจากค่าคอมที่ได้จริง ไม่ใช่ตัวเลขที่ตั้งลอย ๆ

    เหตุผล: ค่าคอมนายหน้าอยู่ที่ 10-40 บาทต่อชิ้น ถ้าตั้งเพดาน CPA ไว้สูงกว่านั้น
    ทุกออเดอร์ที่ขายได้คือการขาดทุน และยิ่งแอดทำงานดี ยิ่งเจ๊งเร็ว
    ระบบจึงคำนวณเพดานจากค่าคอมของสินค้าที่ขายอยู่จริงให้อัตโนมัติ

    แอดผูกกับ 'ไลฟ์' ไม่ได้ผูกกับ 'ตะกร้า'
    เปลี่ยนตะกร้าไม่ต้องแตะแอด ขึ้นไลฟ์ใหม่ถึงจะขึ้นแอดใหม่
    """

    enabled: bool = True
    campaign_type: str = "gmv_max_live"
    starting_budget: float = 300.0
    max_budget: float = 2000.0
    scale_step: float = 1.3

    cpa_ceiling_ratio: float = 0.6
    """เพดาน CPA = ค่าคอมเฉลี่ย x ค่านี้ (0.6 = ยอมจ่ายค่าแอดได้ 60% ของค่าคอม)"""

    cpa_target_ratio: float = 0.35
    """ถูกกว่า ค่าคอม x ค่านี้ ถือว่าคุ้มมาก สเกลได้"""

    max_cpa_override: float = 0.0
    """ตั้งเองแทนการคำนวณจากค่าคอม (0 = ปิด ใช้การคำนวณ)"""

    max_loss_baht: float = 500.0
    """ขาดทุนสะสมในไลฟ์นี้เกินเท่าไหร่ = หยุดยิงแอดทันที ไม่ต้องรอเงื่อนไขอื่น"""

    min_spend_before_judging: float = 150.0
    min_purchases_before_judging: int = 2
    grace_minutes_after_launch: float = 20.0
    """แอดเพิ่งขึ้น ตัวเลขยังไม่นิ่ง อย่าเพิ่งตัดสิน"""

    relaunch_cooldown_minutes: float = 30.0
    """ปิดแล้วขึ้นใหม่ได้เร็วสุดเท่าไหร่ กันขึ้น-ปิดวนไม่จบ"""

    max_relaunches_per_live: int = 3
    check_interval_seconds: float = 60.0

    default_commission: float = 20.0
    """ใช้เมื่อตะกร้าไม่ได้ระบุค่าคอม — ตั้งให้ต่ำไว้ปลอดภัยกว่าตั้งสูง"""


@dataclass(slots=True)
class VerificationSettings:
    """ปริศนายืนยันตัวตน (จิ๊กซอว์)"""

    enabled: bool = True
    machine_name: str = ""
    deadline_seconds: float = 300.0
    poll_seconds: float = 3.0
    min_confidence: float = 0.6
    watcher: str = "mock"
    """mock | template — template ต้อง calibrate ก่อน"""


@dataclass(slots=True)
class StreamSettings:
    health_interval_seconds: float = 5.0
    stall_alert_after_seconds: float = 15.0
    min_bitrate_kbps: int = 800
    max_dropped_frames: int = 300
    segment_min_seconds: float = 120.0
    shuffle_segments: bool = True
    avoid_repeat_within: int = 3


@dataclass(slots=True)
class AdapterSettings:
    comments: str = "mock"
    player: str = "mock"
    shop: str = "mock"
    ads: str = "mock"
    notify: list[str] = field(default_factory=lambda: ["console"])
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WebSettings:
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8765


@dataclass(slots=True)
class FleetSettings:
    """คุมหลายเครื่องพร้อมกัน — เครื่องละ worker ส่งสถานะเข้า hub เดียว"""

    role: str = "standalone"
    """standalone | worker | hub"""

    machine_name: str = ""
    hub_url: str = ""
    report_seconds: float = 5.0
    hub_host: str = "0.0.0.0"
    hub_port: int = 8700
    offline_after_seconds: float = 30.0
    """ไม่ได้ข่าวจากเครื่องไหนเกินนี้ = ถือว่าเครื่องนั้นหลุด"""


@dataclass(slots=True)
class Settings:
    shop: ShopSettings = field(default_factory=ShopSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    comments: CommentSettings = field(default_factory=CommentSettings)
    compliance: ComplianceSettings = field(default_factory=ComplianceSettings)
    baskets: BasketSettings = field(default_factory=BasketSettings)
    ads: AdsSettings = field(default_factory=AdsSettings)
    stream: StreamSettings = field(default_factory=StreamSettings)
    verification: VerificationSettings = field(default_factory=VerificationSettings)
    adapters: AdapterSettings = field(default_factory=AdapterSettings)
    web: WebSettings = field(default_factory=WebSettings)
    fleet: FleetSettings = field(default_factory=FleetSettings)
    products_path: str = "config/knowledge/products.yaml"
    baskets_path: str = "config/baskets.yaml"
    segments_path: str = "config/segments.yaml"

    @property
    def api_key(self) -> str | None:
        return os.environ.get("ANTHROPIC_API_KEY") or None


def _fill(cls: type, data: dict[str, Any] | None):
    """สร้าง dataclass จาก dict โดยข้าม key ที่ไม่รู้จัก (config เก่าจะไม่พัง)"""
    if not data:
        return cls()
    known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
    return cls(**{k: v for k, v in data.items() if k in known})


def load_settings(path: str | Path | None = None) -> Settings:
    cfg_path = Path(path or os.environ.get("AIWORKER_CONFIG") or DEFAULT_CONFIG_PATH)
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    settings = Settings(
        shop=_fill(ShopSettings, raw.get("shop")),
        llm=_fill(LLMSettings, raw.get("llm")),
        comments=_fill(CommentSettings, raw.get("comments")),
        compliance=_fill(ComplianceSettings, raw.get("compliance")),
        baskets=_fill(BasketSettings, raw.get("baskets")),
        ads=_fill(AdsSettings, raw.get("ads")),
        stream=_fill(StreamSettings, raw.get("stream")),
        verification=_fill(VerificationSettings, raw.get("verification")),
        adapters=_fill(AdapterSettings, raw.get("adapters")),
        web=_fill(WebSettings, raw.get("web")),
        fleet=_fill(FleetSettings, raw.get("fleet")),
    )
    for key in ("products_path", "baskets_path", "segments_path"):
        if key in raw:
            setattr(settings, key, raw[key])

    # ไม่มี key ก็รันได้ แต่จะเป็นโหมดกฎล้วน ไม่มี AI ตอบคอมเมนท์
    if not settings.api_key:
        settings.llm.enabled = False
    return settings


def load_yaml_list(path: str | Path, key: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if isinstance(data, list):
        return data
    return data.get(key) or []
