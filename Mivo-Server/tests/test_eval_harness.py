"""The eval harness itself (evals/run.py) still runs against the current
models. It has no other test, and once went unrunnable without anyone noticing
(it built a Customer without the timestamps the model requires)."""
import pytest
from sqlalchemy import func, select

from app.ai.orchestrator import ConversationTurnResult
from app.ai.provider.base import LLMProvider
from app.businesses.models import Business
from evals.run import run_scenario
from evals.scenarios import SCENARIOS

pytestmark = pytest.mark.asyncio


class _ScriptedProvider(LLMProvider):
    async def generate_structured(self, **kwargs):
        raise NotImplementedError

    async def run_agentic_turn(self, **kwargs):
        return ConversationTurnResult(
            reply="Assalomu alaykum! Qanday mahsulot qidiryapsiz?",
            lead_status="cold",
            lead_score=5,
            qualification_reason="greeting",
        )


async def test_a_scenario_runs_end_to_end_and_cleans_up(db_session) -> None:
    scenario = SCENARIOS[0]

    result = await run_scenario(db_session, _ScriptedProvider(), scenario, use_judge=False)

    assert result["scenario"] == scenario.key
    assert len(result["turns"]) == len(scenario.turns)
    assert all(turn["reply"] for turn in result["turns"])
    assert await db_session.scalar(select(func.count()).select_from(Business)) == 0
