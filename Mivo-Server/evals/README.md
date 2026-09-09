# Evals — is the assistant actually getting better?

The test suite proves the pipeline works. It cannot tell you whether the AI
sells well, which is a different question and the one that matters. This is that
second measurement.

Two layers, deliberately:

- **Deterministic checks** (`checks.py`) — free, instant, unarguable. Did the
  reply leak its internal plan, invent a price, answer in the wrong language,
  invent a discount, tell the customer to rephrase, re-ask something already
  answered, forget to ask for a phone number at the point of purchase? Where a
  rule is also enforced at runtime, the check calls the *same function the
  runtime uses*, so the two can't quietly drift apart.
- **LLM-as-judge** (`judge.py`) — scores what no assertion can: does this read
  like a person who wants to sell you something. Four dimensions, 1-5:
  naturalness, usefulness, sales_movement, coherence.

## Running

Needs a database and `OPENROUTER_API_KEY`. It creates a throwaway business,
replays the scripted conversations against the real engine, and deletes it
again. **Nothing is sent to Instagram** — replies are generated and scored only.

```bash
python -m evals.run                  # everything
python -m evals.run --no-judge       # deterministic only, costs nothing but tokens for the turns
python -m evals.run --only closing_to_phone price_then_objection
python -m evals.run --json before.json
python -m evals.run metrics          # read-only production numbers
```

Exit code is non-zero if any deterministic check failed, so it drops into CI as-is.

## How to actually use it

Before changing a prompt, a model or the pipeline:

```bash
python -m evals.run --json before.json
```

Change one thing. Then:

```bash
python -m evals.run --json after.json
```

Compare the judge means and the check failures. **One change at a time** — with
two changes in flight you learn nothing about either. And re-run a couple of
times before believing a small difference: the writer pass samples at a nonzero
temperature, so scores move a little on their own.

The judge never sees which version wrote a reply, so it can't be led. Never ask
it "is this better than before" — compare scores from separate runs instead.

## What to watch

`python -m evals.run metrics` reads production. **Phone capture rate** is the
number to watch: it's the closest thing this system has to a conversion rate,
and it's the goal the sales prompt is written around. A rising handoff rate or a
rising flagged-for-review rate both mean the AI is failing more often, in
different ways.

## Adding a scenario

Add it to `scenarios.py` when you find a conversation the AI handles badly —
that is the moment it's cheapest to capture. Keep it short and give it one
`focus`. A scenario that asserts everything asserts nothing.

`must_mention` / `must_not_mention` are for facts, not phrasing. Asserting exact
wording makes the suite fail every time the AI writes something *better*, which
trains you to ignore it.
