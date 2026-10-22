# Explain the project before you put it in front of an interviewer

This project was built with AI assistance. Treat the code as something to understand, test and modify, not as evidence that you can already recreate every part from memory. Describe your actual contribution honestly.

## A concise explanation

"PulseCheck monitors HTTP endpoints concurrently and keeps a persistent record of checks and incidents. It uses FastAPI for the API, HTTPX and asyncio for bounded network requests, and SQLite for transactional state. I can simulate an outage locally, inspect the incident transition, restart the service and show that the history remains."

Do not claim customer adoption, production uptime, business impact or a throughput improvement. None has been measured in production.

## Be able to trace one request

1. `POST /api/checks/run` calls `MonitorService.run_once`.
2. The cycle lock rejects an overlapping run.
3. Enabled targets are loaded from SQLite off the event loop.
4. A semaphore limits concurrent probes; all probes share one HTTPX client.
5. Each attempt has a total deadline and inactivity timeout. Transient failures may retry.
6. The final result is persisted with state and incident changes in one transaction.
7. The dashboard reads the summary and incident endpoints.

## Questions to practice answering

**Why asyncio rather than one thread per URL?** The work is primarily waiting for network I/O. Async tasks let one event loop manage many waiting requests. The semaphore and connection pool prevent unbounded concurrency. SQLite work is synchronous and is moved to worker threads.

**Why two failed checks?** A single transient failure should not immediately become an incident. Consecutive-observation thresholds reduce flapping, at the cost of slower detection. These thresholds count final checks, not retry attempts.

**Why a database transaction?** Without it, a check might be written but the corresponding incident update could fail, leaving contradictory state. The rollback test deliberately makes incident creation fail and verifies that the new sample is also rolled back.

**How is p95 computed?** Sort successful probe latencies and select item `ceil(0.95 * n)`. Failed probes stay in the success-rate denominator but do not enter that latency distribution. This choice should be explained rather than hidden.

**Is the success rate the same as uptime?** No. It is a ratio of sampled checks. If the app is stopped for an hour, that hour has no observations. It does not become 100% uptime or downtime in this metric.

**How would you scale it?** Separate API, scheduling and worker processes. Use a durable task queue or lease to prevent duplicate ownership, a shared database such as PostgreSQL, and retention/aggregation. Those are possible next steps, not implemented capabilities.

**What would be required before public deployment?** Authentication and authorization, stronger outbound network controls, DNS/IP checks, rate limits, secret handling, database backup and a single scheduling authority. The current project is intentionally a loopback demo.

## Make it your own: complete these exercises

- Change the incident threshold to three and add a test proving that two failures do not open an incident.
- Add an endpoint-level response-body assertion, including a test for a valid 200 response with the wrong body.
- Add one test that proves scheduler cancellation closes resources.
- Explain why a transport timeout and a total attempt deadline are different.
- Explain what a partial unique index prevents and why an application-only check would be weaker.
- Add a retention command that prunes old checks without deleting incident history.

Only add completed extensions or newly measured results to the resume. Keep a short development log of changes you personally understand and can reproduce.
