"""The sale-in-progress carried between turns (app/ai/conversation_state.py).

The point of this state is that it is *grounded*: product facts are recorded
from tool results, never from the model's own output, so a price the AI repeats
next turn is one the catalog actually returned. These tests pin that boundary
along with the freshness rules — what survives a turn and what must not.
"""
import uuid

import pytest

from app.ai.conversation_state import (
    MAX_TRACKED_OBJECTIONS,
    MAX_TRACKED_PRODUCTS,
    fact_snapshot,
    render_state_block,
    update_state,
)

PID = "11111111-1111-1111-1111-111111111111"
OTHER_PID = "22222222-2222-2222-2222-222222222222"


def _summary(product_id: str = PID, name: str = "Nike Air Max", price: float = 780000.0, **over):
    summary = {
        "id": product_id,
        "name": name,
        "price": price,
        "currency": "so'm",
        "availability": True,
        "variants": [
            {"value": "42", "availability": True},
            {"value": "43", "availability": False},
            {"value": "44", "availability": True},
        ],
    }
    summary.update(over)
    return summary


def test_fact_snapshot_keeps_only_what_is_in_stock() -> None:
    snapshot = fact_snapshot(_summary())
    assert snapshot["name"] == "Nike Air Max"
    assert snapshot["price"] == 780000.0
    # 43 is out of stock and must not be remembered as available.
    assert snapshot["in_stock"] == ["42", "44"]


def test_state_records_products_and_the_one_in_focus() -> None:
    state = update_state(
        None,
        product_facts={PID: fact_snapshot(_summary())},
        stage="recommendation",
        interested_product_ids=[PID],
    )
    assert state["stage"] == "recommendation"
    assert state["focus_product_id"] == PID

    block = render_state_block(state)
    assert "Nike Air Max" in block
    assert "IN FOCUS" in block
    assert "42, 44" in block
    assert "43" not in block.split("available:")[1]


def test_focus_must_be_a_product_that_was_actually_looked_up() -> None:
    """The model proposes interested_product_ids; anything not backed by a real
    tool result this conversation is dropped rather than trusted."""
    assert update_state(None, interested_product_ids=["Nike Air Max"]).get("focus_product_id") is None
    assert update_state(None, interested_product_ids=[OTHER_PID]).get("focus_product_id") is None

    state = update_state(None, product_facts={PID: fact_snapshot(_summary())}, interested_product_ids=[PID])
    assert state["focus_product_id"] == PID
    # Focus drops away if the product it pointed at is no longer tracked.
    dropped = update_state({"focus_product_id": OTHER_PID, "product_facts": {}})
    assert "focus_product_id" not in dropped


def test_fresh_lookup_overwrites_a_stale_snapshot() -> None:
    state = update_state(None, product_facts={PID: fact_snapshot(_summary(price=780000.0))})
    state = update_state(state, product_facts={PID: fact_snapshot(_summary(price=690000.0))})
    assert state["product_facts"][PID]["price"] == 690000.0


def test_facts_survive_a_turn_that_looked_nothing_up() -> None:
    """A turn spent answering an objection calls no tools — the price quoted
    two messages ago must still be there afterwards."""
    state = update_state(None, product_facts={PID: fact_snapshot(_summary())})
    later = update_state(state, stage="objection", new_objection="qimmat")
    assert later["product_facts"][PID]["price"] == 780000.0


def test_open_question_lives_exactly_one_turn() -> None:
    """By the next turn the customer has answered it, ignored it, or moved on —
    a stale open question makes the AI ask the same thing twice."""
    asked = update_state(None, open_question="qaysi razmer kerakligi")
    assert asked["open_question"] == "qaysi razmer kerakligi"
    assert "You asked them this" in render_state_block(asked)

    answered = update_state(asked, stage="closing")
    assert "open_question" not in answered


def test_objections_accumulate_without_duplicates_and_stay_capped() -> None:
    state = update_state(None, new_objection="qimmat")
    state = update_state(state, new_objection="Qimmat")  # same objection, different case
    assert state["objections"] == ["qimmat"]

    for i in range(MAX_TRACKED_OBJECTIONS + 2):
        state = update_state(state, new_objection=f"e'tiroz-{i}")
    assert len(state["objections"]) == MAX_TRACKED_OBJECTIONS


def test_tracked_products_are_capped_keeping_the_most_recent() -> None:
    state = None
    ids = [str(uuid.uuid4()) for _ in range(MAX_TRACKED_PRODUCTS + 3)]
    for i, pid in enumerate(ids):
        state = update_state(state, product_facts={pid: fact_snapshot(_summary(pid, name=f"P{i}"))})

    assert len(state["product_facts"]) == MAX_TRACKED_PRODUCTS
    assert list(state["product_facts"]) == ids[-MAX_TRACKED_PRODUCTS:]


def test_escalation_forces_the_handoff_stage() -> None:
    state = update_state(None, stage="closing", escalated=True)
    assert state["stage"] == "handoff"


@pytest.mark.parametrize("empty", [None, {}])
def test_empty_state_renders_nothing(empty) -> None:
    """A brand-new conversation must not carry a header with nothing under it."""
    assert render_state_block(empty) == ""


def test_state_block_tells_the_model_to_re_check_before_repeating() -> None:
    """The state is a record of what was said, not a substitute for a tool call —
    stock moves between messages."""
    state = update_state(None, product_facts={PID: fact_snapshot(_summary())})
    assert "re-check with a tool" in render_state_block(state)


def test_update_returns_a_new_dict() -> None:
    """working_state is JSONB — mutating it in place wouldn't mark the attribute
    dirty and the update would silently never be saved."""
    previous = {"stage": "discovery"}
    updated = update_state(previous, stage="closing")
    assert updated is not previous
    assert previous["stage"] == "discovery"
