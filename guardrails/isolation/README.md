# guardrails/isolation — tenant isolation integrity (detect-first / opt-in repair)

> Owner lane: **guardrails** · issue `kushin77/agent-orchestrator#30` ("26
> Tenant isolation integrity (detect-first / opt-in repair)", work item 26,
> phase 4). Parent: EPIC-00 (issue #4). Doctrine:
> [`../../AGENTS.md`](../../AGENTS.md),
> [`../../docs/EXECUTION-PLAN.md`](../../docs/EXECUTION-PLAN.md),
> [`../../docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md),
> [`../../docs/GOLDEN-RULES.md`](../../docs/GOLDEN-RULES.md).
> The guardrails pillar landing doc is [`../README.md`](../README.md).

This subtree is the **tenant isolation integrity** lane of the Security &
guardrails pillar. It guarantees isolation integrity end-to-end on the
**detect-first / opt-in-repair** doctrine (cannibalized from
`capital-underwriting`'s `accountIsolationRepair` service and `saas-rbac`'s
"scope is a separate gate from permission" incident doctrine — see
[`PROVENANCE.md`](PROVENANCE.md)):

1. **Detect** — a static AST scanner of tenant-store surfaces
   (scope-drop reads/writes, cross-tenant fallbacks, shared mutable state),
   a data-integrity detector over tenant-scoped datasets (orphaned records,
   fallback-tenant pile-up, cross-tenant duplicates, denormalization
   violations), and runtime per-tenant cross-tenant read/write probes.  The
   detector never mutates anything.
2. **Triage** — every finding maps by severity to auto-**BLOCK**
   (critical/high) or an **SME-reviewer queue** (medium) or **LOG** (low).
3. **Repair** — opt-in, transactional (validate-everything-then-commit),
   audited (an in-state ledger), idempotent.  **Dry-run is the default**;
   nothing changes without an explicit `--apply`.

Verdicts use the guard-honesty tri-state vocabulary (issue #28, consumed
read-only from the sibling [`../honesty`](../honesty) lane): **OK / NOT-OK /
CANNOT-ASSESS**, where CANNOT-ASSESS never reads as a pass.  Everything is
offline, deterministic and depends only on the Python standard library.

---

## 1. The tri-state verdict contract

Every scan reports per *tenant-store surface* (an entity index inside a
store class, or a module-level container) exactly one of three verdicts, and
every finding carries reproduction evidence:

| Verdict | Meaning |
|---|---|
| **OK** | a tenant-dimensioned entity index whose every read/write/delete carries the tenant dimension — no scope drops, no fallbacks. |
| **NOT-OK** | a concrete anti-pattern fired (R1–R4 / D1–D4 / P1) with file + line + evidence. |
| **CANNOT-ASSESS** | an entity index keyed only by non-tenant identifiers (an id-keyed store bound to one tenant object, composite derived keys, or a catalog-style global) — tenant scoping is not provable statically. Enumerated in every report; **never reads as a pass**. |

Aggregation is fail-closed (the honesty contract): a single NOT-OK fails the
scan; any CANNOT-ASSESS keeps the aggregate from reading OK; only an all-OK
set reads OK.

## 2. Code anti-pattern rules (static AST scan)

The scanner ([`scanner.py`](scanner.py)) models the *entity indexes* of a
module — dict-typed attributes (`self._x = {}`, a dataclass
`x: dict = field(default_factory=dict)`) or module-level containers accessed
with non-constant keys — and classifies each as tenant-dimensioned when at
least one write keys it with a tenant identity (`tenant_id`, `agent.tenant_id`,
`kb.tenant_id`; catalog keys like `tenant_type` are deliberately excluded).

| Rule | Severity | Anti-pattern detected |
|---|---|---|
| **R1 SCOPE-DROP-READ** | high | tenant-dimensioned index read by a non-tenant key (`self._agents.get(agent_id)`) — cross-tenant read vector |
| **R2 SCOPE-DROP-WRITE** | critical | tenant-dimensioned index written/deleted by a non-tenant key — cross-tenant write vector |
| **R3 CROSS-TENANT-FALLBACK** | high | tenant-dimensioned index read with `.get(non_tenant_key, default)` — fallback across the tenant boundary |
| **R4 SHARED-MUTABLE-STATE** | high | module-level mutable container reached from tenant-scoped methods and not tenant-dimensioned — shared state across tenant namespaces |

The scanner is not a formality: the fixtures under
[`fixtures/code/`](fixtures/code/) plant every rule (a `#107`-class store
that reads/writes by inner id and a shared global cache), and the pytest
suite proves the scanner catches all of them and clears the safe store.

## 3. Store-layer scope gate (acceptance criterion 3)

[`store.py`](store.py) is the reference store shape every platform store
follows and the surface the lane scans/probes/repairs: records carry a
denormalized `tenant_id`, and **every accessor enforces tenant scope at the
store layer** — `get(tA, rid)` on a row owned by tB returns `None`, `require`
raises `IsolationScopeError`, `put` rejects a record whose denormalized
tenant does not match its bucket, `delete` is a no-op on a foreign row.
Scope is never an app-layer filter over an unscoped store.  The registry
`RegistryStore`, the KB `KbRegistry` and the memory `MemoryStore` already
implement this doctrine; this lane's exemplar is dependency-free and
negative-tested.

## 4. Dataset integrity scan + per-tenant cadence probes (AC1)

[`integrity.py`](integrity.py) scans a canonical JSON tenant dataset
(see [`fixtures/data/`](fixtures/data/)):

| Rule | Severity | Violation |
|---|---|---|
| **D1 ORPHANED-RECORD** | critical | record bucket references no tenant namespace |
| **D2 FALLBACK-TENANT-PILEUP** | critical | real record piled up in the reserved fallback tenant (unscoped / ambiguous) |
| **D3 CROSS-TENANT-DUPLICATE** | high | same record id present under more than one tenant (ambiguous ownership) |
| **D4 DENORMALIZED-RECORD** | critical | record's own `tenant_id` disagrees with its bucket |

`run_cadence_probes` runs **per tenant on cadence**: for each tenant it
attempts to read/require/delete a foreign tenant's row and to write a record
whose denormalized tenant does not match the bucket — every attempt must fail
closed.  Any probe that does *not* fail closed is a **P1 PROBE-FAILURE**
finding.  The runner is negative-controlled in the suite against a
deliberately leaky store.

## 5. Triage gate (AC4)

[`triage.py`](triage.py) maps severity to action — there is no silent path:

| Severity | Action |
|---|---|
| critical / high | **BLOCK** (auto-block; the surface is not deployable) |
| medium | **SME_REVIEW** (SME-reviewer queue; never auto-repaired or auto-dismissed) |
| low | **LOG** (recorded only) |

An unrecognized severity raises rather than defaulting silently.

## 6. Opt-in repair (AC2)

[`repair.py`](repair.py) is the only mutation path and it is **never
automatic**:

* `plan_repairs(dataset)` — dry-run default.  Returns the concrete actions
  (quarantine / rescue-to-tenant / quarantine-duplicates), mutating nothing.
* `repair_execute(dataset, operator=...)` — the only mutator, and
  **transactional**: it applies every action to a private clone first, then
  re-scans; any surviving finding aborts with `RepairAbortError` and the
  caller's dataset is untouched (all-or-nothing).  On success the audit
  ledger is appended and the repaired dataset returned.
* **Audited** — every successful repair appends an entry (`operator`, action
  count, issue types, timestamp) to the dataset's `audit` ledger.
* **Idempotent** — re-planning a repaired dataset yields zero actions; a
  no-op re-execute changes nothing and records nothing.
* Repair never deletes and never guesses: orphans and ambiguous records are
  quarantined (with provenance), fallback records are rescued only to the
  real tenant their own `owner_hint`/denormalized `tenant_id` names, and
  cross-tenant duplicates keep one canonical copy and quarantine the rest.

## 7. Integrity self-check against this repo

[`selfcheck.py`](selfcheck.py) runs the scanner over the platform's own
tenant-scoped modules — `registry/service`, `identity/rbac`, `gateway/mcp`,
`engine/memory` — read-only (static AST, nothing imported or mutated).  It
must report clean or produce concrete findings, and it is the regression gate
for future store changes.  Coverage is honest: today it verifies the genuine
cross-tenant registries OK (`RegistryStore._agents`, `KbRegistry._kbs`,
`TaskRouter._routes`) and enumerates every id-keyed/composite surface as
CANNOT-ASSESS (never hidden).  `tests`/`__pycache__` are excluded.

## 8. CLI

```bash
# from the repo root (or the lane directory)
export PYTHONPATH=guardrails

python3 -m isolation check                       # self-check, honest tri-state
python3 -m isolation scan <paths...> [--base B]  # static code anti-patterns
python3 -m isolation integrity <dataset.json>    # dataset scan (D1-D4)
python3 -m isolation cadence <dataset.json>      # per-tenant probes (P1)
python3 -m isolation repair <dataset.json>       # dry-run plan (no mutation)
python3 -m isolation repair <dataset.json> --apply --operator lane30
python3 -m isolation triage <findings.json>      # severity -> BLOCK/SME/LOG
```

Exit codes follow the guard-honesty contract: `0` OK, `1` NOT-OK (a finding /
gate block), `2` CANNOT-ASSESS (`--strict` self-check with unmodeled
surfaces).  Dry-run repair and triage of non-blocking findings exit `0`.

## 9. Layout

| Path | Purpose |
|---|---|
| [`tristate.py`](tristate.py) | Re-export of the honesty tri-state model (single source of truth, issue #28) |
| [`model.py`](model.py) | Finding / severity / index-verdict / scan-report models |
| [`scanner.py`](scanner.py) | AST tenant-isolation anti-pattern scanner (R1–R4) |
| [`store.py`](store.py) | Tenant-scope-gated store exemplar (AC3) |
| [`integrity.py`](integrity.py) | Dataset detector (D1–D4) + cadence probes (P1) |
| [`repair.py`](repair.py) | Opt-in transactional audited idempotent repair (AC2) |
| [`triage.py`](triage.py) | Severity → BLOCK / SME-reviewer / LOG gate (AC4) |
| [`report.py`](report.py) | Deterministic text/JSON report renderers |
| [`selfcheck.py`](selfcheck.py) | Self-check over this repo's tenant-scoped modules |
| [`cli.py`](cli.py) / [`__main__.py`](__main__.py) | `python3 -m isolation …` |
| [`fixtures/code/`](fixtures/code/) | Safe / leaky / shared-global store fixtures (negative-test material) |
| [`fixtures/data/`](fixtures/data/) | Clean + leaky tenant datasets |
| [`tests/`](tests/) | pytest suite (positive + negative, both directions) |
| [`PROVENANCE.md`](PROVENANCE.md) | Cannibalization record (AO-GR-10) |

## 10. Verification

```bash
cd guardrails/isolation
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest tests -q     # 59 tests: honest gates + negatives
python3 -m isolation check                               # self-check: clean (0 NOT-OK)
python3 -m isolation repair fixtures/data/leaky_dataset.json   # dry-run, no mutation
```

All offline, standard library only.
