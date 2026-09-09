"""HTTP-level tests for GET /leads and GET /leads/{id} — tenant scoping and multi-lingual summaries."""
import json
import uuid
import datetime as dt
import psycopg

from tests.conftest import _sync_dsn, create_business_and_headers


def _auth_headers(email="leadowner@test.com", name="Lead Biz") -> dict:
    return create_business_and_headers(email, name)


def test_list_leads_empty_initially(client) -> None:
    headers = _auth_headers()
    resp = client.get("/leads", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_lead_not_found_returns_404(client) -> None:
    headers = _auth_headers()
    resp = client.get(f"/leads/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


def test_leads_require_auth(client) -> None:
    resp = client.get("/leads")
    assert resp.status_code == 401


def test_cross_business_lead_access_isolated(client) -> None:
    headers1 = create_business_and_headers("biz1_owner@test.com", "Biz 1")
    headers2 = create_business_and_headers("biz2_owner@test.com", "Biz 2")

    lead1_id = uuid.uuid4()
    now = dt.datetime.now(dt.timezone.utc)

    # Insert a lead for Business 1 via sync psycopg connection
    with psycopg.connect(_sync_dsn()) as conn:
        with conn.cursor() as cur:
            # Find business 1 ID
            cur.execute("SELECT id FROM businesses WHERE name = 'Biz 1'")
            b1_id = cur.fetchone()[0]

            # Insert customer & conversation for Biz 1
            c1_id = uuid.uuid4()
            conv1_id = uuid.uuid4()
            cur.execute(
                "INSERT INTO customers (id, business_id, ig_scoped_id, username, first_seen_at, last_seen_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (c1_id, b1_id, "ig-c1", "cust_biz1", now, now),
            )
            cur.execute(
                "INSERT INTO conversations (id, business_id, customer_id, channel, status, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (conv1_id, b1_id, c1_id, "instagram", "ai_active", now),
            )
            cur.execute(
                "INSERT INTO leads (id, business_id, customer_id, conversation_id, status, score, phone, interested_products, summary, qualification_reason, summaries, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    lead1_id, b1_id, c1_id, conv1_id, "hot", 90, "+998901234567",
                    "[]", "Biz 1 Lead", "Biz 1 Reason",
                    json.dumps({"uz": "Xulosa UZ", "ru": "Сводка RU", "en": "Summary EN"}),
                    now, now,
                ),
            )
            conn.commit()

    # Business 1 lists its lead
    resp1 = client.get("/leads", headers=headers1)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1) == 1
    assert data1[0]["id"] == str(lead1_id)
    assert data1[0]["phone"] == "+998901234567"
    assert data1[0]["summaries"] == {"uz": "Xulosa UZ", "ru": "Сводка RU", "en": "Summary EN"}

    # Business 1 gets its lead by ID
    resp_get1 = client.get(f"/leads/{lead1_id}", headers=headers1)
    assert resp_get1.status_code == 200
    assert resp_get1.json()["id"] == str(lead1_id)

    # Business 2 lists leads -> empty (cannot see Biz 1's lead)
    resp2 = client.get("/leads", headers=headers2)
    assert resp2.status_code == 200
    assert resp2.json() == []

    # Business 2 tries to GET Business 1's lead by ID -> 404
    resp_get2 = client.get(f"/leads/{lead1_id}", headers=headers2)
    assert resp_get2.status_code == 404
