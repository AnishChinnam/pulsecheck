import asyncio
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .models import MonitorInput, ProbeResult
from .store import Store, utcnow

log = logging.getLogger("pulsecheck")


@dataclass(frozen=True)
class Settings:
    concurrency: int = 5
    interval_seconds: float = 10
    failure_threshold: int = 2
    recovery_threshold: int = 2
    allowed_hosts: tuple[str, ...] = ("127.0.0.1", "localhost")
    retry_backoff_seconds: float = .1

    def __post_init__(self):
        if not 1 <= self.concurrency <= 50 or self.interval_seconds < .1:
            raise ValueError("Invalid concurrency or interval")
        if min(self.failure_threshold, self.recovery_threshold) < 1:
            raise ValueError("Thresholds must be positive")
        if self.retry_backoff_seconds < 0:
            raise ValueError("Backoff cannot be negative")


class RunInProgress(Exception):
    pass


class MonitorService:
    def __init__(self, store: Store, client: httpx.AsyncClient, settings: Settings):
        self.store, self.client, self.settings = store, client, settings
        self._cycle_lock = asyncio.Lock()
        self.last_cycle_at: str | None = None
        self.last_error: str | None = None

    def validate_target(self, monitor: MonitorInput):
        host = urlsplit(monitor.url).hostname.lower()
        if host not in {x.lower() for x in self.settings.allowed_hosts}:
            raise ValueError("Target host is not allowlisted. Configure PULSECHECK_ALLOWED_HOSTS on the server.")

    async def probe(self, monitor: dict) -> ProbeResult:
        # Recheck persisted targets after a configuration change as well as on creation.
        if urlsplit(monitor["url"]).hostname.lower() not in {x.lower() for x in self.settings.allowed_hosts}:
            return ProbeResult(monitor["id"], utcnow(), False, 0, None, 0, "TargetNotAllowed")
        status_code = None
        for attempt in range(1, monitor["attempts"] + 1):
            started = time.perf_counter()
            try:
                # HTTPX's inactivity limits and asyncio's total deadline cover different failure modes.
                async with asyncio.timeout(monitor["timeout_seconds"]):
                    async with self.client.stream("GET", monitor["url"], timeout=monitor["timeout_seconds"]) as response:
                        status_code = response.status_code
                ok = status_code == monitor["expected_status"]
                error = None if ok else f"HTTP {status_code}"
                retry = status_code >= 500 or status_code == 429
            except (httpx.HTTPError, TimeoutError) as exc:
                ok, error, retry, status_code = False, type(exc).__name__, True, None
            latency = round((time.perf_counter() - started) * 1000, 3)
            if ok or not retry or attempt == monitor["attempts"]:
                return ProbeResult(monitor["id"], utcnow(), ok, latency, status_code, attempt, error)
            await asyncio.sleep(self.settings.retry_backoff_seconds * 2 ** (attempt - 1))
        raise AssertionError("A validated monitor always has at least one attempt")

    async def run_once(self) -> list[dict]:
        if self._cycle_lock.locked():
            raise RunInProgress
        async with self._cycle_lock:
            monitors = await asyncio.to_thread(self.store.monitors, True)
            semaphore = asyncio.Semaphore(self.settings.concurrency)

            async def check(monitor):
                async with semaphore:
                    result = await self.probe(monitor)
                transition = await asyncio.to_thread(self.store.record, result, self.settings.failure_threshold, self.settings.recovery_threshold)
                log.info("check monitor=%s ok=%s state=%s attempts=%s", monitor["id"], result.ok, transition["state"], result.attempts_used)
                return transition

            results = await asyncio.gather(*(check(m) for m in monitors), return_exceptions=True)
            errors = [r for r in results if isinstance(r, BaseException)]
            self.last_cycle_at = utcnow()
            self.last_error = type(errors[0]).__name__ if errors else None
            if errors:
                raise RuntimeError("One or more checks could not be persisted") from errors[0]
            return results

    async def schedule(self):
        # Fixed delay prevents overlapping cycles; it is not a precise cron schedule.
        while True:
            await asyncio.sleep(self.settings.interval_seconds)
            try:
                await self.run_once()
            except RunInProgress:
                continue
            except Exception:
                log.exception("Scheduled monitoring cycle failed")
