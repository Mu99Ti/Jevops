import json
import random
from datetime import UTC, datetime, timedelta

random.seed(42)
now = datetime.now(UTC)
events: list[dict] = []
labels: list[dict] = []


def add(message, service, level="ERROR", truth="info", scenario="noise", env="prod", **ctx):
    ts = (now - timedelta(seconds=len(events) * 7)).isoformat()
    e = {"@timestamp": ts, "service": service, "env": env, "level": level, "message": message}
    e.update(ctx)
    events.append(e)
    labels.append({"truth": truth, "scenario": scenario})


def add_ecs(msg_key, level_key, msg, service_key, service, level, truth, scenario, **ctx):
    ts = (now - timedelta(seconds=len(events) * 7)).isoformat()
    e = {"@timestamp": ts, service_key: service, "env": "prod", level_key: level, msg_key: msg}
    e.update(ctx)
    events.append(e)
    labels.append({"truth": truth, "scenario": scenario})


# S1 checkout outage (critical) with recovery tail
for i in range(12):
    add(
        f"payment-gateway timeout after {3000 + i * 700}ms; consecutive_failures={3 + i} circuit_breaker=open",
        "checkout-api",
        "ERROR",
        "critical",
        "checkout-outage",
        error_rate_5m=round(0.3 + i * 0.03, 2),
        p99_latency_ms=6000 + i * 800,
        deploy="v2.3.1 rolled out 11m ago",
    )
for i in range(8):
    add(
        f"POST /v1/checkout 503 upstream checkout-api unavailable failures_5m={40 + i * 12}",
        "web-frontend",
        "ERROR",
        "critical",
        "checkout-outage",
        status_503_rate=round(0.12 + i * 0.03, 2),
    )
for i in range(5):
    add(
        f"pod checkout-api-7d9f restarted (exit 137) shortly after deploy v2.3.1 restarts_15m={5 + i * 2}",
        "checkout-api",
        "ERROR",
        "critical",
        "checkout-outage",
        restarts_15m=5 + i * 2,
        deploy="v2.3.1",
    )
for i in range(8):
    add(
        f"db pool exhausted: {180 + i * 5}/{200} connections in use waiters={i * 3}",
        "checkout-api",
        "WARN",
        "critical",
        "checkout-outage",
        db_connections=180 + i * 5,
        pool_max=200,
    )
add_ecs(
    "msg",
    "log.level",
    "all checkout errors cleared, pool back to 42/200, error rate 0",
    "app",
    "checkout-api",
    "INFO",
    "critical",
    "checkout-outage-recovery",
)
add_ecs(
    "msg",
    "log.level",
    "health probe green again after circuit breaker closed",
    "app",
    "checkout-api",
    "INFO",
    "critical",
    "checkout-outage-recovery",
)

# S2 search capacity (critical)
for i in range(8):
    add(
        f"GC pause {220 + i * 60}ms heap_used={78 + i * 3}% heap_max=4096MB old_gen growing",
        "search-api",
        "WARN",
        "critical",
        "search-capacity",
        heap_used_pct=78 + i * 3,
    )
for i in range(6):
    add(
        f"cache eviction storm: {900 + i * 300} evictions in 60s hit_rate={0.21 - i * 0.03:.2f}",
        "search-api",
        "ERROR",
        "critical",
        "search-capacity",
        evictions_60s=900 + i * 300,
    )
add(
    "disk usage on /var/lib/search at 91% (inode 74%) write_latency_ms=48",
    "search-api",
    "ERROR",
    "critical",
    "search-capacity",
    disk_pct=91,
)
add("slow query took 4200ms over threshold 1000ms", "search-api", "WARN", "warning", "search-capacity")

# S3 auth security (critical)
for i in range(8):
    add(
        f"failed login burst: {180 + i * 90} x 401 invalid_grant from 10.4.{i}.{17 + i} in 60s",
        "auth-service",
        "ERROR",
        "critical",
        "auth-security",
        auth_failures_60s=180 + i * 90,
    )
add(
    "token signing key rotation failed: kid=key-2026-09 unknown kid returned to clients",
    "auth-service",
    "ERROR",
    "critical",
    "auth-security",
    kid="key-2026-09",
)
add(
    "refresh token reuse detected for session sess_9f2, revoking family",
    "auth-service",
    "WARN",
    "critical",
    "auth-security",
)

# S4 worker queue (warning, OOM duplicates)
for i in range(16):
    add(
        f"retry storm: task task-{i:04d} failed {5 + i} times, backing off exponentially",
        "worker-queue",
        "WARN",
        "warning",
        "worker-queue",
        retries=5 + i,
    )
add(
    "DLQ depth 1842 and growing, oldest message 6m old",
    "worker-queue",
    "WARN",
    "warning",
    "worker-queue",
    dlq_depth=1842,
)
for _ in range(12):
    add(
        "OOMKilled container worker-7d9f memory limit exceeded restart_count=7 restarts_15m=6",
        "worker-queue",
        "ERROR",
        "critical",
        "worker-oom",
        trace_id="fixed-oom-trace",
    )

# S5 db-proxy warnings
for i in range(10):
    add(
        f"deadlock detected on relation orders_{i}, transaction rolled back and retried",
        "db-proxy",
        "WARN",
        "warning",
        "db-proxy",
        deadlock_count=i + 1,
    )
for i in range(6):
    add(
        f"replica lag {1200 + i * 900}ms exceeding threshold 1000ms",
        "db-proxy",
        "WARN",
        "warning",
        "db-proxy",
        replica_lag_ms=1200 + i * 900,
    )

# S6 frontend 404s after deploy (warning)
for i in range(10):
    add_ecs(
        "msg",
        "log.level",
        f"GET /assets/main.{i}a91.js 404 after deploy v3.1.0",
        "service_name",
        "web-frontend",
        "WARN",
        "warning",
        "frontend-deploy",
        deploy="v3.1.0",
    )

# S7 noise
for i in range(10):
    add("health check failed ping target=127.0.0.1:8080", "web-frontend", "ERROR", "info", "noise")
for i in range(8):
    add("kube-proxy iptables sync completed successfully", "web-frontend", "INFO", "info", "noise")
for i in range(8):
    add("liveness probe failed: 2 of 3 checks within 10s", "auth-service", "ERROR", "info", "noise")
for i in range(10):
    add("DEBUG connection pool metrics dumped to /var/log/pool-debug.log", "db-proxy", "DEBUG", "info", "noise")
for i in range(10):
    add("cron job report-generator completed successfully in 1.2s", "worker-queue", "INFO", "info", "noise")

paired = list(zip(events, labels))
random.shuffle(paired)
events = [e for e, _ in paired]
labels = [item for _, item in paired]

with open("examples/mock_logs.jsonl", "w") as f:
    for e in events:
        f.write(json.dumps(e) + "\n")
with open("examples/mock_labels.json", "w") as f:
    json.dump(labels, f, indent=1)
print(
    "events:",
    len(events),
    "critical:",
    sum(1 for item in labels if item["truth"] == "critical"),
    "warning:",
    sum(1 for item in labels if item["truth"] == "warning"),
)
