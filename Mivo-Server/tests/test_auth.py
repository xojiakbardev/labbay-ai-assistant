"""Phase 3 acceptance tests: login/refresh + tenant isolation (plan §13), plus
superadmin-only business onboarding (no public signup — plan addendum)."""
from tests.conftest import create_business_and_headers, create_superadmin_and_headers


def _create_business(client, email="owner@test.com", password="supersecret1", business_name="Test Biz"):
    return create_business_and_headers(email, business_name, password)


def test_superadmin_creates_business(client) -> None:
    admin_headers = create_superadmin_and_headers()
    response = client.post(
        "/superadmin/businesses",
        json={"email": "new-owner@test.com", "password": "supersecret1", "business_name": "New Biz"},
        headers=admin_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert "access_token" in body and "refresh_token" in body

    owner_headers = {"Authorization": f"Bearer {body['access_token']}"}
    me = client.get("/business", headers=owner_headers)
    assert me.status_code == 200
    assert me.json()["name"] == "New Biz"
    assert me.json()["subscription_expires_at"] is not None  # trial window granted


def test_superadmin_endpoints_require_superadmin(client) -> None:
    owner_headers = _create_business(client)
    response = client.post(
        "/superadmin/businesses",
        json={"email": "x@test.com", "password": "supersecret1", "business_name": "X"},
        headers=owner_headers,
    )
    assert response.status_code == 403


def test_superadmin_create_rejects_duplicate_email(client) -> None:
    admin_headers = create_superadmin_and_headers()
    body = {"email": "dupe@test.com", "password": "supersecret1", "business_name": "Dupe"}
    client.post("/superadmin/businesses", json=body, headers=admin_headers)
    response = client.post("/superadmin/businesses", json=body, headers=admin_headers)
    assert response.status_code == 409


def test_login_succeeds_with_correct_password(client) -> None:
    headers = _create_business(client, email="login@test.com", password="correcthorse1")
    user_email = "login@test.com"
    response = client.post("/auth/login", json={"email": user_email, "password": "correcthorse1"})
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert headers  # sanity: helper returned usable headers too


def test_login_rejects_wrong_password(client) -> None:
    _create_business(client, email="login2@test.com", password="correcthorse1")
    response = client.post(
        "/auth/login", json={"email": "login2@test.com", "password": "wrongpassword"}
    )
    assert response.status_code == 401


def test_refresh_issues_new_access_token(client) -> None:
    headers = _create_business(client, email="refresh@test.com")
    login_resp = client.post(
        "/auth/login", json={"email": "refresh@test.com", "password": "supersecret1"}
    )
    refresh_token = login_resp.json()["refresh_token"]
    response = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert headers


def test_login_has_no_alias_backdoors(client) -> None:
    """Regression: "admin"/"test"-style logins resolved to the first
    superadmin / first owner account."""
    create_superadmin_and_headers(email="realadmin@test.com", password="supersecret1")
    _create_business(client, email="realowner@test.com", password="supersecret1")
    for alias in ("admin", "admin@mivo.uz", "admin@gmail.com", "test", "user", "test@gmail.com", "user@mivo.uz"):
        response = client.post("/auth/login", json={"email": alias, "password": "supersecret1"})
        assert response.status_code == 401, alias


def test_login_email_is_matched_exactly_not_as_a_pattern(client) -> None:
    """Regression: ilike() made % and _ wildcards in the submitted email."""
    _create_business(client, email="pattern@test.com", password="supersecret1")
    for pattern in ("%@test.com", "patter_@test.com", "%"):
        assert client.post("/auth/login", json={"email": pattern, "password": "supersecret1"}).status_code == 401
    assert client.post("/auth/login", json={"email": "PATTERN@test.com", "password": "supersecret1"}).status_code == 200


def test_repeated_failed_logins_are_throttled(client) -> None:
    _create_business(client, email="throttle@test.com", password="supersecret1")
    for _ in range(5):
        assert client.post("/auth/login", json={"email": "throttle@test.com", "password": "nope"}).status_code == 401
    blocked = client.post("/auth/login", json={"email": "throttle@test.com", "password": "supersecret1"})
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


def test_refresh_token_rotates_and_reuse_signs_everyone_out(client) -> None:
    _create_business(client, email="rotate@test.com")
    first = client.post("/auth/login", json={"email": "rotate@test.com", "password": "supersecret1"}).json()

    rotated = client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Right after rotation, the old token again is a benign race (two tabs
    # refreshing at once): refused, but the new session survives.
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    third = client.post("/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert third.status_code == 200

    # Past the grace window it means the old token was copied by someone:
    # using it again revokes the whole family.
    import psycopg

    from tests.conftest import _sync_dsn

    with psycopg.connect(_sync_dsn()) as conn:
        conn.execute("UPDATE refresh_tokens SET revoked_at = revoked_at - interval '1 minute' WHERE revoked_at IS NOT NULL")
    assert client.post("/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert client.post("/auth/refresh", json={"refresh_token": third.json()["refresh_token"]}).status_code == 401


def test_logout_revokes_the_refresh_token(client) -> None:
    _create_business(client, email="logout@test.com")
    tokens = client.post("/auth/login", json={"email": "logout@test.com", "password": "supersecret1"}).json()
    assert client.post("/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert client.post("/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401


def test_access_token_cannot_be_used_as_refresh_token(client) -> None:
    _create_business(client, email="types@test.com")
    tokens = client.post("/auth/login", json={"email": "types@test.com", "password": "supersecret1"}).json()
    assert client.post("/auth/refresh", json={"refresh_token": tokens["access_token"]}).status_code == 401


def test_business_endpoint_requires_auth(client) -> None:
    response = client.get("/business")
    assert response.status_code == 401


def test_tenant_isolation_across_two_businesses(client) -> None:
    """User A must never be able to see/modify User B's business — plan §13/§41."""
    a_headers = create_business_and_headers("a@test.com", "Business A")
    b_headers = create_business_and_headers("b@test.com", "Business B")

    a_business = client.get("/business", headers=a_headers).json()
    b_business = client.get("/business", headers=b_headers).json()
    assert a_business["name"] == "Business A"
    assert b_business["name"] == "Business B"
    assert a_business["id"] != b_business["id"]

    # A updates their own business — must not affect B's.
    client.patch("/business", json={"name": "Renamed A"}, headers=a_headers)
    b_business_after = client.get("/business", headers=b_headers).json()
    assert b_business_after["name"] == "Business B"


def test_business_update_patches_ai_settings(client) -> None:
    headers = create_business_and_headers("settings@test.com", "Settings Biz")

    response = client.patch(
        "/business",
        json={
            "tone": "Friendly and concise",
            "selling_approach": "Ask relevant questions, don't be pushy.",
            "rules_text": "Never invent prices.",
        },
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tone"] == "Friendly and concise"
    assert body["rules_text"] == "Never invent prices."


def test_business_update_rejects_null_name_and_unknown_fields(client) -> None:
    headers = create_business_and_headers("strict@test.com", "Strict Biz")
    assert client.patch("/business", json={"name": None}, headers=headers).status_code == 422
    assert client.patch("/business", json={"id": "x", "tone": "a"}, headers=headers).status_code == 422


def test_ui_preferences_are_merged_key_by_key(client) -> None:
    """The dashboard sends one changed key at a time; saving the language must
    not wipe the saved theme."""
    headers = create_business_and_headers("prefs@test.com", "Prefs Biz")
    assert client.patch("/business", json={"ui_preferences": {"theme": "dark"}}, headers=headers).status_code == 200
    body = client.patch("/business", json={"ui_preferences": {"locale": "ru"}}, headers=headers).json()
    assert body["ui_preferences"] == {"theme": "dark", "locale": "ru"}
    too_many = {f"k{i}": "v" for i in range(21)}
    assert client.patch("/business", json={"ui_preferences": too_many}, headers=headers).status_code == 422


def test_unhandled_errors_do_not_leak_details(client) -> None:
    from app.main import app

    @app.get("/__boom_for_test")
    async def boom():
        raise RuntimeError("SELECT secret FROM customers WHERE phone='+998901234567'")

    try:
        from fastapi.testclient import TestClient

        with TestClient(app, raise_server_exceptions=False) as raw:
            resp = raw.get("/__boom_for_test")
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Internal server error."}
    finally:
        app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", None) != "/__boom_for_test"]


def test_cors_does_not_reflect_arbitrary_origins(client) -> None:
    resp = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
    ok = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_insecure_configuration_refuses_to_start(monkeypatch) -> None:
    import pytest
    from pydantic import ValidationError

    import secrets

    from cryptography.fernet import Fernet

    from app.core.config import Settings

    # conftest's test-only Telegram/VAPID values are in the environment and
    # are refused outside APP_ENV=test; production cases get real-looking ones.
    base = dict(
        jwt_secret=secrets.token_urlsafe(40),
        fernet_key=Fernet.generate_key().decode(),
        telegram_webhook_secret=secrets.token_urlsafe(32),
        vapid_private_key=secrets.token_urlsafe(32),
    )
    with pytest.raises(ValidationError):  # the conftest values are public outside APP_ENV=test
        Settings(_env_file=None, **{**base, "app_env": "development",
                                    "jwt_secret": "test-jwt-secret-that-is-long-enough-0123456789"})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**base, "jwt_secret": "change-me-in-prod"})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**base, "fernet_key": "bXgkuUYS8btCnR64znDi0WPkACjDopgX0zvfbjYCvwE="})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**base, "telegram_webhook_secret": "mivo-telegram-webhook-secret-2026"})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**base, "app_env": "production", "debug": True, "meta_app_secret": "s",
                                    "meta_webhook_verify_token": "v"})
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**base, "app_env": "production", "meta_app_secret": "",
                                    "meta_webhook_verify_token": "v"})
    assert Settings(_env_file=None, **{**base, "app_env": "production", "meta_app_secret": "s",
                                       "meta_webhook_verify_token": "v"}).is_production
