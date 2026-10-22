import httpx
import pytest
from fastapi.testclient import TestClient

from pulsecheck.api import create_app
from pulsecheck.deploy import build_app


@pytest.fixture
def public_client(tmp_path):
    app = create_app(
        db_path=tmp_path / "public.db", demo=True, public_demo=True, scheduler=False,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )
    with TestClient(app) as client:
        yield client


def test_public_demo_keeps_fixed_targets_and_allows_read_only_inspection(public_client):
    summary = public_client.get("/api/summary").json()
    assert summary["public_demo"] is True
    assert len(summary["monitors"]) == 3
    assert public_client.post("/api/monitors", json={"name": "private", "url": "http://127.0.0.1/admin"}).status_code == 403
    assert public_client.put("/api/monitors/1/enabled", json={"enabled": False}).status_code == 403
    assert public_client.get("/api/export.csv").status_code == 200
    assert public_client.get("/health").json()["status"] == "ok"


def test_public_demo_action_limits_are_global_and_separate(public_client):
    assert public_client.post("/api/checks/run").status_code == 200
    limited = public_client.post("/api/checks/run")
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "2"
    assert public_client.post("/api/demo/outage", json={"down": True}).status_code == 200
    assert public_client.post("/api/demo/outage", json={"down": False}).status_code == 429


def test_public_demo_blocks_cross_origin_mutations(public_client):
    assert public_client.post("/api/demo/outage", json={"down": True}, headers={"Origin": "https://other.example"}).status_code == 403
    assert public_client.get("/demo/checkout").status_code == 200
    assert public_client.post("/api/demo/outage", json={"down": True}, headers={"Origin": "http://testserver"}).status_code == 200
    response = public_client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_public_demo_requires_loopback_and_sample_services(tmp_path):
    with pytest.raises(ValueError):
        create_app(db_path=tmp_path / "bad.db", public_demo=True)
    with pytest.raises(ValueError):
        create_app(db_path=tmp_path / "bad.db", demo=True, public_demo=True, base_url="https://example.com")


def test_deployment_uses_platform_port_and_ignores_target_allowlist_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PORT", "10001")
    monkeypatch.setenv("PULSECHECK_DB", str(tmp_path / "deployed.db"))
    monkeypatch.setenv("PULSECHECK_ALLOWED_HOSTS", "example.com")
    with TestClient(build_app()) as client:
        summary = client.get("/api/summary").json()
        assert summary["interval_seconds"] == 30
        assert all(m["url"].startswith("http://127.0.0.1:10001/demo/") for m in summary["monitors"])
    monkeypatch.setenv("PORT", "80")
    with pytest.raises(ValueError):
        build_app()
