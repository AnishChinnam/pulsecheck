"""Exercise an actual local HTTP server, including an outage and recovery."""
import json
import urllib.request

BASE = "http://127.0.0.1:8081"


def request(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.load(response)


request("/api/demo/outage", "POST", {"down": False})
for _ in range(2):
    request("/api/checks/run", "POST")
request("/api/demo/outage", "POST", {"down": True})
for _ in range(2):
    request("/api/checks/run", "POST")
incidents = request("/api/incidents")
assert any(i["name"] == "Checkout service" and i["resolved_at"] is None for i in incidents)
request("/api/demo/outage", "POST", {"down": False})
for _ in range(2):
    request("/api/checks/run", "POST")
assert not any(i["resolved_at"] is None for i in request("/api/incidents"))
print("Live outage detection and recovery verified against loopback HTTP services.")
