"""เทสว่าพนักงาน AI คุยกันผ่าน bus ได้จริง และระบบไม่ล้มเมื่อไม่มี API key"""

from __future__ import annotations

import asyncio

import pytest

from aiworker.agents.base import Agent
from aiworker.bus import EventBus
from aiworker.domain.basket import BasketBoard
from aiworker.domain.segments import SegmentPlaylist
from aiworker.events import CommentIn, Event, Notification, Severity, ViolationDetected
from aiworker.settings import Settings
from aiworker.state import ShiftState


def make_state() -> ShiftState:
    return ShiftState(
        "test-channel",
        BasketBoard.from_config(
            [{"id": "b1", "name": "A", "sku": "S1", "price": 100, "stock": 10}]
        ),
        SegmentPlaylist.from_config(
            [{"id": "s1", "path": "/a.mp4", "duration_seconds": 60}]
        ),
    )


# ---------------------------------------------------------------- bus


def test_bus_routes_only_to_matching_patterns():
    bus = EventBus()
    comments = bus.subscribe("comment.*", name="c")
    everything = bus.subscribe("*", name="all")

    bus.publish(CommentIn(text="สวัสดี"))
    bus.publish(ViolationDetected(code="x"))

    assert comments.queue.qsize() == 1
    assert everything.queue.qsize() == 2


def test_bus_drops_oldest_when_a_slow_subscriber_backs_up():
    bus = EventBus(queue_size=3)
    sub = bus.subscribe("*", name="slow")
    for i in range(10):
        bus.publish(CommentIn(text=str(i)))

    assert sub.queue.qsize() == 3
    assert sub.dropped == 7
    # ต้องเหลือของใหม่สุด ไม่ใช่ของเก่าสุด
    assert sub.queue.get_nowait().text == "7"


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    sub = bus.subscribe("*", name="temp")
    bus.unsubscribe(sub)
    bus.publish(CommentIn(text="hi"))
    assert sub.queue.qsize() == 0


def test_history_can_be_filtered_by_topic():
    bus = EventBus()
    bus.publish(CommentIn(text="a"))
    bus.publish(ViolationDetected(code="c1"))
    assert len(bus.history(topic_glob="comment.*")) == 1
    assert bus.history(topic_glob="compliance.*")[0]["code"] == "c1"


def test_event_as_dict_includes_inherited_and_own_fields():
    payload = CommentIn(text="สวัสดี", nickname="ploy").as_dict()
    assert payload["topic"] == "comment.in"
    assert payload["text"] == "สวัสดี"
    assert "ts" in payload and "id" in payload


def test_severity_ranks_are_ordered():
    assert Severity.CRITICAL.rank > Severity.HIGH.rank > Severity.LOW.rank


# ---------------------------------------------------------------- agent


class Collector(Agent):
    name = "collector"
    subscribes = ("comment.in",)

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.seen: list[Event] = []
        self.done = asyncio.Event()

    async def handle(self, event: Event) -> None:
        self.seen.append(event)
        self.done.set()


class Exploder(Agent):
    name = "exploder"
    subscribes = ("comment.in",)

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.calls = 0

    async def handle(self, event: Event) -> None:
        self.calls += 1
        raise RuntimeError("พังโดยตั้งใจ")


@pytest.mark.asyncio
async def test_agent_receives_events_and_logs_to_state():
    bus, state = EventBus(), make_state()
    agent = Collector(bus, state, Settings())
    await agent.start()
    try:
        bus.publish(CommentIn(text="ราคาเท่าไหร่"))
        await asyncio.wait_for(agent.done.wait(), timeout=1)
        assert agent.seen[0].text == "ราคาเท่าไหร่"
        assert any(log["agent"] == "collector" for log in state.agent_logs)
    finally:
        await agent.stop()


@pytest.mark.asyncio
async def test_one_failing_agent_does_not_take_down_the_others():
    bus, state, settings = EventBus(), make_state(), Settings()
    boom = Exploder(bus, state, settings)
    good = Collector(bus, state, settings)
    await boom.start()
    await good.start()
    try:
        bus.publish(CommentIn(text="1"))
        bus.publish(CommentIn(text="2"))
        await asyncio.sleep(0.1)
        assert boom.calls == 2  # พังแล้วยังรับ event ต่อได้
        assert len(good.seen) == 2
    finally:
        await boom.stop()
        await good.stop()


@pytest.mark.asyncio
async def test_agent_emit_fills_in_the_channel_id():
    bus, state = EventBus(), make_state()
    agent = Collector(bus, state, Settings())
    sub = bus.subscribe("notify", name="watch")
    await agent.start()
    try:
        agent.notify("หัวข้อ", "รายละเอียด", Severity.HIGH, needs_human=True)
        event = sub.queue.get_nowait()
        assert isinstance(event, Notification)
        assert event.channel_id == "test-channel"
        assert event.needs_human is True
    finally:
        await agent.stop()


# ---------------------------------------------------------------- LLM ปิดอยู่


@pytest.mark.asyncio
async def test_llm_client_reports_unavailable_without_a_key():
    from aiworker.llm import LLMClient

    client = LLMClient(api_key=None)
    assert client.available is False
    result = await client.ask_json(system="s", user="u", schema={"type": "object"})
    assert result.ok is False
    assert result.error == "llm_disabled"
    assert client.usage_summary()["enabled"] is False
