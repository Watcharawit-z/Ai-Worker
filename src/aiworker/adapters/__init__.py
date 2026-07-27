"""จุดเชื่อมต่อกับโลกภายนอก"""

from .registry import (
    build_ads,
    build_comment_sender,
    build_comment_source,
    build_notifiers,
    build_player,
    build_shop,
)

__all__ = [
    "build_ads",
    "build_comment_sender",
    "build_comment_source",
    "build_notifiers",
    "build_player",
    "build_shop",
]
