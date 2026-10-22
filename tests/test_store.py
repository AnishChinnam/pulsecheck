from datetime import datetime, timedelta, timezone

import pytest

from pulsecheck.models import MonitorInput, ProbeResult
from pulsecheck.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "monitor.db")
    instance.initialize()
    instance.add(MonitorInput(name="API", url="http://localhost/api"))
    return instance


def result(ok, seconds=0, latency=10, monitor_id=1):
    at = datetime(2026, 10, 22, 12, tzinfo=timezone.utc) + timedelta(seconds=seconds)
    return ProbeResult(monitor_id, at.isoformat(timespec="milliseconds"), ok, latency, 200 if ok else 503, 1)


def test_incident_opens_once_and_requires_sustained_recovery(store):
    assert store.record(result(False))["state"] == "degraded"
    assert not store.incidents()
    for i in range(1, 5):
        assert store.record(result(False, i))["state"] == "down"
    assert len(store.incidents()) == 1
    assert store.record(result(True, 6))["state"] == "down"
    assert store.record(result(True, 7))["state"] == "up"
    assert store.incidents()[0]["resolved_at"] == result(True, 7).checked_at
    store.record(result(False, 8))
    store.record(result(False, 9))
    assert len(store.incidents()) == 2


def test_recovery_streak_resets_on_another_failure(store):
    for ok in [False, False, True, False, True]:
        store.record(result(ok))
    assert store.monitors()[0]["state"] == "down"
    assert store.incidents()[0]["resolved_at"] is None


def test_p95_and_success_rate_include_the_correct_population(store):
    for latency in range(1, 21):
        store.record(result(True, latency=latency))
    store.record(result(False, latency=9999))
    summary = store.summary(result(True).checked_at)["monitors"][0]
    assert summary["p95_ms"] == 19
    assert summary["checks_24h"] == 21
    assert summary["success_rate_24h"] == 95.24


def test_window_boundaries_future_samples_and_missing_metrics(store):
    summary = store.summary(result(True).checked_at)["monitors"][0]
    assert summary["success_rate_24h"] is None and summary["p95_ms"] is None
    for seconds in [-86401, -86400, 0, 1]:
        store.record(result(False, seconds))
    summary = store.summary(result(True).checked_at)["monitors"][0]
    assert summary["checks_24h"] == 2
    assert summary["success_rate_24h"] == 0
    assert summary["p95_ms"] is None


def test_restart_preserves_history_and_open_incidents(store):
    store.record(result(False))
    store.record(result(False, 1))
    restarted = Store(store.path)
    restarted.initialize()
    assert len(restarted.history(1)) == 2
    assert restarted.monitors()[0]["state"] == "down"
    assert len(restarted.incidents()) == 1


def test_transaction_rolls_back_when_incident_creation_fails(store):
    store.record(result(False))
    with store.connect() as db:
        db.execute("CREATE TRIGGER reject_incident BEFORE INSERT ON incidents BEGIN SELECT RAISE(ABORT, 'simulated disk failure'); END")
    with pytest.raises(Exception, match="simulated disk failure"):
        store.record(result(False, 1))
    assert len(store.history(1)) == 1
    assert store.monitors()[0]["state"] == "degraded"


def test_pause_retains_history_and_excludes_monitor_from_checks(store):
    store.record(result(True))
    assert store.set_enabled(1, False)["enabled"] == 0
    assert store.monitors(True) == []
    assert len(store.history(1)) == 1
    assert store.set_enabled(99, True) is None
