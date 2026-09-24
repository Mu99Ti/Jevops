# Jevops

SRE log tool built on **TypeSafe Jev** (typed decision model) with Elasticsearch, Logstash, Kibana, and Grafana integration — two coexisting approaches:

- **Mode A — real-time triage:** streams logs and decides page / digest / resolve (see below).
- **Mode B — investigative query:** answers *"what's important from 8am to now?"* by hierarchically drilling chunks of logs with Jev, then having an LLM explain the exact lines with citations and tool calls (`docs/hierarchical-query.md`).

Jev answers typed questions against a state (`choice` probabilities, `score` levels, `noul` 0–1 likelihoods) with calibrated confidence, ~70–500 ms, $0.042/M input tokens (output free). Jevops turns that into pages, digests, and resolved incidents instead of log-sprawl — or into a cited timeline when you ask a question.

```
                    ┌──────────────┐   HTTP /ingest
app logs ──► Logstash├─► Elasticsearch ──► poll-es ──┐
      (tcp :5000)   └─► Jevops intake ───────────────┤
                                                     ▼
                       prefilter (free) ──► burst rank (1 Jev call / window)
                                                     ▼
                       triage fan-out (severity, category, urgency, needs_human,
                                        novelty, recovery — all parallel)
                                                     ▼
                       policy (confidence floor, cooldown, composite score)
                          ├─► page ──► PagerDuty / Slack / webhook
                          ├─► resolve ──► PagerDuty (dedup_key)
                          └─► digest ──► SQLite + Elasticsearch ◄── Grafana dashboard
```

## Why Jev here

- **Burst ranking:** up to 255 log lines in one request (`choice` over line IDs + `page_worthy` `noul`) — the semantic-find pattern from TypeSafe's docs. One call ranks a whole window; only the top-K get full triage.
- **Speculative fan-out:** all triage questions share one state and run in parallel; extra questions barely cost latency.
- **Calibrated confidence:** pages fire only above your confidence floor — low confidence is a digest, never a page. No text generation, no hallucinated fields: types are enforced server-side.
- **Recovery detection:** a `recovery` `noul` ≥ 0.8 with an open page resolves the incident on the same dedup key.

## Quickstart

```bash
uv sync --extra dev
cp .env.example .env   # add TYPESAFE_API_KEY at minimum
make check             # ruff + mypy + pytest

# demo stack: Elasticsearch + Logstash (tcp :5000) + Grafana (:3000)
make compose-up

# create ES index + Grafana datasource, contact point, dashboard, alert rule
set -a; source .env; set +a
make provision

make serve             # intake on :8080
uv run jevops replay examples/events.jsonl
uv run jevops selfcheck
```

Send logs to Logstash: `nc localhost 5000 < examples/events.jsonl` (one JSON object per line), or `POST /ingest` with `{"events":[...]}`, a bare object, or a list.

## HTTP API

| Endpoint | Description |
|---|---|
| `POST /ingest` | Accepts one event object, a list, or `{"events":[...]}`. Returns decisions. |
| `POST /query` | Hierarchical drill-down query: `{"question", "from", "to"}` → trace, timeline, cited answer. |
| `GET /decisions?limit=50` | Recent decisions joined with events + triage. |
| `GET /healthz` | Liveness. |

## CLI

| Command | Description |
|---|---|
| `jevops serve` | Run the intake server (triage + query). |
| `jevops query "question" --from ISO --to ISO` | Mode B: drill down with Jev, answer with the LLM (JSON on stdout). |
| `jevops poll-es` | Poll `ES_INDEX_SOURCE` (checkpointed via `search_after` + timestamp) and triage. |
| `jevops provision` | Ensure the ES decisions index and all Grafana resources. |
| `jevops replay FILE` | Triage a JSONL file through the full pipeline (great with `JEVOPS_DRY_RUN=true`). |
| `jevops selfcheck` | Connectivity check for TypeSafe, Elasticsearch, Grafana. |

## Mode B — investigative query

```bash
uv run jevops replay examples/query_logs.jsonl          # ingest raw lines into the store
uv run jevops query "what is important from 08:00 to 10:00?" \
  --from 2026-09-24T08:00:00+00:00 --to 2026-09-24T10:00:00+00:00
```

How it works (`docs/hierarchical-query.md`): group the range into ~40-line chunks
(`JEVOPS_QUERY_CHUNK_SIZE`) → one Jev request labels every chunk in parallel → recurse
into the important ones (×4 by count, depth ≤ 4) until leaf chunks of ≤ 5 lines → the
text model receives only that timeline, can fetch raw lines via the `read_log_lines`
tool, and must cite line ids — unknown ids are rejected. On the 584-line demo corpus:
344 lines scanned, 5 Jev calls, 47 leaf chunks, 24 validated citations, all three
embedded incidents identified with correct times, blast radius, and likely causes.

## Page policy

Page iff `severity == critical` **and** `severity_confidence ≥ JEVOPS_PAGE_MIN_CONFIDENCE` **and** `needs_human ≥ JEVOPS_PAGE_MIN_HUMAN` **and** no active cooldown for `(service, category)`.

- `warning` → digest (stored, charted, never pages), `info` → ignored
- `services.toml` overrides thresholds per service (`min_confidence`, `min_human`, `cooldown_seconds`, `page_enabled`)
- composite = `0.4·severity + 0.3·needs_human + 0.2·novelty + 0.1·urgency` (inspectable, tunable)
- `noise.txt`: substrings dropped before any model call (health checks, probes)

## Grafana (provisioned by `jevops provision`)

- Datasource: Elasticsearch → `jevops-decisions*`
- Contact point `jevops-notify` (webhook → your `JEVOPS_WEBHOOK_URL`)
- Folder `Jevops`, dashboard **Jevops SRE Triage** (pages by severity, decisions over time, pages by service, recent pages)
- Alert rule `jevops-critical-pages`: critical pages in the last 5m > 0

Kibana: `docker compose --profile kibana up -d kibana` (raw log exploration; Jevops itself stores decisions as documents in the same cluster).

## Configuration

All via environment (see `.env.example`): TypeSafe (`TYPESAFE_*`), text model for queries (`TEXT_MODEL_*`), intake/policy (`JEVOPS_*`), query budgets (`JEVOPS_QUERY_*`), Elasticsearch (`ES_*`), Grafana (`GRAFANA_*`), notifiers (`SLACK_WEBHOOK_URL`, `PAGERDUTY_ROUTING_KEY`, `JEVOPS_WEBHOOK_URL`).

Pin `TYPESAFE_MODEL=jev-1.13.0` in production (aliases move under you); responses include the model that actually answered.

## Operations notes

- **Cost:** ~500 tokens/event; prefilter + burst ranking keeps spend around $1–3/Mo at 1M events. Output tokens are free.
- **Rate limits:** 250k tok/s, 1200 rpm (dynamic); client retries 429/503/529 with backoff and honors `Retry-After`.
- **Accuracy:** keep event state tight — Jev's accuracy degrades as state grows (64k request / 32k state limits).
- **Not for:** text generation, embeddings, streaming. Jev decides; code routes; the pager only ever fires on calibrated, high-confidence decisions.
