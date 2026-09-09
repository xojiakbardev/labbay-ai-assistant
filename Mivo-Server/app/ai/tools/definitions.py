"""Tool-call schemas exposed to the LLM — retrieval + escalation only, no
persistence tools (plan §9). create_lead/update_lead are deliberately absent:
lead persistence is deterministic backend logic applied to the orchestrator's
structured final output (Phase 8), never an action the model triggers itself.
"""
from app.ai.provider.base import ToolDefinition

SEARCH_PRODUCTS = ToolDefinition(
    name="search_products",
    description=(
        "Search the business's product catalog for products relevant to what the "
        "customer is asking about. Always use this before answering questions about "
        "product availability, price, or variants — never guess. "
        "The search understands meaning, not just exact words, so pass what the "
        "customer actually described rather than reducing it to one keyword: "
        "'qishga issiq sport kurtka' finds more than 'kurtka'. It also handles "
        "Cyrillic, Latin and misspellings, so pass their wording as they wrote it."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "What the customer wants, in their own words — a description works "
                    "as well as keywords."
                ),
            },
            "color": {"type": "string", "description": "Filter by color, if the customer mentioned one."},
            "size": {"type": "string", "description": "Filter by size, if the customer mentioned one."},
            "price_max": {"type": "number", "description": "Maximum price, if the customer mentioned a budget."},
        },
        "required": ["query"],
    },
)

GET_PRODUCT = ToolDefinition(
    name="get_product",
    description="Get full details for one specific product by its ID.",
    parameters={
        "type": "object",
        "properties": {"product_id": {"type": "string"}},
        "required": ["product_id"],
    },
)

CHECK_PRODUCT_AVAILABILITY = ToolDefinition(
    name="check_product_availability",
    description="Check whether a specific product (optionally a specific variant, e.g. a size) is in stock.",
    parameters={
        "type": "object",
        "properties": {
            "product_id": {"type": "string"},
            "variant_value": {"type": "string", "description": "e.g. 'M', 'black' — optional."},
        },
        "required": ["product_id"],
    },
)

BROWSE_CATALOG = ToolDefinition(
    name="browse_catalog",
    description=(
        "Show what this business stocks when the customer hasn't named a product you can "
        "search for — 'nima bor?', 'sovg'aga nimadir kerak', 'ko'rsating-chi', or when you "
        "want to offer options before they've decided. Always returns the list of categories "
        "this business carries, so you can narrow down with one short question instead of "
        "asking the customer to be more specific. Call it with no arguments first to see "
        "what's available, then again with a category once you know which one they want."
    ),
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Narrow to one category, using a name from a previous browse_catalog result.",
            },
            "price_max": {"type": "number", "description": "Only products at or under this price."},
            "sort_by": {
                "type": "string",
                "enum": ["popular", "newest"],
                "description": (
                    "'popular' = what other customers have asked about most, the right choice "
                    "when they want a recommendation ('nima tavsiya qilasiz?'). Defaults to newest."
                ),
            },
        },
    },
)

GET_SIMILAR_PRODUCTS = ToolDefinition(
    name="get_similar_products",
    description=(
        "Alternatives to a product you've already shown them — same kind of item, comparable "
        "price. Use it for 'boshqasi bormi?', 'shunga o'xshash', and especially when they say "
        "it's too expensive: offering a real cheaper option beats agreeing that it's expensive."
    ),
    parameters={
        "type": "object",
        "properties": {
            "product_id": {
                "type": "string",
                "description": "A real product UUID from an earlier tool result in this message.",
            }
        },
        "required": ["product_id"],
    },
)

GET_ACTIVE_DISCOUNTS = ToolDefinition(
    name="get_active_discounts",
    description=(
        "Get all currently active discounts and promotions for this business. "
        "Always call this before answering customer questions about discounts, "
        "sales, or promo codes — never state or invent a discount."
    ),
    parameters={
        "type": "object",
        "properties": {},
    },
)

REQUEST_HUMAN = ToolDefinition(
    name="request_human",
    description=(
        "Escalate to a human when the customer explicitly asks for a human, or you "
        "don't have the information needed to answer accurately."
    ),
    parameters={
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
    },
)

ALL_TOOLS = [
    SEARCH_PRODUCTS,
    BROWSE_CATALOG,
    GET_SIMILAR_PRODUCTS,
    GET_PRODUCT,
    CHECK_PRODUCT_AVAILABILITY,
    GET_ACTIVE_DISCOUNTS,
    REQUEST_HUMAN,
]

