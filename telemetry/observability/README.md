# telemetry/observability — Observability / telemetry (issue #32, phase 5)

**Owner lane:** `telemetry` (see [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md)).
**Scope of this directory:** the observability/telemetry contract of the
telemetry pillar — full-trace token/latency logging, per-tenant SLOs,
breach detection, usage/chargeback reports and offline dashboards. This is
**not** the audit ledger, usage metering or budget lanes (issues #31/#33/#34);
those live in sibling directories under `telemetry/`.

Everything here is **fully offline**: Python 3 stdlib + PyYAML only, no
network, no external dashboard server, no npm/node. Dashboards are static
HTML/JSON (or a terminal report) generated offline from the telemetry model.

## What this delivers (issue #32 acceptance criteria)

| Criterion | Where |
|-----------|-------|
| Trace every model/gateway/agent-loop call — tokens, latency, model, provider, outcome, tenant, agent, correlation ids end-to-end | `model.py`, `intake.py`, `store.py` |
| Per-tenant SLOs (availability, latency, cost) from parameterized templates + breach detection + burn-rate dashboards | `slo_templates/*.yaml`, `slos.py`, `breach.py`, `dashboard.py` |
| Outcome-not-liveness doctrine — alerts fire on actual outcome metrics, not heartbeat presence | `breach.py` + negative tests |
| Chargeback/usage reports feed billing (see metering issue #33) | `usage.py` (JSON feed) |
| An intake hook contract other pillars call to emit telemetry | `intake.py` (`TelemetrySink` / `TelemetryRecorder`) |

## Module layout

| File | Purpose |
|------|---------|
| `model.py` | `SpanRecord` / `Trace` value objects + the outcome/service/kind vocabulary |
| `intake.py` | **Emit contract**: `TelemetrySink`, `MemorySink`/`NoopSink`, `TelemetryRecorder` (`trace()`/`span()`/`emit()`/`ingest_gateway_call()`) with contextvar correlation propagation |
| `store.py` | `JsonlSpanSink` (offline append-only writer) + `TraceStore` (reader/query/trace rebuild) |
| `slos.py` | `SloDefinition` / `SloTemplate` / `SloEvaluator` — per-tenant SLO definitions + honest evaluation |
| `slo_templates/` | Parameterized offline SLO templates (availability / latency / cost) |
| `breach.py` | `BreachDetector` / `Alert` / `AlertPolicy` — outcome-not-liveness breach detection |
| `usage.py` | Per-tenant usage / chargeback aggregation (the billing feed) |
| `dashboard.py` | Static HTML + JSON data + terminal health report |
| `cli.py` | Offline operator CLI (`python3 -m telemetry.observability.cli …`) |
| `tests/` | 115 pytest tests incl. negatives (see below) |

## Vocabulary (consumed, never redefined)

- **Outcomes** are the gateway's closed dispatch-outcome set
  (`gateway/proxy/contract.py`, issue #16): `success`, `cache_hit`, `blocked`,
  `rate_limited`, `refused`, `cannot_assess`, `no_healthy_route`, `failed`,
  `denied`. `SERVED_OUTCOMES = {success, cache_hit}` is what counts as a
  served request.
- **Availability sample**: completed serve attempts only — span kinds
  `model_call`/`step` with a served or failure outcome. Trace roots and
  guardrail decisions are **not** serve attempts (a pre-flight guard deny must
  not move the availability ratio).
- **Tiers** use the registry `LOW|MED|HIGH|MAX` vocabulary (issue #9).
- **Record keys** are camelCase JSON, matching the `GatewayCallRecord` JSONL
  shape (`gateway/proxy/model.py`) — the phase-5 telemetry pillar is the
  gateway's stated audit-record consumer. One JSON object per line, plus a
  `_schemaVersion` envelope marker.

## The intake hook (how other pillars emit)

Call sites depend on the `TelemetrySink` interface; the composition root is
`TelemetryRecorder`. Correlation ids propagate with `contextvars`, so one
request spans gateway -> guardrails -> engine on a single `trace_id`.

```python
from telemetry.observability.intake import (
    TelemetryRecorder, JsonlSpanSink,
    KIND_GUARD, KIND_MODEL_CALL,
    SERVICE_GATEWAY, SERVICE_GUARDRAILS, SERVICE_ENGINE,
)
from telemetry.observability.model import OUTCOME_DENIED, OUTCOME_SUCCESS

recorder = TelemetryRecorder(JsonlSpanSink("spans.jsonl"))
with recorder.trace(tenant_id="acme", request_id="req-1",
                    service=SERVICE_ENGINE, name="task.run"):
    recorder.emit(SERVICE_GUARDRAILS, KIND_GUARD, "policy.gate",
                  outcome=OUTCOME_DENIED)
    recorder.emit(SERVICE_GATEWAY, KIND_MODEL_CALL, "model.call",
                  outcome=OUTCOME_SUCCESS, provider="anthropic",
                  model="claude-3-5-sonnet", input_tokens=800,
                  output_tokens=200, latency_ms=120.5,
                  estimated_cost_usd=0.002)
```

The gateway proxy's existing JSONL call records can be absorbed verbatim via
`recorder.ingest_gateway_call(record_dict)` — no gateway code changes needed.

## SLO semantics

- **availability** — target ratio of served attempts over completed serve
  attempts in the window. Error budget = `1 - target_ratio`.
- **latency** — the `percentile`-th latency of completed attempts must not
  exceed `target_ms`; the allowance `1 - percentile` bounds slower attempts.
- **cost** — total recorded estimated spend in the window must not exceed
  `budget_usd`.

**Verdicts:** `OK` / `AT_RISK` (>= 50% of the error budget consumed) /
`BREACHED` (target missed) / `NO_DATA`. `NO_DATA` is **never OK**:
- a tenant with **no telemetry at all** in the SLO window is `missed_window`
  (the tenant could be silently down);
- data present but **no serve attempts** (only heartbeats/guard decisions) is
  also `NO_DATA` — heartbeats are not outcomes.

The `slo-eval` and `breach` CLI commands exit non-zero on any
`BREACHED`/`AT_RISK`/`NO_DATA` — a tenant missing its SLO window fails the
gate (this is the outcome-not-liveness doctrine, cannibalized from the
leaderboard `loop-outcome-check.sh`: "HEALTHY IS NOT WORKING").

## Provenance (cannibalized sources)

All sources verified present under the fleet `.research/` tree
(`/home/akushnir/agent-orchestrator/.research/`, gitignored):

- **`leaderboard/scripts/fleet/loop-outcome-check.sh`** — the
  outcome-not-liveness doctrine ("measure actual results, not liveness") and
  the no-false-green exit-code discipline that `breach.py`/the CLI encode.
- **`leaderboard/scripts/fleet/check-fleet-state-fresh.sh`** — freshness /
  silent-tenant detection shape behind the `NO_DATA` handling.
- **`fleet/monitoring-stack/slo-framework/templates/*.yaml`** — the
  parameterized Sloth SLO template shape (availability/latency/cost,
  window + objective) adapted for `slo_templates/`.
- **`fleet/monitoring-stack/slo-framework/calculator/breach_detector.py`** —
  breach-severity mapping (critical/warning thresholds) behind
  `AlertPolicy`.
- **`fleet/monitoring-stack/dashboards/finops-burn-rate.json`** — the
  burn-rate view concept behind `dashboard.py`'s burn-rate section.
- **`CMR/board/epics/EPIC-07-observability.md`** — observability epic framing
  (events to a store, SLOs with validation gates that really fail).
- **`fleet/git-rca-workspace/src/services/structured_logging_service.py`** and
  **`src/services/jaeger_tracing.py`** — correlation-id + span/trace concepts
  behind `intake.py`'s `contextvars` propagation.
- **`fleet/hermes-agents/monitoring/prometheus/rules/application-alerts.yml`**
  — outcome-grounded alerting rule shape (errors/latency, not heartbeats).

## CLI usage

```bash
# from the repo root (telemetry is a PEP-420 namespace package)
python3 -m telemetry.observability.cli seed    --store /tmp/demo.jsonl --tenants acme,nimbus
python3 -m telemetry.observability.cli slo-eval  --store /tmp/demo.jsonl
python3 -m telemetry.observability.cli breach    --store /tmp/demo.jsonl
python3 -m telemetry.observability.cli usage     --store /tmp/demo.jsonl
python3 -m telemetry.observability.cli dashboard --store /tmp/demo.jsonl \
    --html /tmp/dash.html --json /tmp/dash.json
python3 -m telemetry.observability.cli report    --store /tmp/demo.jsonl
python3 -m telemetry.observability.cli trace     --store /tmp/demo.jsonl --tenant acme
```

Exit-code contract: `0` success/all-SLOs-OK/no-alerts, `1` an SLO is
BREACHED/AT_RISK/NO_DATA or an alert fired, `2` usage/store error.

## Tests

115 tests under `tests/` (run from the repo root):

```bash
python3 -m pytest telemetry/observability/tests -q -p no:cacheprovider
```

Highlights of the negative tests (honesty / no-false-green):

- a breached availability/latency/cost SLO evaluates `BREACHED`, never OK;
- an error budget at exactly 50% consumed is `AT_RISK`, not a silent OK;
- a tenant **missing the SLO window** is `NO_DATA` + `missed_window`, alerts
  as `critical`, and `slo-eval`/`breach` exit `1`;
- guardrail denials and trace roots never pollute the availability sample;
- a corrupt store line raises instead of being silently skipped;
- healthy telemetry produces **zero** alerts (no false positives).
