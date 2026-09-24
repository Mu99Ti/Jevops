# Jevops Query Mode — Hierarchical Drill-Down

Two coexisting approaches in one project. Both use TypeSafe Jev for decisions; nothing in Mode A is removed.

## Mode A — Real-time triage (existing)

```
stream → prefilter → burst rank → per-event fan-out triage → policy → page/digest/resolve
```

Answers: *should a human be interrupted right now?* Latency budget: seconds. See README.

## Mode B — Investigative query (new)

The user asks: *"what's important from 8am to now?"*

```
raw logs in [from, to]
        │
        ▼
1. chunk      chronological groups of ~N lines (default 40, JEVOPS_QUERY_CHUNK_SIZE),
              balanced remainder in the last group; window = first..last line timestamp
        │
        ▼
2. label      ONE Jev request, all chunks in parallel:
              imp_C0…imp_Cn (noul) + best (choice) + anything (noul)
        │
        ▼
3. drill      selected = imp ≥ threshold ∪ {best}
              subdivide selected chunks by count (×4 groups) → repeat label
              until leaf size ≤ N lines or max depth reached
        │
        ▼
4. leaves     top-M important lines with their chunk path + importance
        │
        ▼
5. explain    text model (OpenAI-compatible, Jevium TEXT_MODEL_*)
              gets a numbered timeline + tool read_log_lines(ids)
              → tool-call loop (≤3 rounds) → answer citing line ids
```

### Principles

- **Jev decides, code narrows, LLM explains.** Jev never writes text; the LLM never
  changes which lines are selected — it can only fetch lines by id (tool) and cite them.
- **Fan-out per level:** all chunk labels ride in one `/v1/systemone` request
  (questions evaluate in parallel; cost ≈ tokens of the digests).
- **Bounded work:** chunks are groups of a fixed number of lines (density-aware by
  construction), and depth, subdivision factor, leaf size, and max leaves are config —
  O(budget) Jev calls, never a scan of every line by the model. Top-level chunk count is
  additionally capped (~200) so choice cardinality stays within Jev's 255 limit.
- **Verifiable citations:** cited line ids are validated against the leaf set before
  the answer is returned; unknown ids are dropped.
- **Dependency injection:** every client takes an injectable transport; all logic is
  unit-tested without network, plus live smoke tests.
- **Honest degradation:** a Jev or LLM failure at any level returns a structured error
  for that stage, never a crash of the process (same contract as Mode A's `model_error`).

### Configuration

| Env | Default | Meaning |
|---|---|---|
| `TEXT_MODEL_*` | Jevium values | LLM endpoint (OpenAI-compatible), model, key |
| `JEVOPS_QUERY_CHUNK_SIZE` | 40 | lines per top-level chunk |
| `JEVOPS_QUERY_SUB` | 4 | subdivision factor per level |
| `JEVOPS_QUERY_DEPTH` | 4 | max drill depth |
| `JEVOPS_QUERY_LEAF` | 5 | lines per leaf (stop condition) |
| `JEVOPS_QUERY_IMP` | 0.5 | noul threshold to descend |
| `JEVOPS_QUERY_MAX_LEAVES` | 50 | cap on lines sent to the LLM |

### Entry points

- CLI: `jevops query "what's important from 08:00 to now?" --from 08:00 --json`
- HTTP: `POST /query {"question": "...", "from": "...", "to": "..."}`
- Raw lines are persisted for every ingested event (`log_lines`), independent of the
  Mode A prefilter, so queries always see the full picture including INFO noise.
