"""Plans and monthly AI-reply limits (app/billing)."""
import datetime as dt
from types import SimpleNamespace

import pytest

from app.billing import service as billing
from tests.conftest import create_superadmin_and_headers


def test_months_are_counted_in_tashkent_time() -> None:
    late_utc = dt.datetime(2026, 9, 30, 20, 0, tzinfo=dt.timezone.utc)  # 1 Oct, 01:00 in Tashkent
    assert billing.billing_period(late_utc) == (dt.date(2026, 10, 1), dt.date(2026, 11, 1))
    assert billing.billing_period(dt.datetime(2026, 12, 15, tzinfo=dt.timezone.utc)) == (
        dt.date(2026, 12, 1), dt.date(2027, 1, 1),
    )


def _usage(used: int, limit: int | None) -> billing.Usage:
    plan = SimpleNamespace(monthly_ai_replies=limit) if limit is not None else None
    return billing.Usage(plan=plan, used=used, period_start=dt.date(2026, 9, 1), period_end=dt.date(2026, 10, 1))


@pytest.mark.parametrize(
    ("used", "title"),
    [(7, None), (8, "Tarif limiti yaqin 📊"), (9, None), (10, "Tarif limiti tugadi ⚠️"), (11, "AI to'xtadi — tarif limiti ⛔")],
)
def test_the_owner_hears_at_80_100_and_the_stop(monkeypatch, used, title) -> None:
    monkeypatch.setattr(billing._settings, "plan_warn_fraction", 0.8)
    monkeypatch.setattr(billing._settings, "plan_grace_fraction", 0.1)
    alert = billing.crossing_alert(_usage(used, 10))
    assert (alert[0] if alert else None) == title


def test_the_ai_stops_after_the_grace(monkeypatch) -> None:
    monkeypatch.setattr(billing._settings, "plan_grace_fraction", 0.1)
    assert billing.limit_reason(_usage(10, 10)) is None
    assert "tarif limiti tugadi" in billing.limit_reason(_usage(11, 10)).lower()
    assert billing.limit_reason(_usage(10_000, None)) is None  # no plan, no limit
    assert billing.crossing_alert(_usage(10_000, None)) is None


def test_superadmin_edits_plans_and_sets_one_when_extending(client) -> None:
    admin = create_superadmin_and_headers()
    trial = client.post(
        "/superadmin/plans", json={"name": "Sinov", "price": 0, "monthly_ai_replies": 50, "is_default": True},
        headers=admin,
    ).json()
    paid = client.post(
        "/superadmin/plans", json={"name": "Biznes", "price": 99, "currency": "usd", "monthly_ai_replies": 5000},
        headers=admin,
    ).json()
    assert paid["currency"] == "USD" and trial["is_default"]

    # A new business starts on the default plan.
    created = client.post(
        "/superadmin/businesses", json={"email": "shop@test.com", "password": "supersecret1", "business_name": "Shop"},
        headers=admin,
    )
    owner = {"Authorization": f"Bearer {created.json()['access_token']}"}
    usage = client.get("/billing/usage", headers=owner).json()
    assert (usage["plan"]["name"], usage["ai_replies_used"], usage["ai_replies_limit"]) == ("Sinov", 0, 50)

    business_id = client.get("/business", headers=owner).json()["id"]
    expiry = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=30)).isoformat()
    row = client.patch(
        f"/superadmin/businesses/{business_id}/subscription",
        json={"subscription_expires_at": expiry, "payment_amount": 99, "payment_currency": "USD", "plan_id": paid["id"]},
        headers=admin,
    ).json()
    assert (row["plan_name"], row["ai_replies_limit"], row["ai_replies_this_month"]) == ("Biznes", 5000, 0)

    # Extending without plan_id keeps the plan; plan_id null removes it.
    kept = client.patch(
        f"/superadmin/businesses/{business_id}/subscription", json={"subscription_expires_at": expiry}, headers=admin
    ).json()
    assert kept["plan_name"] == "Biznes"
    cleared = client.patch(
        f"/superadmin/businesses/{business_id}/subscription",
        json={"subscription_expires_at": expiry, "plan_id": None}, headers=admin,
    ).json()
    assert cleared["plan_name"] is None and cleared["ai_replies_limit"] is None

    # Only one default; editing a limit; unlimited is null.
    client.patch(f"/superadmin/plans/{paid['id']}", json={"is_default": True, "monthly_ai_replies": None}, headers=admin)
    plans = {p["name"]: p for p in client.get("/superadmin/plans", headers=admin).json()}
    assert plans["Biznes"]["is_default"] and not plans["Sinov"]["is_default"]
    assert plans["Biznes"]["monthly_ai_replies"] is None


def test_owners_cannot_touch_plans(client) -> None:
    from tests.conftest import create_business_and_headers

    owner = create_business_and_headers("owner@test.com", "Owner Shop")
    assert client.get("/superadmin/plans", headers=owner).status_code == 403
    assert client.post("/superadmin/plans", json={"name": "Free", "price": 0}, headers=owner).status_code == 403
