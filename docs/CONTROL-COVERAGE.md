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

**7 of 9 rules were cited by no check at all** — but that is a *citation* count,
not a coverage count, and reading it as coverage is the mistake this section's own
first draft made. The controls largely exist — the map below binds them — but the
traceability from *rule* to *control* did not, and a control nobody can find is a
control a reviewer cannot credit.

> **A correction worth keeping.** The first draft of this document recorded
> **AO-GR-15** (tenant isolation) as a `GAP`, on the strength of an empty
> `grep -rn test_tenant_isolation scripts/*.sh scripts/pytest-suites.txt`. The grep
> really was empty; the conclusion was still false. `scripts/pytest-suites.txt`
> declares **module directories** (`telemetry/ledger`, `guardrails/isolation`), never
> test filenames, so a grep for a filename could not have matched it. Both suites are
> declared and both are gate-run. **An empty grep for the wrong pattern is not
> evidence of absence.** The remedy was structural rather than cosmetic: a suite
> control is now asserted *against the manifest* instead of searched for by name, and
> the assertion that would have caught it has its own provocation.
>
> The same blind spot produced a second error, in the **other** direction. This
> document argued AO-GR-13 was `PARTIAL` on the reasoning that "nothing proves the
> auditor independent" — without looking for suite controls at all. But
> `governance/merge` **is** a declared suite, and it is the state machine that
> requires a green verify-gate **+ an independent SME reviewer + no self-merge**, with
> the landing seam delegating to it and refusing if it cannot be loaded. The rule was
> better enforced than the first draft allowed; only the **attribution** is missing.
> Both errors came from one habit — searching for a name instead of reading the
> mechanism — which is why the map now records a control's *kind* and the gate
> resolves it, rather than trusting a grep.

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
| AO-GR-13 | Independent auditor | `governance/merge/`, `governance/lifecycle/` | `check-landing.sh`, `suite:governance/merge` | **ENFORCED** |
| AO-GR-14 | Separation of duties | `governance/merge/`, `identity/rbac/` | `check-authority.sh` | **ENFORCED** |
| AO-GR-15 | Tenant isolation is structural | `guardrails/isolation/`, `telemetry/ledger/`, `identity/edges/` | `suite:guardrails/isolation`, `suite:telemetry/ledger` | **ENFORCED** |
| AO-GR-16 | DLP + prompt-injection on every model interaction | `guardrails/dlp/`, `guardrails/chat/` | `check-chat-guardrails.sh` | **ENFORCED** |
| AO-GR-17 | Tamper-evident audit ledger | `telemetry/ledger/`, `telemetry/audit/` | `check-audit-read-model.sh`, `suite:telemetry/ledger` | **ENFORCED** |
| AO-GR-18 | Per-tenant budgets, quotas, kill switch | `gateway/finops/`, `telemetry/chat/` | `check-chat-finops.sh`, `check-metering-parity.sh` | **ENFORCED** |
| AO-GR-19 | Guard honesty: tri-state + negative controls | `guardrails/policy/`, `guardrails/honesty/` | `check-negative-controls.sh`, `check-guardrail-controls.sh`, `check-policy-schema.sh` | **ENFORCED** |
| AO-GR-20 | Private by default | `infra/feature-flags/`, `portal/config/` | `check-feature-flags.py` | **ENFORCED** |

The bindings are not decorative. Each control's **own stated subject** is the rule:

- AO-GR-13 → `check-landing.sh` **and** `suite:governance/merge` — the state
  machine's own promise is *"A reviewer is never the author/executor of the work it
  reviews"*, and the landing seam's is *"no local re-implementation of 'green +
  reviewer + carve-out', and no fallback that decides on its own"*. The landed
  artifact now also names **who verified it**: both attestation writers record
  `verified_by`, and a green, commit-named attestation that names no verifier is
  refused by the landing driver as `attestation-does-not-name-a-verifier`.
- AO-GR-14 → `check-authority.sh` — *"authority-matrix gate … repo separation,
  scoped admin rights, **separation of duties**, end-to-end closure"*.
- AO-GR-16 → `check-chat-guardrails.sh` — *"chat-turn guardrails: **DLP egress**,
  inbound re-validation and retrieval-**injection defense**"*.
- AO-GR-17 → `check-audit-read-model.sh` — *"a real, read-only, filterable
  projection over the **tamper-evident ledger**"*, over the same suite that proves
  the chain: `telemetry/ledger/tests/test_chain_integrity.py`.
- AO-GR-15 → `suite:guardrails/isolation` — its scope-gate test states the rule in
  the rule's own words (*"Cross-tenant access attempts must fail closed"*:
  `test_foreign_only_row_returns_none`, `test_require_foreign_row_raises`,
  `test_put_rejects_denormalized_tenant`, `test_no_unscoped_accessor_exists`), and the
  rule's second `Verify.` clause — *"a known-bad probe fails it"* — is
  `test_leaky_store_produces_probe_failures`, driven by a fixture whose own docstring
  reads *"DELIBERATELY LEAKY store — planted cross-tenant isolation bugs …
  NEGATIVE-test material"*. `suite:telemetry/ledger` covers the per-tenant ledger half
  (`test_tenant_isolation.py`, `test_chain_integrity.py`).
- AO-GR-18 → `check-chat-finops.sh` — *"the control that matters is the
  **refusal**: a turn over budget…"*.
- AO-GR-20 → `check-feature-flags.py` — *"every control-plane surface **ships
  enabled by default**"* (AO-GR-6, policy-gr5-enabled-by-default, 2026-09-21),
  and it asserts the registry carries `default_policy: on` in lock-step with
  `infra/terraform/variables.tf`.
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
new gap must be enforced rather than recorded. At 9 of 9 the record is **empty** —
and the assertion stands: a NEW gap would still be refused by name.

```
$ bash scripts/check-control-coverage.sh
== the control map ==
  OK    every rule has a row, every module exists, every control is invoked by a gate, and every spine Control line matches its row (#873)
== the shrink-only gap record ==
  OK    every non-enforced rule is recorded
  enforced: 9 of 9 rule(s)
== vacuity control: a control nobody runs must be refused ==
  OK    a control script that does not exist is refused
  OK    a control script that exists but NO GATE runs is refused
  OK    a suite the manifest does not declare is refused
  OK    a rule's Control line deleted from the spine is refused
  OK    a Control line naming a control not in the map row is refused
check-control-coverage: OK — … the 0 recorded gap(s) are shrink-only
RC=0
```

- **None.** The record is empty — for the first time every rule of the
  platform/SaaS spine is `ENFORCED`. **AO-GR-13 was the last.** The verification
  artifact now records **who verified it**: `scripts/merge-gate.sh` and
  `scripts/verify.sh` both write `verified_by` (the session identity the isolation
  layer exports, `AO_AGENT_ID`, falling back to the OS user — deliberately never
  the git author, since conflating author and verifier is exactly the
  self-attestation the rule forbids) alongside `verification_session`; the landing
  driver (`governance/landing/evidence.py`) refuses a green, commit-named
  attestation that names **no verifier** — `attestation-does-not-name-a-verifier` —
  and `check-landing.sh` proves that refusal by name, in both directions (a green
  attestation *with* a verifier is granted, so the matcher is not simply refusing).

  What this does **not** claim, stated to the same standard: it does not prove the
  verifier is a *different party* from the author. It makes the verifier
  **visible on the landed artifact**, which is what makes independence checkable at
  all; the independent-reviewer guarantee itself remains the merge model's
  (`suite:governance/merge`).

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
   `check-gate-coverage.sh` uses. A control may also be a **suite**, written
   `suite:<dir>`, which counts only when `scripts/pytest-suites.txt` *declares* that
   directory — the manifest `scripts/run-pytest-suites.sh` executes — and when the
   suite runner is itself shown to be invoked by a gate;
4. a `GAP` row names a control, or a non-`GAP` row names none;
5. `docs/CONTROL-COVERAGE.md` stops naming a Part B rule;
6. a non-enforced rule is **not recorded** in the gap record;
7. **(#873)** a Part B rule's own `**Control.**` line in `docs/GOLDEN-RULES.md` is
   missing, or its `modules:`/`controls:` set does not equal — in **both**
   directions — the same rule's row in this map.

It also proves it can fail. The assertions are one function over a map file, run
against the real map and then against mutated copies — one provocation per
assertion, each required to produce *the specific finding it is testing for*, because
a refusal for the wrong reason would not show that the assertion works:

| provocation | the assertion it exercises |
|---|---|
| a control script that does not exist | the control-exists assertion |
| a real script in `scripts/` that no gate invokes | the control-is-run assertion |
| `suite:docs` — a real directory the manifest does not declare | the suite-declaration assertion |
| a rule's `**Control.**` line deleted from a copy of the spine | the Control-line-exists assertion (#873) |
| a `**Control.**` line naming a control not in the rule's map row | the Control-line-matches-the-map assertion (#873) |

The second provocation asserts its own precondition (that its target really is
unwired), so if a later change wires that script into a gate the control fails with
an explanation rather than silently weakening. The last two mutate a **copy of
`docs/GOLDEN-RULES.md`**, not the map, reusing the same `assert_map` function the
first three exercise — there is one harness, not two.

### The spine binding (#873)

Every Part B rule now carries a fourth part after `**Verify.**`:

```
**Control.** modules: `a/`, `b/` — controls: `check-x.sh`, `suite:dir`
```

This is not prose: `check-control-coverage.sh` parses it (backticks stripped,
comma-separated names trimmed) and requires its modules set and controls set to be
**exactly** the same as the rule's row in `scripts/control-coverage.tsv`, checked in
both directions — a name added to one side without the other fails by rule and
name. This is what makes the two sources of truth actually one: previously only the
TSV row was checked against the repository; the human-readable spine line could say
anything, or nothing, and no gate would notice. Now the spine line and the map are
bound, and neither can drift out from under the other without the gate naming
exactly which rule and which name disagreed.

## 5. Closing a gap

1. enforce the rule — add or wire the control that can fail;
2. flip its status to `ENFORCED` in `scripts/control-coverage.tsv`;
3. re-run `bash scripts/check-control-coverage.sh --record` to **shrink** the
   record;
4. the rule now appears in the map as a control a reviewer can run.

Enforced rules: **9 of 9**. The record is empty — and a new gap would still be refused by name.
