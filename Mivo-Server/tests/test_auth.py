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
