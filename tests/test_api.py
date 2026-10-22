import httpx
import pytest
from fastapi.testclient import TestClient

from pulsecheck.api import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=tmp_path / "app.db", scheduler=False, transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    with TestClient(app) as client:
        yield client


def test_add_check_pause_export_and_health(client):
    assert client.get("/health").json()["status"] == "ok"
    monitor = client.post("/api/monitors", json={"name": "Test API", "url": "http://localhost/api"})
    assert monitor.status_code == 201
    assert client.post("/api/checks/run").json()["results"][0]["state"] == "up"
    summary = client.get("/api/summary").json()["monitors"][0]
    assert summary["success_rate_24h"] == 100
    assert client.get("/api/monitors/1/checks").json()[0]["ok"] == 1
    assert client.put("/api/monitors/1/enabled", json={"enabled": False}).status_code == 200
    assert client.post("/api/checks/run").json()["results"] == []
    exported = client.get("/api/export.csv")
    assert exported.status_code == 200 and "Test API" in exported.text


@pytest.mark.parametrize("payload", [
    {"name": " ", "url": "http://localhost"},
    {"name": "API", "url": "file:///etc/passwd"},
    {"name": "API", "url": "http://user:pass@localhost"},
    {"name": "API", "url": "https://example.com"},
    {"name": "API", "url": "http://localhost", "attempts": 0},
    {"name": "API", "url": "http://localhost", "timeout_seconds": -1},
    {"name": "API", "url": "http://localhost", "expected_status": 999},
    {"name": "API", "url": "http://localhost", "unexpected": True},
])
def test_invalid_configuration_is_rejected(client, payload):
    assert client.post("/api/monitors", json=payload).status_code == 422


def test_duplicate_and_missing_monitor_errors(client):
    payload = {"name": "API", "url": "http://localhost"}
    assert client.post("/api/monitors", json=payload).status_code == 201
    assert client.post("/api/monitors", json=payload).status_code == 409
    assert client.get("/api/monitors/99/checks").status_code == 404
    assert client.put("/api/monitors/99/enabled", json={"enabled": True}).status_code == 404
    assert client.get("/api/monitors/1/checks?limit=9999").status_code == 422


def test_csv_formula_injection_is_escaped(client):
    client.post("/api/monitors", json={"name": "=1+1", "url": "http://localhost"})
    assert "'=1+1" in client.get("/api/export.csv").text


def test_demo_controls_are_absent_by_default(client):
    assert client.post("/api/demo/outage", json={"down": True}).status_code == 404


def test_demo_control_changes_real_endpoint_response(tmp_path):
    with TestClient(create_app(db_path=tmp_path / "demo.db", scheduler=False, demo=True)) as client:
        assert len(client.get("/api/summary").json()["monitors"]) == 3
        assert client.get("/demo/checkout").status_code == 200
        assert client.post("/api/demo/outage", json={"down": True}).json()["down"] is True
        assert client.get("/demo/checkout").status_code == 503


def test_redirects_are_not_followed_to_other_hosts(tmp_path):
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.com/private"})
    with TestClient(create_app(db_path=tmp_path / "redirect.db", scheduler=False, transport=httpx.MockTransport(handler))) as client:
        client.post("/api/monitors", json={"name": "Redirect", "url": "http://localhost"})
        assert client.post("/api/checks/run").json()["results"][0]["ok"] is False
    assert requests == ["http://localhost"]
