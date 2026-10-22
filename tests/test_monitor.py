import asyncio

import httpx
import pytest

from pulsecheck.models import MonitorInput
from pulsecheck.monitor import MonitorService, RunInProgress, Settings
from pulsecheck.store import Store


def make_store(tmp_path, count=1, attempts=2):
    store = Store(tmp_path / "checks.db")
    store.initialize()
    for i in range(count):
        store.add(MonitorInput(name=f"API {i}", url=f"http://localhost/{i}", attempts=attempts, timeout_seconds=.1))
    return store


@pytest.mark.parametrize("first_status,expected_attempts,expected_ok", [(503, 2, True), (429, 2, True), (404, 1, False), (200, 1, True)])
def test_transient_status_retries_but_client_errors_do_not(tmp_path, first_status, expected_attempts, expected_ok):
    async def scenario():
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            return httpx.Response(first_status if calls == 1 else 200)
        store = make_store(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = MonitorService(store, client, Settings(retry_backoff_seconds=0))
            result = (await service.run_once())[0]
        assert calls == expected_attempts
        assert result["ok"] is expected_ok
        assert len(store.history(1)) == 1  # retries are one final observation
        assert store.history(1)[0]["attempts_used"] == expected_attempts
    asyncio.run(scenario())


def test_timeout_becomes_a_failed_sample_and_can_be_retried(tmp_path):
    async def scenario():
        async def handler(request):
            raise httpx.ReadTimeout("test", request=request)
        store = make_store(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = MonitorService(store, client, Settings(retry_backoff_seconds=0))
            await service.run_once()
        row = store.history(1)[0]
        assert row["ok"] == 0 and row["attempts_used"] == 2
        assert row["error"] == "ReadTimeout"
    asyncio.run(scenario())


def test_total_deadline_also_bounds_a_nonresponsive_transport(tmp_path):
    async def scenario():
        async def handler(request):
            await asyncio.Event().wait()
        store = make_store(tmp_path, attempts=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = MonitorService(store, client, Settings())
            await asyncio.wait_for(service.run_once(), timeout=1)
        assert store.history(1)[0]["error"] == "TimeoutError"
    asyncio.run(scenario())


def test_concurrency_is_bounded_and_cycles_cannot_overlap(tmp_path):
    async def scenario():
        active = peak = calls = 0
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            nonlocal active, peak, calls
            calls += 1
            active += 1
            peak = max(peak, active)
            if active == 2:
                entered.set()
            await release.wait()
            active -= 1
            return httpx.Response(200)
        store = make_store(tmp_path, count=6, attempts=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = MonitorService(store, client, Settings(concurrency=2))
            task = asyncio.create_task(service.run_once())
            await asyncio.wait_for(entered.wait(), timeout=1)
            with pytest.raises(RunInProgress):
                await service.run_once()
            release.set()
            await task
        assert peak == 2 and calls == 6
        assert all(len(store.history(i)) == 1 for i in range(1, 7))
    asyncio.run(scenario())


def test_cancellation_does_not_invent_an_outage(tmp_path):
    async def scenario():
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        store = make_store(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = MonitorService(store, client, Settings())
            task = asyncio.create_task(service.run_once())
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert store.history(1) == []
        assert store.incidents() == []
    asyncio.run(scenario())


def test_changed_allowlist_blocks_a_persisted_target_without_network(tmp_path):
    async def scenario():
        def handler(request):
            pytest.fail("Disallowed target must not be requested")
        store = make_store(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = MonitorService(store, client, Settings(allowed_hosts=("api.example.com",)))
            await service.run_once()
        assert store.history(1)[0]["error"] == "TargetNotAllowed"
        assert store.history(1)[0]["attempts_used"] == 0
    asyncio.run(scenario())
