"""Run the evaluation suite against the real pipeline.

    python -m evals.run                     # every scenario
    python -m evals.run --only closing_to_phone price_then_objection
    python -m evals.run --no-judge          # deterministic checks only (free)
    python -m evals.run --json out.json     # machine-readable, for comparing runs
    python -m evals.run metrics             # production quality metrics, read-only

Needs a database and OPENROUTER_API_KEY: this drives the real conversation
engine, the real catalog search and the real model. It creates a throwaway
business, replays the scripted conversations against it, and deletes it again.
Nothing touches Instagram — replies are generated and scored, never sent.
"""
import argparse
import asyncio
import datetime as dt
import json
import sys
import uuid
from types import SimpleNamespace

from sqlalchemy import delete, func, select

import app.core.models_registry  # noqa: F401  (populate Base.metadata)
from app.ai.context.builder import unseen_shared_media
from app.ai.orchestrator import handle_customer_message
from app.ai.provider.factory import get_llm_provider
from app.auth.models import User
from app.businesses.models import Business
from app.conversations.models import Conversation, Message
from app.conversations.service import get_or_create_conversation
from app.core.db import async_session_factory
from app.customers.service import get_or_create_customer
from app.leads.models import Lead
from app.products.models import Product, ProductVariant
from app.products.search_normalize import normalize_for_search
from evals.checks import CheckContext, run_checks, summarise
from evals.judge import judge_turn
from evals.scenarios import SCENARIOS, Scenario, by_key

EVAL_EMAIL_PREFIX = "eval-harness+"


async def _seed_business(db, scenario: Scenario) -> tuple[Business, set[float]]:
    user = User(email=f"{EVAL_EMAIL_PREFIX}{uuid.uuid4()}@mivo.local", password_hash="x")
    db.add(user)
    await db.flush()

    business = Business(
        owner_user_id=user.id,
        name="Mivo Eval Shop",
        description="Instagram orqali krossovka, hoodie va aksessuar sotadigan do'kon",
        tone="Samimiy, qisqa, professional",
        language=scenario.business_language,
        target_customers="18-35 yosh, Toshkent",
        delivery_info="Toshkent bo'ylab 1 kunda, 25 000 so'm",
        payment_info="Naqd yoki karta orqali",
        ai_enabled=True,
        subscription_expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30),
    )
    db.add(business)
    await db.flush()

    prices: set[float] = set()
    for seed in scenario.products:
        product = Product(
            business_id=business.id,
            name=seed.name,
            description=seed.description,
            price=seed.price,
            currency="so'm",
            availability=seed.available,
            attributes={"category": seed.category},
            # crud.py fills this on every write; without it the trigram
            # fallback silently can't match and the eval isn't testing the
            # search the product actually ships with.
            search_normalized=normalize_for_search(f"{seed.name} {seed.description}"),
        )
        db.add(product)
        await db.flush()
        prices.add(float(seed.price))

        for size in seed.sizes:
            db.add(ProductVariant(
                product_id=product.id, variant_type="size", value=size,
                availability=True, attributes={"size": size},
            ))
        for color in seed.colors:
            db.add(ProductVariant(
                product_id=product.id, variant_type="color", value=color,
                availability=True, attributes={"color": color},
            ))
    await db.flush()
    await db.commit()
    return business, prices


async def _cleanup(db, business: Business) -> None:
    owner_id = business.owner_user_id
    await db.execute(delete(Business).where(Business.id == business.id))
    await db.execute(delete(User).where(User.id == owner_id))
    await db.commit()


async def run_scenario(db, provider, scenario: Scenario, *, use_judge: bool) -> dict:
    business, catalog_prices = await _seed_business(db, scenario)
    customer = await get_or_create_customer(db, business.id, f"eval-{uuid.uuid4()}")
    conversation = await get_or_create_conversation(db, business.id, customer.id)
    await db.commit()

    history: list[tuple[str, str]] = []
    turn_results = []

    try:
        for index, turn in enumerate(scenario.turns):
            tool_state: dict = {}
            result = await handle_customer_message(
                db, provider, business, conversation, turn.customer,
                apply_lead_qualification=True, escalation_state_out=tool_state,
                attachment_type=turn.attachment_type,
            )
            await db.commit()
            await db.refresh(conversation)

            reply = result.reply
            ctx = CheckContext(
                customer_message=turn.customer,
                business_language=business.language,
                catalog_prices=catalog_prices,
                active_discounts=tool_state.get("active_discounts", []),
                executed_tools=tool_state.get("executed_tools", []),
                known_slots=(conversation.working_state or {}).get("slots", {}),
                expects_phone_ask=turn.expects_phone_ask,
                unseen_media=unseen_shared_media(SimpleNamespace(
                    attachment_type=turn.attachment_type, message_type=None, content=turn.customer,
                )),
            )
            findings = run_checks(reply, ctx)

            lowered = reply.lower()
            for needle in turn.must_mention:
                if needle.lower() not in lowered:
                    findings.append(_missing(f"must mention {needle!r}"))
            for needle in turn.must_not_mention:
                if needle.lower() in lowered:
                    findings.append(_missing(f"must NOT mention {needle!r}"))
            if turn.expects_question and "?" not in reply:
                findings.append(_missing("expected a question, got none"))

            # The judge reads a transcript: a media message has to say what it
            # was, or a sensible reply to it reads as a reply to nothing.
            shown = turn.customer
            if turn.attachment_type:
                shown = f"[sent an Instagram {turn.attachment_type} the assistant can't see] {turn.customer}".strip()

            judgement = None
            if use_judge:
                judgement = await judge_turn(
                    provider, history=history, customer_message=shown,
                    reply=reply, focus=turn.focus, business_id=business.id,
                )

            history.append(("customer", shown))
            history.append(("assistant", reply))

            turn_results.append({
                "index": index,
                "customer": turn.customer,
                "reply": reply,
                "tools": [t["name"] for t in ctx.executed_tools],
                "stage": (conversation.working_state or {}).get("stage"),
                "slots": ctx.known_slots,
                "lead": {"status": result.lead_status, "score": result.lead_score},
                **summarise(findings),
                "judgement": judgement.model_dump() if judgement else None,
                "judge_mean": round(judgement.mean, 2) if judgement else None,
            })
    finally:
        await _cleanup(db, business)

    return {"scenario": scenario.key, "description": scenario.description, "turns": turn_results}


def _missing(detail: str):
    from evals.checks import Finding

    return Finding("expectation", "error", detail)


def _print_report(results: list[dict], use_judge: bool) -> bool:
    all_passed = True
    scored: list[float] = []

    for scenario in results:
        print(f"\n{'=' * 78}\n{scenario['scenario']}  —  {scenario['description']}\n{'=' * 78}")
        for turn in scenario["turns"]:
            status = "PASS" if turn["passed"] else "FAIL"
            all_passed = all_passed and turn["passed"]
            judge_note = f"  judge {turn['judge_mean']}/5" if turn["judge_mean"] else ""
            print(f"\n[{status}]{judge_note}   tools: {', '.join(turn['tools']) or 'none'}"
                  f"   stage: {turn['stage']}   lead: {turn['lead']['status']}/{turn['lead']['score']}")
            print(f"  customer > {turn['customer']}")
            for line in turn["reply"].splitlines():
                print(f"  reply    > {line}")
            for finding in turn["findings"]:
                mark = "!!" if finding.severity == "error" else " ~"
                print(f"  {mark} {finding.check}: {finding.detail}")
            if turn["judgement"]:
                j = turn["judgement"]
                print(f"  judge: nat {j['naturalness']} | use {j['usefulness']} | "
                      f"sale {j['sales_movement']} | coh {j['coherence']}")
                print(f"         \"{j['verdict']}\"")
                if j["biggest_weakness"].lower() not in ("none", "nothing", ""):
                    print(f"         fix: {j['biggest_weakness']}")
                scored.append(turn["judge_mean"])

    total = sum(len(s["turns"]) for s in results)
    failed = sum(1 for s in results for t in s["turns"] if not t["passed"])
    warned = sum(t["warnings"] for s in results for t in s["turns"])

    print(f"\n{'=' * 78}")
    print(f"{total - failed}/{total} turns passed the deterministic checks ({warned} warnings)")
    if scored:
        print(f"judge mean: {sum(scored) / len(scored):.2f}/5 over {len(scored)} turns")
    elif use_judge:
        print("judge produced no scores (the judge model failed on every turn)")
    print("=" * 78)
    return all_passed


async def show_metrics() -> None:
    """Read-only production quality metrics. What the offline suite can't tell
    you: whether any of this is working on real customers."""
    async with async_session_factory() as db:
        conversations = await db.scalar(select(func.count()).select_from(Conversation)) or 0
        leads = await db.scalar(select(func.count()).select_from(Lead)) or 0
        with_phone = await db.scalar(
            select(func.count()).select_from(Lead).where(Lead.phone.is_not(None))
        ) or 0
        hot = await db.scalar(
            select(func.count()).select_from(Lead).where(Lead.status == "hot")
        ) or 0
        handoffs = await db.scalar(
            select(func.count()).select_from(Conversation).where(
                Conversation.status.in_(("human_needed", "human_active"))
            )
        ) or 0
        ai_messages = await db.scalar(
            select(func.count()).select_from(Message).where(Message.sender_type == "ai")
        ) or 0
        flagged = await db.scalar(
            select(func.count()).select_from(Message).where(Message.flagged_for_review.is_(True))
        ) or 0

        def pct(part: int, whole: int) -> str:
            return f"{(part / whole * 100):.1f}%" if whole else "n/a"

        print("Production quality metrics")
        print("-" * 40)
        print(f"conversations             {conversations}")
        print(f"leads                     {leads}")
        print(f"  phone captured          {with_phone}  ({pct(with_phone, leads)} of leads)")
        print(f"  hot                     {hot}  ({pct(hot, leads)} of leads)")
        print(f"handed to a human         {handoffs}  ({pct(handoffs, conversations)} of conversations)")
        print(f"AI messages sent          {ai_messages}")
        print(f"  flagged for review      {flagged}  ({pct(flagged, ai_messages)})")
        print()
        print("Phone capture rate is the one to watch — it's the closest thing")
        print("to a conversion number this system has.")


async def main_async(args) -> int:
    if args.command == "metrics":
        await show_metrics()
        return 0

    scenarios = [by_key(k) for k in args.only] if args.only else SCENARIOS
    provider = get_llm_provider()

    results = []
    async with async_session_factory() as db:
        for scenario in scenarios:
            print(f"running {scenario.key}...", file=sys.stderr)
            results.append(await run_scenario(db, provider, scenario, use_judge=not args.no_judge))

    passed = _print_report(results, use_judge=not args.no_judge)

    if args.json:
        serialisable = json.loads(json.dumps(results, default=lambda o: o.__dict__))
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(serialisable, fh, ensure_ascii=False, indent=2)
        print(f"\nwrote {args.json}")

    return 0 if passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", nargs="?", default="run", choices=["run", "metrics"])
    parser.add_argument("--only", nargs="+", metavar="KEY", help="run only these scenarios")
    parser.add_argument("--no-judge", action="store_true", help="deterministic checks only (no LLM judge calls)")
    parser.add_argument("--json", metavar="PATH", help="also write results as JSON, to compare runs")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
