"""เทสตรรกะธุรกิจ — ส่วนที่ต้องถูกเสมอเพราะเกี่ยวกับเงินและการโดนเตือน"""

from __future__ import annotations

import random
import time

import pytest

from aiworker.domain.basket import BasketBoard
from aiworker.domain.compliance_rules import ComplianceRules
from aiworker.domain.cues import CueSheet
from aiworker.domain.segments import SegmentPlaylist
from aiworker.domain.verification import ChallengeState, ChallengeTracker
from aiworker.events import Severity

ROWS = [
    {"id": "b1", "name": "สินค้า A", "sku": "S1", "price": 100, "cost": 30, "stock": 50},
    {"id": "b2", "name": "สินค้า B", "sku": "S2", "price": 200, "cost": 60, "stock": 50},
    {"id": "b3", "name": "สินค้า C", "sku": "S3", "price": 300, "cost": 90, "stock": 0},
]


# ---------------------------------------------------------------- ตะกร้า


def test_board_finds_baskets_by_sku():
    board = BasketBoard.from_config(ROWS)
    assert board.by_sku("S2").name == "สินค้า B"
    assert board.by_sku("ไม่มีจริง") is None
    assert board.known_skus() == {"S1", "S2", "S3"}


def test_pinning_switches_which_basket_is_displayed():
    board = BasketBoard.from_config(ROWS)
    assert board.pinned is None

    board.mark_pinned("b1")
    assert board.pinned.id == "b1"
    assert board.pinned.pin_count == 1

    board.mark_pinned("b2")
    assert board.pinned.id == "b2"
    # ใบเดิมเลิกปักแล้ว แต่ยังอยู่ในไลฟ์
    assert board.by_id("b1").is_pinned is False
    assert board.by_id("b1") in board.baskets


def test_pinned_time_accumulates_across_multiple_turns():
    board = BasketBoard.from_config(ROWS)
    board.mark_pinned("b1")
    board._pinned_at = time.time() - 60  # แกล้งว่าปักมาแล้วหนึ่งนาที
    board.mark_pinned("b2")
    assert board.by_id("b1").pinned_seconds == pytest.approx(60, abs=2)

    board._pinned_at = time.time() - 30
    board.mark_pinned("b1")
    board._pinned_at = time.time() - 30
    board.settle_pinned_time()
    # ปักรอบสอง 30 วิ รวมเป็น 90
    assert board.by_id("b1").pinned_seconds == pytest.approx(90, abs=2)
    assert board.by_id("b1").pin_count == 2


def test_sales_are_recorded_against_the_sku_that_sold():
    board = BasketBoard.from_config(ROWS)
    board.record_sale("S1", 3, 300.0)
    assert board.by_sku("S1").orders == 3
    assert board.by_sku("S1").stock == 47
    # 300 รายได้ - (30 ต้นทุน x 3 ชิ้น) = 210
    assert board.by_sku("S1").gross_margin == pytest.approx(210.0)


def test_sales_for_an_unknown_sku_are_ignored_not_crash():
    board = BasketBoard.from_config(ROWS)
    board.record_sale("ไม่มีจริง", 5, 500.0)
    assert board.summary()["total_orders"] == 0


def test_never_pinned_lists_products_the_clip_never_mentions():
    board = BasketBoard.from_config(ROWS)
    board.mark_pinned("b1")
    names = [b.name for b in board.never_pinned()]
    assert names == ["สินค้า B", "สินค้า C"]


# ---------------------------------------------------------------- คิวชีต


def test_cue_sheet_maps_playback_position_to_the_right_product():
    sheet = CueSheet.from_config(
        [
            {"at_seconds": 0, "sku": "S1"},
            {"at_seconds": 600, "sku": "S2"},
            {"at_seconds": 1200, "sku": "S3"},
        ]
    )
    assert sheet.sku_at(0) == "S1"
    assert sheet.sku_at(599) == "S1"
    assert sheet.sku_at(600) == "S2"  # ถึงจุดเปลี่ยนพอดี ต้องเปลี่ยนแล้ว
    assert sheet.sku_at(1500) == "S3"


def test_cue_sheet_falls_back_before_the_first_cue():
    sheet = CueSheet.from_config([{"at_seconds": 300, "sku": "S2"}], default_sku="S1")
    assert sheet.sku_at(0) == "S1"
    assert sheet.sku_at(299) == "S1"
    assert sheet.sku_at(300) == "S2"


def test_cue_sheet_sorts_out_of_order_config():
    sheet = CueSheet.from_config(
        [{"at_seconds": 600, "sku": "S2"}, {"at_seconds": 0, "sku": "S1"}]
    )
    assert sheet.sku_at(100) == "S1"
    assert sheet.sku_at(700) == "S2"


def test_next_change_tells_when_the_basket_must_switch():
    sheet = CueSheet.from_config(
        [{"at_seconds": 0, "sku": "S1"}, {"at_seconds": 600, "sku": "S2"}]
    )
    upcoming = sheet.next_change(100)
    assert upcoming is not None and upcoming.at_seconds == 600
    assert sheet.next_change(700) is None


def test_validate_catches_cues_pointing_at_missing_baskets():
    sheet = CueSheet.from_config(
        [{"at_seconds": 0, "sku": "S1"}, {"at_seconds": 60, "sku": "ผี"}]
    )
    problems = sheet.validate_against({"S1", "S2"})
    assert len(problems) == 1
    assert "ผี" in problems[0]


def test_empty_cue_sheet_is_falsy_and_uses_the_segment_sku():
    sheet = CueSheet.from_config([], default_sku="S9")
    assert not sheet
    assert sheet.sku_at(999) == "S9"


# ---------------------------------------------------------------- คลิป


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


def test_playlist_returns_none_when_there_is_nothing_to_play():
    assert SegmentPlaylist([]).next_segment() is None


def test_segment_reads_its_own_cue_sheet():
    playlist = SegmentPlaylist.from_config(
        [
            {
                "id": "full",
                "path": "/m/full.mp4",
                "duration_seconds": 3600,
                "cues": [
                    {"at_seconds": 0, "sku": "S1"},
                    {"at_seconds": 1800, "sku": "S2"},
                ],
            }
        ]
    )
    segment = playlist.segments[0]
    assert segment.sku_at(10) == "S1"
    assert segment.sku_at(2000) == "S2"
    assert segment.all_skus() == ["S1", "S2"]


def test_segment_without_cues_falls_back_to_its_sku():
    playlist = SegmentPlaylist.from_config(
        [{"id": "a", "path": "/a.mp4", "duration_seconds": 60, "sku": "S5"}]
    )
    assert playlist.segments[0].sku_at(30) == "S5"


# ---------------------------------------------------------------- จิ๊กซอว์


def test_tracker_opens_one_challenge_not_one_per_poll():
    tracker = ChallengeTracker("pc-1", deadline_seconds=300)
    first = tracker.on_detected()
    assert first is not None
    # ตัวเฝ้าจอยิงสัญญาณทุกรอบที่ปริศนายังอยู่ ต้องไม่นับเป็นครั้งใหม่
    assert tracker.on_detected() is None
    assert tracker.on_detected() is None
    assert tracker.current is first


def test_tracker_ignores_low_confidence_signals():
    tracker = ChallengeTracker("pc-1", min_confidence=0.8)
    assert tracker.on_detected(confidence=0.5) is None
    assert tracker.current is None
    assert tracker.on_detected(confidence=0.9) is not None


def test_clearing_marks_the_challenge_solved_with_response_time():
    tracker = ChallengeTracker("pc-1")
    tracker.on_detected()
    tracker.current.detected_at = time.time() - 40
    solved = tracker.on_cleared()
    assert solved is not None
    assert solved.state is ChallengeState.SOLVED
    assert solved.seconds_taken == pytest.approx(40, abs=2)
    assert tracker.current is None


def test_clearing_when_nothing_pending_is_harmless():
    assert ChallengeTracker("pc-1").on_cleared() is None


def test_expiry_marks_it_missed_once_and_only_once():
    tracker = ChallengeTracker("pc-1", deadline_seconds=300)
    tracker.on_detected()
    tracker.current.detected_at = time.time() - 301

    missed = tracker.check_expiry()
    assert missed is not None and missed.state is ChallengeState.MISSED
    assert tracker.check_expiry() is None  # ไม่รายงานซ้ำ
    assert tracker.stats()["missed"] == 1


def test_escalation_steps_fire_in_order_and_never_twice():
    tracker = ChallengeTracker("pc-1", deadline_seconds=300)
    tracker.on_detected()

    first = tracker.due_escalations()
    assert len(first) == 1  # ขั้นแรกยิงทันที
    assert tracker.due_escalations() == []  # ขั้นเดิมไม่ยิงซ้ำ

    tracker.current.detected_at = time.time() - 130
    later = tracker.due_escalations()
    assert len(later) == 2  # ขั้น 45 วิ และ 120 วิ ถึงเวลาพร้อมกัน
    assert tracker.due_escalations() == []


def test_new_challenge_resets_the_escalation_ladder():
    tracker = ChallengeTracker("pc-1")
    tracker.on_detected()
    tracker.due_escalations()
    tracker.on_cleared()

    tracker.on_detected()
    assert len(tracker.due_escalations()) == 1, "ปริศนาครั้งใหม่ต้องปลุกใหม่ตั้งแต่ขั้นแรก"


def test_seconds_left_counts_down_and_floors_at_zero():
    tracker = ChallengeTracker("pc-1", deadline_seconds=300)
    challenge = tracker.on_detected()
    challenge.detected_at = time.time() - 120
    assert challenge.seconds_left == pytest.approx(180, abs=2)
    challenge.detected_at = time.time() - 999
    assert challenge.seconds_left == 0.0


def test_stats_report_response_times():
    tracker = ChallengeTracker("pc-1")
    tracker.on_detected()
    tracker.current.detected_at = time.time() - 20
    tracker.on_cleared()
    tracker.on_detected()
    tracker.current.detected_at = time.time() - 60
    tracker.on_cleared()

    stats = tracker.stats()
    assert stats["solved"] == 2
    assert stats["avg_response_seconds"] == pytest.approx(40, abs=2)
    assert stats["worst_response_seconds"] == pytest.approx(60, abs=2)


# ---------------------------------------------------------------- การละเมิด


def test_rules_catch_off_platform_selling():
    rules = ComplianceRules()
    hits = rules.check("สนใจทักไลน์ไอดี shop123 นะคะ", source="comment")
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


def test_rules_report_each_problem_once_not_once_per_pattern():
    rules = ComplianceRules()
    hits = rules.check("ขอไลน์หน่อยค่ะ")
    codes = [h.code for h in hits]
    assert len(codes) == len(set(codes)), "เรื่องเดียวกันต้องนับครั้งเดียว"


def test_rules_honour_shop_specific_banned_words():
    rules = ComplianceRules(banned_words=["ของก๊อป"])
    assert any(h.code == "banned_word" for h in rules.check("ไม่ใช่ของก๊อปนะคะ"))


def test_rules_stay_quiet_on_normal_selling_talk():
    rules = ComplianceRules()
    assert rules.check("ตัวนี้เนื้อบางเบา ซึมไว ใช้ได้ทุกวันค่ะ") == []
    assert rules.check("กดตะกร้าสีเหลืองมุมล่างซ้ายได้เลยค่ะ") == []
    assert rules.check("ตะกร้าที่ 3 ราคา 590 บาทค่ะ") == []


def test_rules_survive_a_broken_regex_in_config():
    rules = ComplianceRules(extra_claim_patterns=["([unclosed"])
    assert rules.check("ข้อความปกติ") == []
