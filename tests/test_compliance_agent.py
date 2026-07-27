"""เทสว่าระบบแยกออกว่า 'ใครเป็นคนผิด'

จุดที่พลาดง่ายที่สุด: ลูกค้าพิมพ์ "ขอไลน์" แล้วระบบไปสลับคลิปหนี
ซึ่งไม่ช่วยอะไรเลย เพราะคนผิดคือลูกค้า ไม่ใช่เนื้อหาของเรา
"""

from __future__ import annotations

import asyncio

import pytest

from aiworker.agents.compliance_agent import ComplianceAgent
from aiworker.bus import EventBus
from aiworker.domain.basket import BasketBoard
from aiworker.domain.segments import SegmentPlaylist
from aiworker.events import CommentIn, CommentReply, ComplianceAction, Notification
from aiworker.llm import LLMClient
from aiworker.settings import Settings
from aiworker.state import ShiftState


def make_agent() -> tuple[ComplianceAgent, EventBus, ShiftState]:
    bus = EventBus()
    state = ShiftState(
        "test",
        BasketBoard.from_config(
            [{"id": "b1", "name": "A", "sku": "S1", "price": 100, "stock": 10}]
        ),
        SegmentPlaylist.from_config(
            [{"id": "s1", "path": "/a.mp4", "duration_seconds": 60}]
        ),
    )
    agent = ComplianceAgent(bus, state, Settings(), LLMClient(api_key=None))
    return agent, bus, state


async def _drain() -> None:
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_viewer_asking_for_line_does_not_switch_the_video():
    agent, bus, state = make_agent()
    actions = bus.subscribe("compliance.action", name="actions")
    await agent.start()
    try:
        bus.publish(CommentIn(nickname="ploy", text="ขอไลน์หน่อยค่ะ"))
        await _drain()
        assert actions.queue.qsize() == 0, "ลูกค้าพิมพ์เอง ไม่ควรไปสลับคลิปหนี"
        assert state.compliance.strikes == 0, "strike ต้องนับเฉพาะความผิดของเราเอง"
        # แต่ต้องบันทึกไว้ให้คนเห็น
        assert any(r["code"] == "offplatform_sale" for r in state.compliance.recent)
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_viewer_violation_still_notifies_a_human():
    agent, bus, state = make_agent()
    notes = bus.subscribe("notify", name="notes")
    await agent.start()
    try:
        bus.publish(CommentIn(nickname="x", text="ขอไลน์หน่อยค่ะ"))
        await _drain()
        events = [notes.queue.get_nowait() for _ in range(notes.queue.qsize())]
        assert any(
            isinstance(e, Notification) and e.needs_human for e in events
        ), "ต้องบอกคนให้ไปซ่อนคอมเมนท์เอง"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_our_own_reply_breaking_the_rules_switches_the_video():
    agent, bus, state = make_agent()
    actions = bus.subscribe("compliance.action", name="actions")
    await agent.start()
    try:
        bus.publish(CommentReply(text="แอดไลน์มาคุยกันนะคะ", reply_to_user="u1"))
        await _drain()
        assert actions.queue.qsize() == 1
        action = actions.queue.get_nowait()
        assert isinstance(action, ComplianceAction)
        assert action.action == "switch_segment"
        assert state.compliance.strikes == 1
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_our_reply_with_bank_details_stops_the_stream():
    agent, bus, state = make_agent()
    actions = bus.subscribe("compliance.action", name="actions")
    await agent.start()
    try:
        bus.publish(CommentReply(text="โอนเงินเข้าบัญชีนี้ได้เลยค่ะ", reply_to_user="u1"))
        await _drain()
        action = actions.queue.get_nowait()
        assert action.action == "stop_stream"
        assert state.compliance.paused is True
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_repeated_viewer_violations_are_reported_once_not_a_hundred_times():
    agent, bus, state = make_agent()
    await agent.start()
    try:
        for _ in range(30):
            bus.publish(CommentIn(nickname="x", text="ขอไลน์ค่ะ"))
        await _drain()
        logs = [
            entry
            for entry in state.agent_logs
            if "คอมเมนท์ผู้ชมมีปัญหา" in entry["message"]
        ]
        assert len(logs) == 1, "คอมเมนท์ซ้ำ ๆ ต้องไม่ท่วม log"
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_normal_selling_talk_triggers_nothing():
    agent, bus, state = make_agent()
    actions = bus.subscribe("compliance.action", name="actions")
    await agent.start()
    try:
        bus.publish(CommentIn(nickname="a", text="ส่งกี่วันถึงคะ"))
        bus.publish(CommentReply(text="ส่งภายใน 1-2 วันทำการค่ะ", reply_to_user="a"))
        await _drain()
        assert actions.queue.qsize() == 0
        assert state.compliance.strikes == 0
        assert len(state.compliance.recent) == 0
    finally:
        await agent.stop()
