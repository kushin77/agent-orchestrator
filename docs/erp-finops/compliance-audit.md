# ERP/FinOps Compliance Audit — Phase-4 validation & governance

- **Issue:** `kushin77/agent-orchestrator#676` (PF-11) — parent epic `#665` (ERPNext & DeepSeek FinOps)
- **Lane:** `compliance-audit`
- **Audited:** 2026-09-14 (what exists on the tree **today**)
- **AI-assistance:** Copilot (Relentless, flash/LOW)

> **Honesty note (elite).** This is a *measured* audit of what exists today, not
> a prediction. Two of the four briefed audit dimensions — webhook latency and
> transaction discrepancies — cannot be assessed because the subject code (the
> CRM→ERPNext bridges) has not landed. Those sections record `CANNOT-ASSESS`
> explicitly and, instead of fabricating results, enumerate the edge-case
> *classes* the future integration must test. Every assertion about existing code
> cites `file:line` actually read.

---

## 1. Scope and method

The issue briefs four audit dimensions over the "agent action → financial ledger"
chain:

1. **Security-standards compliance** — which repo gate covers which concern.
2. **Webhook latency** — per-bridge latency numbers.
3. **Transaction discrepancies** — discrepancy classes and resolutions.
4. **Automated journal-entry edge cases** — each with a test.

Only dimension 1 is fully assessable today. Dimensions 2–4 depend on the
CRM→ERPNext integration surface, which is ordered by epic `#645` ("ERP module —
indexer-fed enterprise integration of the ERPNext feature surface", **open**) and
whose webhook bridges are `#671` ("CRM→ERPNext webhook bridges", **open**,
Blocked-by `#645`) and the E2E sandbox sim `#675` (**open**, Blocked-by
`#645`/`#607`).

Measured evidence of absence (read on the lane tree):

- `integrations/` contains **only** `paperclip/` — there is no `integrations/erp`
  directory (`ls integrations/erp` → *"No such file or directory"*).
- `grep -ril "ERPNext"` over the whole tree (excluding `vendor/`) returns
  **zero** matches — the string "ERPNext" appears nowhere in tracked code/docs.

---

## 2. Security-standards compliance — gate-coverage map

The gate of record is `scripts/verify.sh` (`make verify`). It declares an
explicit `checks=()` array (every check named by `name|command`), and a secondary
surface `scripts/gate.sh` / `scripts/merge-gate.sh` (`make gate` /
`make merge-gate`) runs four more checks (`check-drift.sh`,
`check-negative-controls.sh`, `check-policy-schema.sh`,
`check-pr-contract.sh`). The security golden rules are AO-GR-5..AO-GR-20 in
`docs/GOLDEN-RULES.md`.

Coverage is classified per concern:

| Coverage | Meaning |
|---|---|
| **covered-by-gate** | A mechanical check in `scripts/verify.sh` (gate of record) or `scripts/gate.sh`/`merge-gate.sh` exercises the concern and can genuinely fail. |
| **declared-only** | A golden rule or module exists in the tree, but no mechanical gate exercises it. Advisory, not enforced (GR-29: "a doc-only rule is advisory; only a platform-enforced gate is binding"). |
| **absent** | No rule, module, or gate. |

| # | Concern | Golden rule | Enforcing gate (surface) | Coverage |
|---|---|---|---|---|
| 1 | Secrets in code / history | AO-GR-7 | `secrets` → `scripts/check-secrets.sh` (`verify.sh:133`); `paperclip-secrets` → `scripts/check-paperclip-secrets.sh` (`verify.sh:110`) | **covered-by-gate** |
| 2 | DLP + prompt-injection + egress on model calls | AO-GR-16 | *none* — `guardrails/dlp/` is declared but carries no `verify.sh`-wired gate or promotion flag at all (not an AO-GR-6 off-by-default case) | **declared-only** |
| 3 | Policy-as-code bundle validation | AO-GR-9 / AO-GR-16 | `scripts/check-policy-schema.sh` (via `gate.sh`/`merge-gate.sh`, **not** `verify.sh`) | **covered-by-gate** (`make gate` only) |
| 4 | Guard honesty (tri-state + negative controls) | AO-GR-19 | `guardrail-controls` (`verify.sh:85`); `control-verbs`/`control-audit`/`control-functions` (`verify.sh:300–302`); `check-negative-controls.sh` (via `gate.sh`) | **covered-by-gate** |
| 5 | Tamper-evident audit ledger | AO-GR-17 | `audit-read-model` → `scripts/check-audit-read-model.sh` (`verify.sh:83`) | **covered-by-gate** |
| 6 | Tenant isolation (structural) | AO-GR-15 | *none* — `guardrails/isolation/` scaffold | **declared-only** |
| 7 | Sandbox execution isolation | — | *none* — `guardrails/sandbox/` module | **declared-only** |
| 8 | Dev session / lane isolation | — | `session-isolation` → `scripts/check-session-isolation.sh` (`verify.sh:65`) | **covered-by-gate** |
| 9 | Fully-IaC (no console clicks) | AO-GR-5 | `terraform` → `scripts/check-terraform.sh` (`verify.sh:136`); `cloudbuild` (`verify.sh:135`) | **covered-by-gate** |
| 10 | Cross-boundary auth seam | — | `paperclip-auth` → `scripts/check-paperclip-auth.sh` (`verify.sh:98`) | **covered-by-gate** |
| 11 | Private by default | AO-GR-20 | *none* — proxy allowlist boundary declared (issue `#37`) | **declared-only** |
| 12 | Per-tenant budgets / quotas / kill switch | AO-GR-18 | *none* — declared (issue `#34`) | **declared-only** |
| 13 | Scratch / resource safety | — | `scratch-safety` → `scripts/check-scratch-safety.sh` (`verify.sh:148`) | **covered-by-gate** |

### 2.1 What each security gate actually proves (quoted, not paraphrased)

- **`secrets` — `scripts/check-secrets.sh`** (`verify.sh:133`): a mechanical,
  always-on pattern scan over the repo's text files; "Any finding is a hard
  failure." Patterns include EC2/PGP private keys, AWS `AKIA…`, GitHub tokens
  (`ghp_/gho_/ghu_/ghs_`), GitHub App PATs, `sk-*`, Google `AIza…`, Slack
  `xox…`, plus a generic `secret|password|token|…`-assignment shape
  (`check-secrets.sh:1–30`). This is the AO-GR-7 mechanical scan.
- **`paperclip-secrets` — `scripts/check-paperclip-secrets.sh`** (`verify.sh:110`):
  the per-agent secret vault gate — a GSM-backed *reference/rotation view* that
  names a secret's path/scope/rotation/consumer and "never carries the value";
  fails by name on a carried value, a second store, an unscoped read, or an
  orphaned consumer, with its own negative control
  (`check-paperclip-secrets.sh:1–27`).
- **`guardrail-controls` — `scripts/check-guardrail-controls.sh`** (`verify.sh:85`):
  proves every control defaults OFF, an unknown control id is refused with no
  audit record, a toggle writes exactly one append-only record, the status
  vocabulary is the closed `246 PASSED / 446 BLOCKED` pair, and the self-mutating
  negative control (`check-guardrail-controls.sh:1–20`).
- **`control-verbs` / `control-audit` / `control-functions`** (`verify.sh:300–302`):
  the EPIC `#551` remote-control command-center gates — closed control-verb
  vocabulary (RC-2 `#553`), exactly-once control with the audit record + refusal
  path (RC-4 `#555`), and every cockpit function declared once (RC-10 `#565`).
- **`audit-read-model` — `scripts/check-audit-read-model.sh`** (`verify.sh:83`):
  proves the audit read model is deterministic and read-only, that
  `verify_chain()` is OK on an intact chain and NOT-OK (naming the record) on a
  modified / reordered / removed / truncated record, that an unparseable chain is
  CANNOT-ASSESS (never a pass), and that no mutating name is exposed
  (`check-audit-read-model.sh:1–25`).
- **`session-isolation` — `scripts/check-session-isolation.sh`** (`verify.sh:65`):
  proves each agent session is a minted identity bound to one issue in its own
  worktree/branch (AO rule 15 of `AGENTS.md`).
- **`terraform` / `cloudbuild`** (`verify.sh:136` / `verify.sh:135`): the AO-GR-5
  fully-IaC declarations validate; `scripts/check-terraform.sh` reuses one
  `TF_DATA_DIR` for `init` and `validate`.

### 2.2 Declared-only concerns (no mechanical gate — advisory, not enforced)

- **DLP / prompt-injection / egress (AO-GR-16).** `guardrails/dlp/` ships the
  `EgressPipeline` + `EgressGuard` with per-call HMAC, "flag gated OFF"
  (`guardrails/dlp/README.md:16–18`), injection defenses
  (`guardrails/dlp/README.md:170`). There is **no** `scripts/check-dlp.sh` /
  `check-egress.sh` and no DLP suite in the `checks=()` array — the module is
  implemented but *not gate-exercised*.
- **Tenant isolation (AO-GR-15).** `guardrails/isolation/` exists as a scaffold
  (integrity/repair/triage/scanner); no `scripts/check-isolation.sh`.
- **Sandbox execution.** `guardrails/sandbox/` (firecracker/docker microvm
  profiles) exists; no `scripts/check-sandbox.sh`.
- **Private-by-default (AO-GR-20)** and **budgets/kill-switch (AO-GR-18)** are
  declared in `docs/GOLDEN-RULES.md` (`:390` and `:360`) with their `Verify:`
  tied to future issues `#37` / `#34`; no gate today.

### 2.3 Compliance findings (today)

1. **`guardrails/README.md` is stale and contradicts the tree.** It states
   *"Placeholder scaffold from issue #5. No implementation yet."*
   (`guardrails/README.md:20`) while `dlp/`, `policy/`, `honesty/`, `isolation/`,
   `sandbox/`, `controls/`, `chat/` all carry real implementations. The pillar
   README understates the actual surface — a reader of the top-level README
   would conclude DLP/policy are unbuilt when they are built-but-ungated.
2. **AO-GR-16 (DLP) and AO-GR-15 (tenant isolation) are `declared-only`.** The
   modules exist but are not exercised by the gate of record; per GR-29 they are
   advisory until a platform-enforced gate lands.
3. **No ERP/FinOps-specific gate exists** — none of `scripts/check-*.sh` targets
   ledger, journal, reconciliation, or webhook integrity. The entire
   financial-integity surface is still to be built (see §3–§5).

---

## 3. Webhook latency — `CANNOT-ASSESS`

**CANNOT-ASSESS (depends on #671/#645, not yet landed).**

The CRM→ERPNext bridges do not exist yet. Measured on the lane tree:

- `integrations/` has no `erp/` directory (`integrations/erp` → not found).
- `grep -ril "ERPNext"` → zero matches outside `vendor/`.

Consequently there are **no bridges to measure**, and no latency number can be
honestly reported. Fabricating a latency figure (even a "placeholder") would
violate the audit's honesty rules. When `#671` lands (Blocked-by `#645`), the
latency dimension must be re-run with the checklist in §5 and a real
p50/p95/p99 per bridge under load.

---

## 4. Transaction discrepancies — `CANNOT-ASSESS` + future checklist

**CANNOT-ASSESS.** Discrepancy detection is a property of the (not-yet-landed)
CRM→ERPNext reconciliation path. There are no discrepancy classes *found* today
because there is no transaction path to observe. No resolution can be reported.

What this audit *can* do honestly is name the discrepancy **classes** the future
integration must detect and the **test each needs** (delivered here as a
checklist, not as fake results). These belong to `#671`/`#675` (both Blocked-by
`#645`); this lane does not write those tests.

---

## 5. Journal-posting edge-case classes — checklist (each with the test it needs)

Each row is a class the automated journal-entry integration must prove, with the
test that fails-before-fix and passes-after (the issue's acceptance criterion:
*"each edge case has a test that fails before the fix and passes after"*).

| # | Edge-case class | Why it matters (failure it prevents) | Test required (fail-before → pass-after) |
|---|---|---|---|
| E1 | **Duplicate event idempotency** | A redelivered webhook must not double-post a journal entry or a revenue event. | Replay the same `event_id` twice; assert exactly one journal entry and one ledger line, and that the second delivery returns an idempotent "already applied" (no new row). |
| E2 | **Journal rounding** | Monetary rounding must not let a multi-line journal drift from the source total (sum-of-lines vs. header total). | Post a source amount whose per-line allocation does not divide evenly (e.g. 0.01 across 3 lines); assert the rounding delta is carried on a designated "rounding" line so `Σ lines == header` to the cent. |
| E3 | **Timezone boundary** | A conversion event at a UTC-boundary must land in the correct accounting period (not the neighbouring one). | Emit events at `23:59:59.999Z` and `00:00:00.000Z` on a period boundary; assert each posts to the tenant's declared fiscal period, not the adjacent one. |
| E4 | **Retry storm** | A downstream outage must not accumulate a backlog of duplicate posts or unacknowledged writes. | Drop the ERPNext side mid-batch, allow retries with exponential backoff + idempotency key; assert no duplicate ledger rows and that the final state equals a single clean post. |
| E5 | **Partial posting** | A multi-line journal that fails mid-write must not leave a half-committed entry (all-or-nothing). | Simulate a failure on line *N* of *M*; assert the whole journal is rolled back (no orphan lines, no partial balance) and the event is left retryable. |
| E6 | **Negative / reversal amounts** | Refunds, chargebacks and reversals must sign correctly and reconcile. | Post a reversal for a previously-posted positive entry; assert the net ledger balance returns to the pre-entry state and the reversal is cross-referenced to its original. |
| E7 | **Currency precision / scale** | A currency with a non-2-decimal scale (e.g. JPY = 0, crypto = 8) must not be truncated. | Post amounts in a 0-decimal and an 8-decimal currency; assert no silent truncation and the stored scale matches the currency's declared minor-unit. |
| E8 | **Sequence-gap / out-of-order delivery** | Reordered events must not corrupt the running balance or the audit chain ordering. | Deliver events out of order (2 before 1); assert the ledger applies them deterministically (by `sequence`/`created_at`) and the running balance is order-independent or the gap is surfaced. |
| E9 | **Reconciliation mismatch** | Ledger totals that disagree with the source CRM/event feed must be detected, not silently accepted. | Feed a source total that differs from the posted journal; assert the reconciliation check flags the discrepancy by name (rather than passing). |

> These are **classes**, not results. None is currently executable because the
> bridges (`#671`) and the E2E sandbox sim (`#675`) are Blocked-by `#645`. The
> tests belong to those lanes, not to this audit.

---

## 6. Summary

| Dimension | Verdict today |
|---|---|
| Security-standards compliance | **Assessable** — 7 concerns covered-by-gate (secrets, paperclip-secrets, guardrail-controls, control-*, audit-read-model, session-isolation, terraform/cloudbuild, scratch-safety, paperclip-auth), 5 declared-only (DLP, tenant isolation, sandbox, private-by-default, budgets/kill-switch). |
| Webhook latency | **CANNOT-ASSESS** (depends on `#671`/`#645`, not yet landed). |
| Transaction discrepancies | **CANNOT-ASSESS** (no transaction path to observe). |
| Journal edge cases | **Checklist delivered** — 9 classes (§5), each with its required test; tests owned by `#671`/`#675`. |

The single most important governance gap for the ERP/FinOps work: **the financial
integrity surface has no mechanical gate at all**, and its two security
neighbours that do matter (DLP for the model→ledger chain, tenant isolation for
multi-tenant ledgers) are `declared-only`. Before `#671`/`#675` can claim
verification, those lanes must ship `check-*` gates for journal idempotency,
rounding, and reconciliation — or the financial spine will be built the way
`AO-GR-29` forbids: documented but not enforced.
