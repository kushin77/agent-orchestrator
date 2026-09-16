# Control coverage — the enterprise spine

**Issue:** [#874](https://github.com/kushin77/agent-orchestrator/issues/874)
(child of EPIC [#873](https://github.com/kushin77/agent-orchestrator/issues/873))
**Lane:** `docs/` + `scripts/control-coverage.tsv` · **Gate:**
`scripts/check-control-coverage.sh`

This document is the product's **control map for the platform/SaaS spine** — the
nine rules of Part B in [`GOLDEN-RULES.md`](GOLDEN-RULES.md) that make this a
multi-tenant enterprise control plane rather than an agent runner.

It exists because an enterprise does not purchase a claim. A vendor review asks
three questions, and this document answers them per rule: **what implements it,
what enforces it, and can that enforcement fail?**

---

## 1. Why this document exists (the measurement)

Each Part B rule already declares a `**Verify.**` block. What was missing is the
edge from the rule to the control that actually runs. Measured on
`origin/master`, 2026-09-16 — which `scripts/check-*.sh` cites each rule:

| Rule | Subject | Gate check that cited the rule |
|---|---|---|
| AO-GR-12 | control plane never executes | **none** |
| AO-GR-13 | independent auditor | **none** |
| AO-GR-14 | separation of duties | **none** |
| AO-GR-15 | tenant isolation is structural | **none** |
| AO-GR-16 | DLP + prompt-injection on every model interaction | **none** |
| AO-GR-17 | tamper-evident audit ledger | **none** |
| AO-GR-18 | per-tenant budgets, quotas, kill switch | `check-chat-finops.sh` |
| AO-GR-19 | guard honesty: tri-state + negative controls | 12 checks |
| AO-GR-20 | private by default | **none** |

**7 of 9 rules had no control that named them.** The controls themselves largely
exist — the map below binds them — but the traceability from *rule* to *control*
did not, and a control nobody can find is a control a reviewer cannot credit.

Part C already had exactly this machinery for AO-GR-21…27
(`scripts/check-fleet-durability-rules.sh`: every rule carries Rule/Why/Verify **and**
a backing control). This document closes the same loop for the enterprise spine, and
`scripts/check-control-coverage.sh` keeps it closed.

---

## 2. The map

The **machine-readable source of truth** is
[`scripts/control-coverage.tsv`](../scripts/control-coverage.tsv): one row per Part B
rule, `rule → status → modules → controls`. This section renders it; the gate
validates it against the repository, so the two cannot drift.

| Rule | Subject (the spine's own heading) | Implementing module(s) | Enforcing control | Status |
|---|---|---|---|---|
| AO-GR-12 | Control plane never executes | `control-plane/` | `check-control-functions.sh`, `check-control-verbs.sh` | **ENFORCED** |
| AO-GR-13 | Independent auditor | `governance/merge/`, `governance/lifecycle/` | `check-landing.sh` | **PARTIAL** |
| AO-GR-14 | Separation of duties | `governance/merge/`, `identity/rbac/` | `check-authority.sh` | **ENFORCED** |
| AO-GR-15 | Tenant isolation is structural | `telemetry/ledger/`, `identity/edges/` | — | **GAP** |
| AO-GR-16 | DLP + prompt-injection on every model interaction | `guardrails/dlp/`, `guardrails/chat/` | `check-chat-guardrails.sh` | **ENFORCED** |
| AO-GR-17 | Tamper-evident audit ledger | `telemetry/ledger/`, `telemetry/audit/` | `check-audit-read-model.sh` | **ENFORCED** |
| AO-GR-18 | Per-tenant budgets, quotas, kill switch | `gateway/finops/`, `telemetry/chat/` | `check-chat-finops.sh`, `check-metering-parity.sh` | **ENFORCED** |
| AO-GR-19 | Guard honesty: tri-state + negative controls | `guardrails/policy/`, `guardrails/honesty/` | `check-negative-controls.sh`, `check-guardrail-controls.sh`, `check-policy-schema.sh` | **ENFORCED** |
| AO-GR-20 | Private by default | `infra/feature-flags/`, `portal/config/` | `check-feature-flags.py` | **ENFORCED** |

The bindings are not decorative. Each control's **own stated subject** is the rule:

- AO-GR-14 → `check-authority.sh` — *"authority-matrix gate … repo separation,
  scoped admin rights, **separation of duties**, end-to-end closure"*.
- AO-GR-16 → `check-chat-guardrails.sh` — *"chat-turn guardrails: **DLP egress**,
  inbound re-validation and retrieval-**injection defense**"*.
- AO-GR-17 → `check-audit-read-model.sh` — *"a real, read-only, filterable
  projection over the **tamper-evident ledger**"*.
- AO-GR-18 → `check-chat-finops.sh` — *"the control that matters is the
  **refusal**: a turn over budget…"*.
- AO-GR-20 → `check-feature-flags.py` — *"every control-plane surface **ships OFF
  until promoted**"*, and it asserts the registry carries `default_policy: off` in
  lock-step with `infra/terraform/variables.tf`.
- AO-GR-19 → `check-negative-controls.sh` — *"a guard that ships no negative
  control…"*.

`ENFORCED` means a gate control exists **whose stated subject is this rule**.
`PARTIAL` means a control covers part of it and the remainder is recorded below.
`GAP` means no gate enforces it today. Nothing here is inferred from a module
existing.

---

## 3. The recorded gaps

A gap is never silent. Every rule that is not `ENFORCED` is carried, with its
reason, in [`scripts/control-coverage-gaps.tsv`](../scripts/control-coverage-gaps.tsv)
— a **shrink-only** record: `--record` lowers it, and **refuses to raise it**, so a
new gap must be enforced rather than recorded.

- **AO-GR-13 — PARTIAL.** `check-landing.sh` proves the landing driver *refuses*
  without evidence, which is real and is the delivery half of "verify before done".
  It does not make the *auditor independent*: nothing yet proves, on the
  product-facing path, that the party verifying a change is not the party that
  authored it.
- **AO-GR-15 — GAP.** `telemetry/ledger/tests/test_tenant_isolation.py` exists and
  **no gate runs it**. The multi-tenant integrity suite ships inert: measured
  `grep -rn test_tenant_isolation scripts/*.sh scripts/pytest-suites.txt` → empty.
  A test nobody runs is not a control. Tracked as
  [#875](https://github.com/kushin77/agent-orchestrator/issues/875).

---

## 4. How this is enforced

`scripts/check-control-coverage.sh` runs in `make verify` (auto-discovered by
`scripts/discover-checks.sh`) and fails when:

1. a Part B rule has **no row** in the map, or a row exists for a rule Part B does
   not define (the spine defines the scope, never the map);
2. a row's **module path does not exist**;
3. a row names a **control that is not in `scripts/`, or that no gate runs** —
   "discovered by `scripts/discover-checks.sh`, or named in `scripts/verify.sh` /
   the `Makefile`", the same gate-invocation universe
   `check-gate-coverage.sh` uses;
4. a `GAP` row names a control, or a non-`GAP` row names none;
5. `docs/CONTROL-COVERAGE.md` stops naming a Part B rule;
6. a non-enforced rule is **not recorded** in the gap record.

It also proves it can fail: the assertions are one function over a map file, run
twice — against the real map, and against a copy pointing AO-GR-14 at a script
nobody runs, which must be refused.

## 5. Closing a gap

1. enforce the rule — add or wire the control that can fail;
2. flip its status to `ENFORCED` in `scripts/control-coverage.tsv`;
3. re-run `bash scripts/check-control-coverage.sh --record` to **shrink** the
   record;
4. the rule now appears in the map as a control a reviewer can run.

Enforced rules: **7 of 9**. Two gaps are recorded, both with an owner.
