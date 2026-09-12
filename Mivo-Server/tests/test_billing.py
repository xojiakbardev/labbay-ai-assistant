"""Plans and monthly AI-reply limits (app/billing)."""
import datetime as dt
from types import SimpleNamespace

import pytest

from app.billing import service as billing
from tests.conftest import create_superadmin_and_headers


UTC = dt.timezone.utc
TASHKENT = dt.timezone(dt.timedelta(hours=5))


def test_without_a_plan_start_months_are_calendar_months_in_tashkent() -> None:
    late_utc = dt.datetime(2026, 9, 30, 20, 0, tzinfo=UTC)  # 1 Oct, 01:00 in Tashkent
    assert billing.billing_period(None, late_utc) == (
        dt.datetime(2026, 10, 1, tzinfo=TASHKENT), dt.datetime(2026, 11, 1, tzinfo=TASHKENT),
    )


def test_a_plan_month_runs_from_the_day_it_started() -> None:
    started = dt.datetime(2026, 1, 31, 10, 0, tzinfo=UTC)
    assert billing.billing_period(started, dt.datetime(2026, 2, 15, tzinfo=UTC)) == (
        started, dt.datetime(2026, 2, 28, 10, 0, tzinfo=UTC),  # no 31st in February
    )
    assert billing.billing_period(started, dt.datetime(2026, 3, 31, 9, 0, tzinfo=UTC))[0] == dt.datetime(
        2026, 2, 28, 10, 0, tzinfo=UTC
    )
    assert billing.billing_period(started, dt.datetime(2026, 3, 31, 11, 0, tzinfo=UTC))[0] == dt.datetime(
        2026, 3, 31, 10, 0, tzinfo=UTC
    )


def _usage(used: int, limit: int | None) -> billing.Usage:
    plan = SimpleNamespace(monthly_ai_replies=limit) if limit is not None else None
    start = dt.datetime(2026, 9, 1, tzinfo=UTC)
    return billing.Usage(plan=plan, used=used, period_start=start, period_end=billing.add_months(start, 1))


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


def test_superadmin_edits_plans_and_renews_onto_one(client) -> None:
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
    row = client.post(
        f"/superadmin/businesses/{business_id}/renew",
        json={"plan_id": paid["id"], "months": 1, "payment_amount": 99, "payment_currency": "USD"},
        headers=admin,
    ).json()
    assert (row["plan_name"], row["ai_replies_limit"], row["ai_replies_used"]) == ("Biznes", 5000, 0)
    started = dt.datetime.fromisoformat(row["plan_started_at"])
    assert abs((dt.datetime.now(dt.timezone.utc) - started).total_seconds()) < 60  # starts today

    cleared = client.post(
        f"/superadmin/businesses/{business_id}/renew", json={"plan_id": None, "months": 1}, headers=admin
    ).json()
    assert cleared["plan_name"] is None and cleared["ai_replies_limit"] is None

    # Only one default; editing a limit; unlimited is null.
    client.patch(f"/superadmin/plans/{paid['id']}", json={"is_default": True, "monthly_ai_replies": None}, headers=admin)
    plans = {p["name"]: p for p in client.get("/superadmin/plans", headers=admin).json()}
    assert plans["Biznes"]["is_default"] and not plans["Sinov"]["is_default"]
    assert plans["Biznes"]["monthly_ai_replies"] is None


def test_a_plan_in_use_cannot_be_deleted(client) -> None:
    admin = create_superadmin_and_headers()
    plan = client.post("/superadmin/plans", json={"name": "Pro", "price": 199, "monthly_ai_replies": 12000}, headers=admin).json()
    spare = client.post("/superadmin/plans", json={"name": "Eski", "price": 5}, headers=admin).json()
    client.post(
        "/superadmin/businesses",
        json={"email": "p@test.com", "password": "supersecret1", "business_name": "P", "plan_id": plan["id"], "trial_days": 30},
        headers=admin,
    )
    assert client.delete(f"/superadmin/plans/{plan['id']}", headers=admin).status_code == 409
    assert client.delete(f"/superadmin/plans/{spare['id']}", headers=admin).status_code == 204
    assert [p["name"] for p in client.get("/superadmin/plans", headers=admin).json()] == ["Pro"]


def test_a_renewal_starts_a_fresh_month_today() -> None:
    business = SimpleNamespace(plan_id=None, plan_started_at=dt.datetime(2026, 9, 1, tzinfo=UTC))
    now = dt.datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    billing.start_plan(business, None, months=3, now=now)
    assert billing.billing_period(business.plan_started_at, now)[0] == now  # the count starts over
    assert business.subscription_expires_at == dt.datetime(2026, 12, 20, 14, 30, tzinfo=UTC)


def test_owners_cannot_touch_plans(client) -> None:
    from tests.conftest import create_business_and_headers

    owner = create_business_and_headers("owner@test.com", "Owner Shop")
    assert client.get("/superadmin/plans", headers=owner).status_code == 403
    assert client.post("/superadmin/plans", json={"name": "Free", "price": 0}, headers=owner).status_code == 403
