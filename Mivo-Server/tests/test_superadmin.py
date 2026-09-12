"""Superadmin panel: onboarding, subscription management, and the usage/cost
stats behind the dashboard (plan addendum — no self-serve signup)."""
import datetime as dt

from tests.conftest import create_business_and_headers, create_superadmin_and_headers


def test_non_superadmin_is_forbidden(client) -> None:
    owner_headers = create_business_and_headers("plain@test.com", "Plain Biz")
    resp = client.get("/superadmin/businesses", headers=owner_headers)
    assert resp.status_code == 403


def test_unauthenticated_is_rejected(client) -> None:
    resp = client.get("/superadmin/businesses")
    assert resp.status_code == 401


def test_create_list_and_renew_business(client, monkeypatch) -> None:
    from app.superadmin import service

    async def fake_balance():
        return 12.5, 20.0

    # The live OpenRouter account API is never called from tests.
    monkeypatch.setattr(service, "get_openrouter_balance", fake_balance)
    admin_headers = create_superadmin_and_headers()

    create_resp = client.post(
        "/superadmin/businesses",
        json={"email": "shop-owner@test.com", "password": "supersecret1", "business_name": "Shop"},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201

    list_resp = client.get("/superadmin/businesses", headers=admin_headers)
    assert list_resp.status_code == 200
    businesses = list_resp.json()
    assert any(b["owner_email"] == "shop-owner@test.com" for b in businesses)
    shop = next(b for b in businesses if b["owner_email"] == "shop-owner@test.com")
    assert shop["subscription_active"] is True  # trial window granted on creation
    assert shop["cost_last_30d_usd"] == 0.0

    renew_resp = client.post(
        f"/superadmin/businesses/{shop['id']}/renew",
        json={"plan_id": None, "months": 2, "payment_amount": 500000, "payment_currency": "uzs", "payment_note": "Naqd"},
        headers=admin_headers,
    )
    assert renew_resp.status_code == 200
    renewed = renew_resp.json()
    assert renewed["subscription_active"] is True
    expires = dt.datetime.fromisoformat(renewed["subscription_expires_at"])
    assert 58 <= (expires - dt.datetime.now(dt.timezone.utc)).days <= 62  # two months from today

    stats_resp = client.get("/superadmin/stats", headers=admin_headers)
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["total_businesses"] >= 1
    assert stats["income_this_month"].get("UZS") == 500000.0
    assert (stats["openrouter_balance_usd"], stats["openrouter_limit_usd"]) == (12.5, 20.0)


def test_superadmin_suspension_cannot_be_undone_by_the_owner(client) -> None:
    admin_headers = create_superadmin_and_headers()
    create_resp = client.post(
        "/superadmin/businesses",
        json={"email": "toggle@test.com", "password": "supersecret1", "business_name": "Toggle Biz"},
        headers=admin_headers,
    )
    owner_headers = {"Authorization": f"Bearer {create_resp.json()['access_token']}"}
    business_id = client.get("/business", headers=owner_headers).json()["id"]

    resp = client.patch(
        f"/superadmin/businesses/{business_id}/ai", json={"ai_suspended": True}, headers=admin_headers
    )
    assert resp.status_code == 200
    assert resp.json()["ai_suspended"] is True

    # The owner's own switch is theirs — but it isn't the kill switch.
    own = client.patch("/business", json={"ai_enabled": True}, headers=owner_headers)
    assert own.status_code == 200
    assert own.json()["ai_suspended"] is True
    # ...and the kill switch itself can't be set by the owner.
    assert client.patch("/business", json={"ai_suspended": False}, headers=owner_headers).status_code == 422


def test_deleted_business_is_refused_immediately_with_a_live_access_token(client) -> None:
    admin_headers = create_superadmin_and_headers()
    create_resp = client.post(
        "/superadmin/businesses",
        json={"email": "livetoken@test.com", "password": "supersecret1", "business_name": "Live Token Biz"},
        headers=admin_headers,
    )
    tokens = create_resp.json()
    owner_headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    business_id = client.get("/business", headers=owner_headers).json()["id"]
    assert client.delete(f"/superadmin/businesses/{business_id}", headers=admin_headers).status_code == 204

    # The owner's account is gone with it.
    assert client.get("/business", headers=owner_headers).status_code == 401
    assert client.patch("/business", json={"ai_enabled": True}, headers=owner_headers).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_delete_business_removes_everything_but_the_payments(client) -> None:
    import psycopg

    from tests.conftest import _sync_dsn

    admin_headers = create_superadmin_and_headers()
    create_resp = client.post(
        "/superadmin/businesses",
        json={"email": "deleteme@test.com", "password": "supersecret1", "business_name": "Delete Me Biz"},
        headers=admin_headers,
    )
    owner = {"Authorization": f"Bearer {create_resp.json()['access_token']}"}
    business_id = client.get("/business", headers=owner).json()["id"]
    assert client.post("/products", json={"name": "Hoodie", "price": 100000}, headers=owner).status_code == 201
    client.post(
        f"/superadmin/businesses/{business_id}/renew",
        json={"plan_id": None, "months": 1, "payment_amount": 99, "payment_currency": "USD"}, headers=admin_headers,
    )

    del_resp = client.delete(f"/superadmin/businesses/{business_id}", headers=admin_headers)
    assert del_resp.status_code == 204

    with psycopg.connect(_sync_dsn()) as conn:
        counts = conn.execute(
            "SELECT (SELECT count(*) FROM businesses), (SELECT count(*) FROM products), "
            "(SELECT count(*) FROM users WHERE email = 'deleteme@test.com'), "
            "(SELECT count(*) FROM payments WHERE business_id IS NULL AND amount = 99)"
        ).fetchone()
    assert counts == (0, 0, 0, 1)  # the platform's income record stays

    # gone from the active list...
    businesses = client.get("/superadmin/businesses", headers=admin_headers).json()
    assert not any(b["id"] == business_id for b in businesses)

    # ...deleting again 404s...
    assert client.delete(f"/superadmin/businesses/{business_id}", headers=admin_headers).status_code == 404

    # ...and the owner can no longer log in.
    login_resp = client.post(
        "/auth/login", json={"email": "deleteme@test.com", "password": "supersecret1"}
    )
    assert login_resp.status_code == 401


def test_delete_business_requires_superadmin(client) -> None:
    owner_headers = create_business_and_headers("notadmin@test.com", "Not Admin Biz")
    business_id = client.get("/business", headers=owner_headers).json()["id"]
    resp = client.delete(f"/superadmin/businesses/{business_id}", headers=owner_headers)
    assert resp.status_code == 403


def test_usage_and_revenue_timeseries_shape(client) -> None:
    admin_headers = create_superadmin_and_headers()
    usage_resp = client.get("/superadmin/usage-timeseries?days=7", headers=admin_headers)
    assert usage_resp.status_code == 200
    assert len(usage_resp.json()) == 7

    revenue_resp = client.get("/superadmin/revenue-timeseries?months=3", headers=admin_headers)
    assert revenue_resp.status_code == 200
    assert len(revenue_resp.json()) == 3
