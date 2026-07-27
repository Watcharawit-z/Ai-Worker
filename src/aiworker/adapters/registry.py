"""เลือก adapter ตาม config

เพิ่ม adapter ใหม่: เขียนคลาสให้ครบเมธอดใน base.py แล้วมาลงทะเบียนที่นี่
"""

from __future__ import annotations

import logging
from typing import Any

from ..settings import Settings
from .mock import MockAds, MockCommentSender, MockCommentSource, MockPlayer, MockShop
from .notify import ConsoleNotifier, NotifierGroup, TelegramNotifier, WebhookNotifier

log = logging.getLogger(__name__)


def build_player(settings: Settings) -> Any:
    kind = settings.adapters.player
    opts = settings.adapters.options.get("obs", {})
    if kind == "obs":
        from .obs_player import OBSPlayer

        return OBSPlayer(**opts)
    if kind != "mock":
        log.warning("ไม่รู้จัก player adapter '%s' — ใช้ mock แทน", kind)
    return MockPlayer(**settings.adapters.options.get("mock_player", {}))


def build_comment_source(settings: Settings) -> Any:
    kind = settings.adapters.comments
    if kind == "tiktok":
        from .tiktok_comments import TikTokCommentSource

        return TikTokCommentSource(**settings.adapters.options.get("tiktok", {}))
    if kind != "mock":
        log.warning("ไม่รู้จัก comment adapter '%s' — ใช้ mock แทน", kind)
    return MockCommentSource(**settings.adapters.options.get("mock_comments", {}))


def build_comment_sender(settings: Settings) -> Any:
    kind = settings.adapters.comments
    if kind == "tiktok":
        # TikTok ไม่เปิดให้ส่งคอมเมนท์ผ่าน API — เข้าคิวให้คนกดส่งแทน
        from .tiktok_comments import ReviewQueueSender

        return ReviewQueueSender()
    return MockCommentSender()


def build_shop(settings: Settings) -> Any:
    kind = settings.adapters.shop
    if kind != "mock":
        log.warning(
            "shop adapter '%s' ยังไม่ได้เขียน — ใช้ mock ไปก่อน "
            "(เขียนคลาสตาม ShopAdapter ใน adapters/base.py แล้วมาต่อที่นี่)",
            kind,
        )
    return MockShop(**settings.adapters.options.get("mock_shop", {}))


def build_ads(settings: Settings) -> Any:
    kind = settings.adapters.ads
    if kind != "mock":
        log.warning(
            "ads adapter '%s' ยังไม่ได้เขียน — ใช้ mock ไปก่อน "
            "(TikTok Ads API ต้องสมัคร developer account ก่อน)",
            kind,
        )
    return MockAds(**settings.adapters.options.get("mock_ads", {}))


def build_notifiers(settings: Settings) -> NotifierGroup:
    channels: list[Any] = []
    opts = settings.adapters.options
    for name in settings.adapters.notify:
        if name == "console":
            channels.append(ConsoleNotifier())
        elif name == "telegram":
            channels.append(TelegramNotifier(**opts.get("telegram", {})))
        elif name == "webhook":
            channels.append(WebhookNotifier(**opts.get("webhook", {})))
        else:
            log.warning("ไม่รู้จักช่องทางแจ้งเตือน '%s'", name)
    if not channels:
        channels.append(ConsoleNotifier())
    return NotifierGroup(channels)
