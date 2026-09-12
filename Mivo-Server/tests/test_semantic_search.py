"""Semantic product search — what gets embedded, when, and how it's fused.

The lexical tiers can only match words the catalog already contains. This tier
exists so a customer can describe what they want instead of guessing the
catalog's vocabulary, and these tests pin the two properties that make it safe
to have: it never outranks an exact match, and it never breaks a search when the
embedding API isn't there.
"""
import uuid

import pytest

from app.ai.embeddings.base import EmbeddingError
from app.core.config import get_settings
from app.products import search
from app.products.embedding_text import (
    build_embedding_text,
    embedding_hash,
    needs_reembedding,
)

MODEL = "text-embedding-3-large"
DIMS = 1536


@pytest.fixture(autouse=True)
def _reset_embedding_cooldown():
    """A failed embedding call switches the semantic tier off for a minute —
    tests must not inherit that from each other."""
    search._embedding_down_until = 0.0
    search._QUERY_VECTOR_CACHE.clear()
    yield
    search._embedding_down_until = 0.0
    search._QUERY_VECTOR_CACHE.clear()


class _Variant:
    def __init__(self, value):
        self.value = value


class _Product:
    """Light stand-in — none of this needs a database."""

    def __init__(self, name="Nike Air Max", description="Original charm krossovka",
                 attributes=None, sizes=("42", "44"), embedding=None, embedding_hash=None):
        self.id = uuid.uuid4()
        self.name = name
        self.description = description
        self.attributes = {"category": "Krossovka", "season": "winter"} if attributes is None else attributes
        self.variants = [_Variant(v) for v in sizes]
        self.embedding = embedding
        self.embedding_hash = embedding_hash


# --- what gets embedded ----------------------------------------------------


def test_embedding_text_carries_what_customers_actually_describe() -> None:
    """A customer asking for "qishga issiq narsa" names a season and a property,
    not a product. If the vector only held the name there'd be nothing to match."""
    text = build_embedding_text(_Product())

    assert "Nike Air Max" in text
    assert "category: Krossovka" in text
    assert "season: winter" in text
    assert "42, 44" in text


def test_internal_attributes_stay_out_of_the_vector() -> None:
    """ai_instructions and image URLs help no customer's query and only add noise."""
    text = build_embedding_text(
        _Product(attributes={"category": "Sumka", "ai_instructions": "always upsell", "image_url": "http://x"})
    )
    assert "Sumka" in text
    assert "upsell" not in text
    assert "http" not in text


def test_a_product_with_no_text_is_not_embedded() -> None:
    assert build_embedding_text(_Product(name="", description="", attributes={}, sizes=())) == ""


# --- when it is re-embedded ------------------------------------------------


def test_unchanged_products_cost_no_api_call() -> None:
    """Editing a stock count must not trigger an embedding request."""
    text = build_embedding_text(_Product())
    product = _Product(embedding=[0.1] * 4, embedding_hash=embedding_hash(text, MODEL, DIMS))
    assert not needs_reembedding(product, text, MODEL, DIMS)


def test_a_never_embedded_product_needs_embedding() -> None:
    assert needs_reembedding(_Product(), build_embedding_text(_Product()), MODEL, DIMS)


@pytest.mark.parametrize(
    "model,dims",
    [("some-other-model", DIMS), (MODEL, 3072)],
)
def test_changing_model_or_width_invalidates_every_vector(model: str, dims: int) -> None:
    """Vectors from different models don't share a space — comparing them is
    meaningless, so a config change has to force a re-embed."""
    text = build_embedding_text(_Product())
    product = _Product(embedding=[0.1] * 4, embedding_hash=embedding_hash(text, MODEL, DIMS))
    assert needs_reembedding(product, text, model, dims)


def test_editing_product_text_invalidates_its_vector() -> None:
    old_text = build_embedding_text(_Product())
    product = _Product(embedding=[0.1] * 4, embedding_hash=embedding_hash(old_text, MODEL, DIMS))
    new_text = build_embedding_text(_Product(name="Puma Rebound"))
    assert needs_reembedding(product, new_text, MODEL, DIMS)


# --- fusion ----------------------------------------------------------------


def test_products_both_tiers_agree_on_rise_to_the_top() -> None:
    a, b, c, d = (_Product(name=n) for n in "ABCD")
    fused = search._rrf_fuse([([a, b, c], 1.0), ([c, d, a], 0.6)], limit=4)

    assert [p.name for p in fused][0] == "A"  # ranked well by both
    assert sorted(p.name for p in fused) == ["A", "B", "C", "D"]  # nothing lost or duplicated


def test_an_exact_lexical_match_outranks_an_unrelated_semantic_one() -> None:
    """The property that makes the semantic tier safe to add: it contributes
    recall, it does not overrule precision."""
    exact, loose = _Product(name="exact"), _Product(name="loose")
    fused = search._rrf_fuse([([exact], 1.0), ([loose], 0.6)], limit=5)
    assert [p.name for p in fused] == ["exact", "loose"]


def test_fusion_respects_the_limit() -> None:
    products = [_Product(name=str(i)) for i in range(10)]
    assert len(search._rrf_fuse([(products, 1.0), ([], 0.6)], limit=3)) == 3


def test_without_semantic_results_ranking_is_untouched() -> None:
    a, b = _Product(name="A"), _Product(name="B")
    assert [p.name for p in search._rrf_fuse([([a, b], 1.0), ([], 0.6)], limit=5)] == ["A", "B"]


# --- degrading safely ------------------------------------------------------


async def test_search_survives_the_embedding_api_being_down(monkeypatch) -> None:
    """A customer waiting on a reply must not lose it because a third-party
    embedding call failed."""

    class _Broken:
        model = MODEL
        dimensions = DIMS
        calls = 0

        async def embed_query(self, text, timeout=None):
            _Broken.calls += 1
            raise EmbeddingError("API down")

    monkeypatch.setattr(search, "get_embedding_provider", lambda: _Broken())
    for query in ("krossovka", "boshqa so'rov"):
        results = await search._semantic_candidates(
            None, uuid.uuid4(), query=query, price_max=None,
            only_available=True, limit=5, color=None, size=None,
        )
        assert results == []
    # After one failure the tier sits out instead of making every search in
    # the turn wait on the same dead API again.
    assert _Broken.calls == 1


async def test_a_database_error_in_the_semantic_tier_does_not_poison_the_session(db_session, monkeypatch) -> None:
    """Regression: the semantic query failing inside Postgres (e.g. a vector
    width mismatch) left the transaction aborted, so every later statement of
    the turn — the reply, the lead — failed. It runs in a SAVEPOINT now."""
    from sqlalchemy import text

    from app.auth.models import User
    from app.businesses.models import Business

    user = User(email=f"{uuid.uuid4()}@test.com", password_hash="x")
    db_session.add(user)
    await db_session.flush()
    business = Business(owner_user_id=user.id, name="Savepoint Biz")
    db_session.add(business)
    await db_session.flush()

    class _WrongWidthButClaimsRight:
        model = MODEL
        dimensions = DIMS

        async def embed_query(self, text, timeout=None):
            return [0.1] * DIMS

    monkeypatch.setattr(search, "get_embedding_provider", lambda: _WrongWidthButClaimsRight())
    # Force the query itself to fail inside Postgres.
    monkeypatch.setattr(search, "_SEMANTIC_MAX_DISTANCE", "not-a-number")
    assert await search._semantic_candidates(
        db_session, business.id, query="krossovka", price_max=None,
        only_available=True, limit=5, color=None, size=None,
    ) == []
    # The session is still usable.
    assert (await db_session.execute(text("SELECT 1"))).scalar() == 1


async def test_a_vector_of_the_wrong_width_is_never_queried(monkeypatch) -> None:
    class _Wrong:
        model = MODEL
        dimensions = DIMS

        async def embed_query(self, text, timeout=None):
            return [0.1] * 12

    monkeypatch.setattr(search, "get_embedding_provider", lambda: _Wrong())
    assert await search._semantic_candidates(
        None, uuid.uuid4(), query="krossovka", price_max=None,
        only_available=True, limit=5, color=None, size=None,
    ) == []


async def test_no_provider_means_no_semantic_tier(monkeypatch) -> None:
    monkeypatch.setattr(search, "get_embedding_provider", lambda: None)
    assert await search._semantic_candidates(
        None, uuid.uuid4(), query="krossovka", price_max=None,
        only_available=True, limit=5, color=None, size=None,
    ) == []


async def test_an_empty_query_is_never_embedded(monkeypatch) -> None:
    """Embedding whitespace would spend money to retrieve noise."""

    class _NeverCalled:
        model = MODEL
        dimensions = DIMS

        async def embed_query(self, text, timeout=None):
            raise AssertionError("should not have been called")

    monkeypatch.setattr(search, "get_embedding_provider", lambda: _NeverCalled())
    for query in ("", "   ", None):
        assert await search._semantic_candidates(
            None, uuid.uuid4(), query=query, price_max=None,
            only_available=True, limit=5, color=None, size=None,
        ) == []


# --- configuration ---------------------------------------------------------


def test_embedding_width_fits_pgvectors_index_limit() -> None:
    """pgvector cannot build an HNSW index above 2000 dimensions — exceeding it
    silently costs a full table scan on every search."""
    assert get_settings().embedding_dimensions <= 2000


def test_semantic_is_weighted_below_lexical() -> None:
    assert 0 < get_settings().semantic_fusion_weight < 1.0
