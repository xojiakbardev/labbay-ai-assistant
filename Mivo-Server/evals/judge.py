"""LLM-as-judge for the half of quality no assertion can capture.

The deterministic checks in checks.py catch what is *wrong*. They cannot tell
you whether a reply reads like a person who wants to sell you something, which
is the actual complaint this work started from. That needs a reader.

Two rules keep this honest:
- The judge never sees which version produced the reply, so it can't flatter a
  change. Compare runs by their scores, not by asking it "is this better?".
- It scores what it can see. It is not asked whether prices are correct — the
  deterministic checks own that, and they can't be argued with.
"""
import uuid

from pydantic import BaseModel, Field

from app.ai.provider.base import LLMProvider, LLMProviderError

JUDGE_SYSTEM_PROMPT = """You are evaluating a single reply from a shop's Instagram sales \
assistant. You are a demanding sales manager reading over the assistant's shoulder, not a \
grader looking for reasons to be generous.

You will be given the conversation so far, the reply being judged, and what this particular \
turn was supposed to achieve.

Score each dimension 1-5. Use the whole range: 3 is "acceptable, unremarkable", 5 is "I'd be \
pleased if my best salesperson wrote this", 1 is "this loses the customer".

- naturalness: does it read like a real person typing on Instagram? Short, warm, specific, \
varied. A reply that is grammatically fine but stiff, generic, templated, or over-explained is \
a 2. Boilerplate politeness with no substance is a 1.
- usefulness: does it actually answer what the customer just said, leading with the thing that \
matters most to them? Answering a different question than the one asked, or burying the answer, \
scores low.
- sales_movement: does it move the sale forward — a concrete recommendation, a next step, one \
easy question, a real answer to an objection? A reply that states a fact and stops dead is a 2, \
however accurate. Pushing hard at someone who is backing away is also low: reading the customer \
correctly is part of the score.
- coherence: is it consistent with everything said earlier in this conversation? Contradicting \
an earlier answer, re-asking something already answered, or restarting the conversation as \
though the earlier turns didn't happen all score low. A first turn with no history scores 5 \
unless it ignores the customer's own message.

Do not score factual correctness of prices or stock — that is checked separately and is not \
your job. Judge only what you can see in the writing.

Write `verdict` as one blunt sentence a manager would actually say, and `biggest_weakness` as \
the single change that would most improve this reply. If the reply is genuinely good, say so \
plainly rather than inventing a criticism."""


class TurnJudgement(BaseModel):
    """Reasoning first, then the scores it justifies — same ordering rule as
    TurnAnalysis, and for the same reason."""

    verdict: str = Field(description="One blunt sentence on this reply.")
    biggest_weakness: str = Field(description="The single most valuable change, or 'none'.")
    naturalness: int = Field(ge=1, le=5)
    usefulness: int = Field(ge=1, le=5)
    sales_movement: int = Field(ge=1, le=5)
    coherence: int = Field(ge=1, le=5)

    @property
    def mean(self) -> float:
        return (self.naturalness + self.usefulness + self.sales_movement + self.coherence) / 4


def _render_history(history: list[tuple[str, str]]) -> str:
    if not history:
        return "(this is the first message in the conversation)"
    return "\n".join(
        f"{'Customer' if role == 'customer' else 'Assistant'}: {text}" for role, text in history
    )


async def judge_turn(
    provider: LLMProvider,
    *,
    history: list[tuple[str, str]],
    customer_message: str,
    reply: str,
    focus: str,
    business_id: uuid.UUID | None = None,
) -> TurnJudgement | None:
    """Returns None if the judge itself fails — a broken judge must not be
    reported as a bad score for the assistant."""
    user_content = (
        f"CONVERSATION SO FAR:\n{_render_history(history)}\n\n"
        f"CUSTOMER'S LATEST MESSAGE:\n{customer_message}\n\n"
        f"THE REPLY BEING JUDGED:\n{reply}\n\n"
        f"WHAT THIS TURN WAS SUPPOSED TO ACHIEVE:\n{focus or '(no specific goal — judge it as a general sales reply)'}"
    )
    try:
        return await provider.generate_structured(
            system_prompt=JUDGE_SYSTEM_PROMPT,
            user_content=user_content,
            response_schema=TurnJudgement,
            business_id=business_id,
        )
    except LLMProviderError:
        return None
