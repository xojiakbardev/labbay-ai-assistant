import app.main as main


def test_health(client) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_health_is_503_when_the_database_is_down(client, monkeypatch) -> None:
    class _DownEngine:
        def connect(self):
            raise ConnectionRefusedError("db down")

    monkeypatch.setattr(main, "engine", _DownEngine())
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "db_unavailable"}
