"""เทสตรรกะธุรกิจ — ส่วนที่ต้องถูกเสมอเพราะเกี่ยวกับเงิน"""

from __future__ import annotations

import random
import time

import pytest

from aiworker.domain.basket import BasketQueue, BasketState, Verdict
from aiworker.domain.compliance_rules import ComplianceRules
from aiworker.domain.segments import SegmentPlaylist
from aiworker.events import Severity

ROWS = [
    {"id": "b1", "name": "สินค้า A", "sku": "S1", "price": 100, "cost": 30, "stock": 50},
    {"id": "b2", "name": "สินค้า B", "sku": "S2", "price": 200, "cost": 60, "stock": 50},
    {"id": "b3", "name": "สินค้า C", "sku": "S3", "price": 300, "cost": 90, "stock": 0},
]

EVAL_ARGS = {
    "min_minutes": 20.0,
    "evaluate_after_minutes": 60.0,
    "max_minutes": 90.0,
    "min_orders": 3,
    "target_roas": 3.0,
}


# ---------------------------------------------------------------- ตะกร้า


def test_activate_next_walks_the_queue_in_order():
    q = BasketQueue.from_config(ROWS)
    assert q.live is None

    first = q.activate_next()
    assert first is not None and first.id == "b1"
    assert q.position == 1
    assert first.state is BasketState.LIVE

    second = q.activate_next(reason="ครบเวลา")
    assert second is not None and second.id == "b2"
    assert q.baskets[0].state is BasketState.DONE
    assert "ครบเวลา" in q.baskets[0].notes


def test_activate_next_skips_out_of_stock_baskets():
    q = BasketQueue.from_config(ROWS)
    q.activate_next()
    q.activate_next()
    # b3 สต็อกเป็น 0 ต้องถูกข้าม แล้วคิวหมด
    assert q.activate_next() is None


def test_upcoming_reports_the_next_baskets_for_the_host():
    q = BasketQueue.from_config(ROWS)
    q.activate_next()
    names = [b.name for b in q.upcoming(2)]
    assert names == ["สินค้า B"]  # C ถูกข้ามเพราะสต็อกเป็น 0
    assert len(q.upcoming(5)) == 1


def test_record_sale_decrements_stock_and_accumulates_revenue():
    q = BasketQueue.from_config(ROWS)
    q.activate_next()
    q.record_sale(3, 300.0)
    live = q.live
    assert live is not None
    assert live.orders == 3
    assert live.revenue == 300.0
    assert live.stock == 47


def test_margin_subtracts_cost_of_goods_and_ad_spend():
    q = BasketQueue.from_config(ROWS)
    q.activate_next()
    q.record_sale(10, 1000.0)
    q.record_ad_spend(200.0)
    # 1000 รายได้ - (30 ต้นทุน x 10 ชิ้น) - 200 ค่าแอด = 500
    assert q.live is not None
    assert q.live.margin == pytest.approx(500.0)


@pytest.mark.parametrize(
    ("minutes", "orders", "ad_spend", "expected"),
    [
        (5, 0, 0, Verdict.TOO_EARLY),  # เพิ่งขึ้น ยังไม่ตัดสิน
        (30, 1, 0, Verdict.TOO_EARLY),  # ผ่านขั้นต่ำ แต่ยังไม่ถึงชั่วโมงประเมิน
        (30, 20, 0, Verdict.SCALE),  # ติดแรงมาก สเกลได้เลยไม่ต้องรอ
        (95, 50, 0, Verdict.ROTATE),  # ชนเพดานเวลา เปลี่ยนแม้ยอดจะดี
        (61, 1, 0, Verdict.ROTATE),  # ครบชั่วโมงแล้วยอดไม่ถึงเกณฑ์
        (61, 4, 0, Verdict.KEEP_GOING),  # ถึงเกณฑ์ ไปต่อได้
        (61, 10, 100, Verdict.SCALE),  # ROAS ดี ควรสเกล
    ],
)
def test_evaluate_matches_the_shift_playbook(minutes, orders, ad_spend, expected):
    q = BasketQueue.from_config(ROWS)
    basket = q.activate_next()
    assert basket is not None
    basket.started_at = time.time() - minutes * 60
    basket.orders = orders
    basket.revenue = orders * 100.0
    basket.ad_spend = ad_spend
    assert q.evaluate(**EVAL_ARGS) is expected


def test_evaluate_rotates_when_ad_spend_burns_with_no_return():
    q = BasketQueue.from_config(ROWS)
    basket = q.activate_next()
    assert basket is not None
    basket.started_at = time.time() - 61 * 60
    basket.orders = 5
    basket.revenue = 500.0
    basket.ad_spend = 900.0  # ROAS 0.55 ต่ำกว่าครึ่งของเป้า 3.0
    assert q.evaluate(**EVAL_ARGS) is Verdict.ROTATE


def test_evaluate_reports_out_of_stock_before_anything_else():
    q = BasketQueue.from_config(ROWS)
    basket = q.activate_next()
    assert basket is not None
    basket.stock = 0
    assert q.evaluate(**EVAL_ARGS) is Verdict.OUT_OF_STOCK


# ---------------------------------------------------------------- จิ๊กซอว์


def _segments(n: int, sku: str = "") -> list[dict]:
    return [
        {"id": f"s{i}", "path": f"/m/{i}.mp4", "duration_seconds": 100, "sku": sku}
        for i in range(n)
    ]


def test_playlist_does_not_repeat_within_the_avoid_window():
    playlist = SegmentPlaylist.from_config(
        _segments(6), avoid_repeat_within=3, rng=random.Random(1)
    )
    picked = [playlist.next_segment().id for _ in range(20)]
    for i in range(3, len(picked)):
        assert picked[i] not in picked[i - 3 : i], "หยิบท่อนซ้ำเร็วเกินไป คนดูจับได้"


def test_playlist_prefers_segments_matching_the_live_basket():
    rows = _segments(3, sku="S1") + _segments(3, sku="S2")
    for i, row in enumerate(rows):
        row["id"] = f"seg{i}"
    playlist = SegmentPlaylist.from_config(rows, rng=random.Random(2))
    for _ in range(10):
        assert playlist.next_segment("S2").sku == "S2"


def test_playlist_falls_back_to_generic_clips_when_sku_has_none():
    rows = _segments(2, sku="S1") + _segments(2, sku="")
    for i, row in enumerate(rows):
        row["id"] = f"seg{i}"
    playlist = SegmentPlaylist.from_config(rows, rng=random.Random(3))
    # ยังไม่มีคลิปของ S9 → ต้องหยิบท่อนกลาง ไม่ใช่ท่อนของ S1
    for _ in range(6):
        assert playlist.next_segment("S9").sku == ""


def test_playlist_returns_none_when_there_is_nothing_to_play():
    assert SegmentPlaylist([]).next_segment() is None


def test_coverage_by_sku_flags_thin_content():
    rows = _segments(2, sku="S1") + _segments(1, sku="")
    for i, row in enumerate(rows):
        row["id"] = f"seg{i}"
    coverage = SegmentPlaylist.from_config(rows).coverage_by_sku()
    assert coverage["S1"] == 200
    assert coverage["_generic"] == 100


# ---------------------------------------------------------------- การละเมิด


def test_rules_catch_off_platform_selling():
    rules = ComplianceRules()
    hits = rules.check("สนใจทักไลน์ไอดี shop123 นะคะ", source="comment")
    assert hits
    assert any(h.code == "offplatform_sale" for h in hits)
    assert ComplianceRules.worst(hits) is Severity.HIGH


def test_rules_treat_bank_transfer_as_critical():
    rules = ComplianceRules()
    hits = rules.check("โอนเงินเข้าบัญชีได้เลยค่ะ", source="our_reply")
    assert ComplianceRules.worst(hits) is Severity.CRITICAL


def test_rules_catch_medical_claims():
    rules = ComplianceRules()
    assert any(
        h.code == "medical_claim" for h in rules.check("ทาแล้วรักษาหายขาดแน่นอน")
    )


def test_rules_honour_shop_specific_banned_words():
    rules = ComplianceRules(banned_words=["ของก๊อป"])
    hits = rules.check("ไม่ใช่ของก๊อปนะคะ")
    assert any(h.code == "banned_word" for h in hits)


def test_rules_stay_quiet_on_normal_selling_talk():
    rules = ComplianceRules()
    assert rules.check("ตัวนี้เนื้อบางเบา ซึมไว ใช้ได้ทุกวันค่ะ") == []
    assert rules.check("กดตะกร้าสีเหลืองมุมล่างซ้ายได้เลยค่ะ") == []


def test_rules_survive_a_broken_regex_in_config():
    rules = ComplianceRules(extra_claim_patterns=["([unclosed"])
    assert rules.check("ข้อความปกติ") == []
