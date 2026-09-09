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
        "product availability, price, or variants — never guess."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords describing what the customer wants."},
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

ALL_TOOLS = [SEARCH_PRODUCTS, GET_PRODUCT, CHECK_PRODUCT_AVAILABILITY, GET_ACTIVE_DISCOUNTS, REQUEST_HUMAN]

