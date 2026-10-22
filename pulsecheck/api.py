import asyncio
import csv
import io
import os
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .models import EnabledInput, MonitorInput, OutageInput
from .monitor import MonitorService, RunInProgress, Settings
from .store import Store

STATIC = Path(__file__).parent / "static"


def csv_cell(value):
    text = str(value) if value is not None else ""
    return "'" + text if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text


def create_app(db_path=None, settings=None, *, scheduler=True, demo=False, public_demo=False, base_url="http://127.0.0.1:8081", transport=None):
    if public_demo and (not demo or urlsplit(base_url).hostname != "127.0.0.1"):
        raise ValueError("Public demo requires demo mode and a loopback base URL")
    store = Store(db_path or os.getenv("PULSECHECK_DB", "data/pulsecheck.db"))
    settings = settings or Settings(allowed_hosts=tuple(x.strip() for x in os.getenv("PULSECHECK_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if x.strip()))

    @asynccontextmanager
    async def lifespan(app):
        await asyncio.to_thread(store.initialize)
        if demo and not await asyncio.to_thread(store.monitors):
            for name, route in [("Application API", "healthy"), ("Checkout service", "checkout"), ("Search service", "slow")]:
                await asyncio.to_thread(store.add, MonitorInput(name=name, url=f"{base_url}/demo/{route}"))
        async with httpx.AsyncClient(
            transport=transport, follow_redirects=False, trust_env=False,
            limits=httpx.Limits(max_connections=settings.concurrency, max_keepalive_connections=settings.concurrency),
        ) as client:
            service = MonitorService(store, client, settings)
            app.state.service = service
            task = asyncio.create_task(service.schedule()) if scheduler else None
            async def housekeeping():
                while True:
                    await asyncio.to_thread(store.prune, 7)
                    await asyncio.sleep(3600)
            cleanup = asyncio.create_task(housekeeping()) if public_demo else None
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                if cleanup:
                    cleanup.cancel()
                    with suppress(asyncio.CancelledError):
                        await cleanup

    app = FastAPI(title="PulseCheck", version="0.1.0", lifespan=lifespan)
    app.state.demo_down = False
    app.state.last_public_action = {}
    app.state.store = store
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def demo_guard(request, call_next):
        if public_demo and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "Use the demo controls on this website."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    def demo_action(action):
        if not public_demo:
            return
        now = time.monotonic()
        last = app.state.last_public_action.get(action)
        if last is not None and now - last < 2:
            raise HTTPException(429, "Please wait two seconds between demo actions.", headers={"Retry-After": "2"})
        app.state.last_public_action[action] = now

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    async def health():
        await asyncio.to_thread(store.monitors)
        return {"status": "ok", "last_cycle_at": app.state.service.last_cycle_at, "last_cycle_error": app.state.service.last_error}

    @app.get("/api/summary")
    async def summary():
        result = await asyncio.to_thread(store.summary)
        return {**result, "demo": demo, "public_demo": public_demo, "demo_down": app.state.demo_down if demo else None,
                "interval_seconds": settings.interval_seconds, "failure_threshold": settings.failure_threshold,
                "recovery_threshold": settings.recovery_threshold}

    @app.get("/api/analytics")
    async def analytics(hours: int = Query(24, ge=1, le=168)):
        return await asyncio.to_thread(store.analytics, hours)

    @app.get("/api/monitors/{monitor_id}")
    async def monitor_detail(monitor_id: int):
        monitor = next((m for m in await asyncio.to_thread(store.monitors) if m['id'] == monitor_id), None)
        if monitor is None:
            raise HTTPException(404, "Monitor not found")
        checks, incidents = await asyncio.gather(asyncio.to_thread(store.history, monitor_id, 120), asyncio.to_thread(store.incidents, 500))
        return {"monitor":monitor,"checks":checks,"incidents":[i for i in incidents if i['monitor_id']==monitor_id]}

    @app.post("/api/monitors", status_code=201)
    async def add_monitor(body: MonitorInput):
        if public_demo:
            raise HTTPException(403, "Public demo uses fixed sample services. Run locally to add your own monitors.")
        try:
            app.state.service.validate_target(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        try:
            return await asyncio.to_thread(store.add, body)
        except sqlite3.IntegrityError as error:
            raise HTTPException(409, "This URL is already monitored") from error

    @app.put("/api/monitors/{monitor_id}/enabled")
    async def enabled(monitor_id: int, body: EnabledInput):
        if public_demo:
            raise HTTPException(403, "Monitor configuration is read-only in the public demo.")
        result = await asyncio.to_thread(store.set_enabled, monitor_id, body.enabled)
        if result is None:
            raise HTTPException(404, "Monitor not found")
        return result

    @app.get("/api/monitors/{monitor_id}/checks")
    async def checks(monitor_id: int, limit: int = Query(100, ge=1, le=500)):
        if not any(m["id"] == monitor_id for m in await asyncio.to_thread(store.monitors)):
            raise HTTPException(404, "Monitor not found")
        return await asyncio.to_thread(store.history, monitor_id, limit)

    @app.post("/api/checks/run")
    async def run():
        demo_action("checks")
        try:
            return {"results": await app.state.service.run_once()}
        except RunInProgress as error:
            raise HTTPException(409, "A monitoring cycle is already running") from error

    @app.get("/api/incidents")
    async def incidents():
        return await asyncio.to_thread(store.incidents)

    @app.get("/api/export.csv", response_class=Response)
    async def export():
        report = await asyncio.to_thread(store.summary)
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        columns = ["name", "url", "state", "enabled", "checks_24h", "success_rate_24h", "p95_ms", "last_checked_at"]
        writer.writerow(columns)
        for monitor in report["monitors"]:
            writer.writerow(csv_cell(monitor[key]) for key in columns)
        return Response(buffer.getvalue(), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="pulsecheck-summary.csv"'})

    if demo:
        @app.post("/api/demo/outage")
        async def outage(body: OutageInput):
            demo_action("outage")
            app.state.demo_down = body.down
            return {"down": body.down}

        @app.get("/demo/healthy")
        async def healthy():
            return {"status": "ok"}

        @app.get("/demo/checkout")
        async def checkout():
            return Response(status_code=503 if app.state.demo_down else 200)

        @app.get("/demo/slow")
        async def slow():
            await asyncio.sleep(.08)
            return {"status": "ok"}

    return app
