"""The conversations the assistant is evaluated on.

Each scenario is a scripted customer, replayed turn by turn through the real
pipeline against a seeded catalog. They're deliberately the situations that were
going wrong — a vague opener that used to get "be more specific", an objection
that used to get a catalog dump, a photo question two messages after the photo —
so a regression shows up as a scenario that stops passing rather than as a
vague sense that replies got worse.

Keep them small and specific. A scenario that asserts everything asserts nothing.
"""
from dataclasses import dataclass, field


@dataclass
class SeedProduct:
    name: str
    price: float
    category: str
    description: str = ""
    sizes: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    available: bool = True


@dataclass
class Turn:
    """One customer message and what the reply must and must not do."""

    customer: str
    #: Substrings that must appear (case-insensitive) — facts the reply owes them.
    must_mention: tuple[str, ...] = ()
    #: Substrings that must not appear.
    must_not_mention: tuple[str, ...] = ()
    #: The reply is expected to ask something (discovery, or moving the sale on).
    expects_question: bool = False
    #: Real purchase intent has been shown — not asking for a number is a lost lead.
    expects_phone_ask: bool = False
    #: What the judge should weigh most on this turn.
    focus: str = ""


@dataclass
class Scenario:
    key: str
    description: str
    turns: list[Turn]
    products: list[SeedProduct] = field(default_factory=list)
    business_language: str = "uz"


CATALOG = [
    SeedProduct(
        name="Nike Air Max 90",
        price=780000,
        category="Krossovka",
        description="Original charm, kundalik va sport uchun",
        sizes=("41", "42", "44"),
        colors=("Qora", "Oq"),
    ),
    SeedProduct(
        name="Puma Rebound",
        price=520000,
        category="Krossovka",
        description="Arzon va bardoshli kundalik krossovka",
        sizes=("40", "41", "42", "43"),
        colors=("Qora",),
    ),
    SeedProduct(
        name="Nike Tech Fleece",
        price=450000,
        category="Hoodie",
        description="Issiq oversize hoodie",
        sizes=("M", "L", "XL"),
        colors=("Qora", "Kulrang"),
    ),
    SeedProduct(
        name="Charm sumka",
        price=300000,
        category="Sumka",
        description="Kichik qo'l sumkasi",
    ),
]


SCENARIOS = [
    Scenario(
        key="vague_opener",
        description="Customer names no product. Used to get 'be more specific'.",
        products=CATALOG,
        turns=[
            Turn(
                customer="salom",
                expects_question=True,
                focus="A warm opening that invites them to say what they need, without a catalog dump.",
            ),
            Turn(
                customer="bilmadim, sovg'aga nimadir kerak edi",
                expects_question=True,
                focus=(
                    "Must open the catalog itself (categories or concrete options) and narrow with ONE "
                    "easy question. Telling the customer to be more specific is a failure."
                ),
            ),
        ],
    ),
    Scenario(
        key="price_then_objection",
        description="The objection path — 'qimmat ekan' used to trigger a forced search and a product dump.",
        products=CATALOG,
        turns=[
            Turn(
                customer="Nike Air Max bormi? narxi qancha",
                must_mention=("780",),
                focus="States the real price and moves toward a size or a next step.",
            ),
            Turn(
                customer="qimmat ekan",
                focus=(
                    "Answers the objection about value, then offers the genuinely cheaper option "
                    "(Puma Rebound, 520 000). Must not repeat the catalog or just agree."
                ),
            ),
        ],
    ),
    Scenario(
        key="size_not_carried",
        description="An honest miss: 43 doesn't exist in that model, 41/42/44 do.",
        products=CATALOG,
        turns=[
            Turn(
                customer="Nike Air Max 43 razmer bormi?",
                must_mention=("42",),
                focus=(
                    "Says plainly that 43 isn't available and offers the sizes that are. "
                    "Claiming 43 is in stock is the worst possible failure here."
                ),
            ),
        ],
    ),
    Scenario(
        key="discount_pressure",
        description="No active discounts exist. The customer pushes anyway.",
        products=CATALOG,
        turns=[
            Turn(
                customer="chegirma bormi? do'stimga 20 foiz qilib beruvdilaringiz",
                must_not_mention=("20%", "20 foiz"),
                focus=(
                    "Must say honestly there's no active discount. Inventing or 'confirming' a "
                    "percentage under social pressure is a hard failure."
                ),
            ),
        ],
    ),
    Scenario(
        key="slot_memory",
        description="Discovery answers given once must never be asked for again.",
        products=CATALOG,
        turns=[
            Turn(customer="krossovka kerak", expects_question=True),
            Turn(
                customer="sportga, 42 razmer",
                focus="Uses both facts immediately and recommends something concrete.",
            ),
            Turn(
                customer="boshqa rangi bormi?",
                focus=(
                    "Must not ask for the size or the use case again — it was told both. "
                    "Answers about colours for the product in focus."
                ),
            ),
        ],
    ),
    Scenario(
        key="closing_to_phone",
        description="Real purchase intent — the point where not asking for a number loses the lead.",
        products=CATALOG,
        turns=[
            Turn(customer="Puma Rebound 42 razmer bormi?", must_mention=("42",)),
            Turn(
                customer="zo'r, olaman",
                expects_phone_ask=True,
                focus="Confirms and asks for the phone number naturally, without sounding like a form.",
            ),
        ],
    ),
    Scenario(
        key="russian_customer",
        description="Language matching — a Russian message must never get an Uzbek reply.",
        products=CATALOG,
        turns=[
            Turn(
                customer="здравствуйте, есть кроссовки?",
                expects_question=True,
                focus="Entirely in Russian, and leads with a useful question or option.",
            ),
        ],
    ),
    Scenario(
        key="cooled_off",
        description="They back away. Pushing here is what makes customers leave.",
        products=CATALOG,
        turns=[
            Turn(customer="Nike Tech Fleece narxi qancha?", must_mention=("450",)),
            Turn(
                customer="rahmat, o'ylab ko'raman",
                focus=(
                    "Gives them room, stays warm, leaves the door open. Must not re-pitch, "
                    "re-list products, or push for a decision."
                ),
            ),
        ],
    ),
]


def by_key(key: str) -> Scenario:
    for scenario in SCENARIOS:
        if scenario.key == key:
            return scenario
    raise KeyError(f"unknown scenario {key!r}; have: {', '.join(s.key for s in SCENARIOS)}")
