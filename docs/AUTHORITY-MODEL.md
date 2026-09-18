# Authority model — repo separation, admin rights and end-to-end closure

**Issue #150** (EPIC-00 #144) · pillar: governance · product spine:
`docs/GOLDEN-RULES.md` (AO-GR-1..28) · fleet doctrine: [`../AGENTS.md`](../AGENTS.md)

Each governed repo has **its own fleet** — its own engineering team — that
controls, locks and implements that repo's issues to full closure under
**scoped** admin authority. No fleet may act on another repo's files, issues or
state. A single **enterprise controller** has admin across repos for roll-up and
is the **only** cross-repo actor. Closure is gated on recorded evidence, and
separation of duties is schema-enforced.

Machine-readable model: [`../governance/authority/matrix.yaml`](../governance/authority/matrix.yaml)
· schema: [`../governance/authority/schema.json`](../governance/authority/schema.json)
· engine: [`../governance/authority/model.py`](../governance/authority/model.py)
· controls: [`../governance/authority/controls.yaml`](../governance/authority/controls.yaml)
· gate: [`../scripts/check-authority.sh`](../scripts/check-authority.sh).

**Freshness.** The machine-checkable claims in this document (matrix validity,
the scoping/SoD/closure rules, the isolation scenario) are re-verified by
`bash scripts/check-authority.sh` on every run — that gate *is* the freshness
mechanism, not this prose. Verified green on 2026-09-13 against lane branch
`issue-150-authority-<hex>` cut from `de5b7ed`.

---

## 1. The matrix

A matrix declares three things, and nothing else:

| Section | What it declares |
|---------|------------------|
| `repos[]` | Every repo under governance: the **one** fleet that administers it, its own gate command, and the admin rights it delegates (`files`, `issues`, `state`, `merge`). |
| `principals[]` | The authority holders: one `repo-fleet` per repo plus exactly one `enterprise-controller`. Each carries `scope`, `cross_repo`, `admin_rights` and the `actors` that hold its identity. |
| `work_items[]` | Items under closure governance: their repo, their separation-of-duties assignment, and (when a run has produced them) the two evidence records. |

An **actor** is one identity inside a principal — the unit separation of duties
reasons about. It carries the harvested chain-of-command `role`
(`commander`, `lieutenant-commander`, `general`, `platoon-leader`, `soldier`,
`auditor`, `scribe`), the closed persona `posture`
(`executor` | `reviewer` | `auditor`, the vocabulary owned by
[`../registry/personas/persona-card.schema.json`](../registry/personas/persona-card.schema.json)),
and a dispatch `model_tier` (`LOW` | `MED` | `HIGH` | `MAX`).

The shipped matrix declares `kushin77/agent-orchestrator` and
`kushin77/capital-underwriting`, each administered by its own fleet of seven
actors (commander, lieutenant-commander, general, platoon-leader, soldier,
auditor, scribe), plus the single `enterprise-controller`.

## 2. The scoping rule — admin rights stop at the repo boundary

`can_act(principal, repo, action)` is the single decision point, and it denies
in this order:

1. **Not declared** → CANNOT-ASSESS. An unknown `principal`, `repo` or `action`
   has no authority record to decide on, and an absent record is never a pass.
2. **Out of scope** → DENY `out-of-scope`. The principal's `scope` does not
   contain the repo. A `repo-fleet` is scoped to **exactly one** repo; a fleet
   claiming two repos is a validation failure (`fleet-scope-single`), because a
   fleet that spans repos *is* the cross-repo hole this model closes.
3. **Cross-repo actions** → DENY `cross-repo-denied`. `rollup` is inherently
   cross-repo and is reserved for the principal with `cross_repo: true`.
4. **Merge authority** → DENY `merge-authority-denied`. `merge` additionally
   requires the acting identity to carry `merge_authority`, which only a
   `commander` may hold (`merge-authority-commander`) — and the shipped
   enterprise controller deliberately does **not** hold it, so no cross-repo
   actor can land another repo's work.
5. **Rights** → DENY `admin-right-not-granted`. Effective rights are the
   principal's rights **intersected with what the repo delegates**; a fleet
   holding rights its repo never granted is a validation failure
   (`fleet-rights-subset`).

The repo → fleet mapping is one-to-one (`fleet-scope-single`): if a repo named
fleet A while fleet B claimed it, two fleets would administer one repo and the
scoping guarantee would be vacuous.

**Who can do what, in the shipped matrix**

| Actor | Own repo | Other repo | Roll-up |
|-------|----------|------------|---------|
| any fleet actor | ALLOW for the rights its repo delegates | DENY `out-of-scope` | DENY `cross-repo-denied` |
| fleet commander | + ALLOW `merge` | DENY `out-of-scope` | DENY `cross-repo-denied` |
| `enterprise-controller` | ALLOW | ALLOW | ALLOW |

## 3. One cross-repo actor, demonstrated

`cross-repo-single` requires **exactly one** principal with `cross_repo: true`
and `cross-repo-kind` requires it to be the `enterprise-controller`, scoped to
every declared repo. A second `cross_repo: true` principal is a validation
failure — that is the headline negative control of the gate.

Isolation between two repos is not asserted, it is **demonstrated**
(`governance/authority/isolation.py`, run by `cli.py isolation` and by the gate):

1. a fleet writes and reads **its own** repo's state (claims, sessions, board);
2. a second fleet's write to that repo is **denied**, and the target repo's state
   is byte-identical before and after — the denial is side-effect free;
3. cross-repo reads and key listings are denied and yield nothing;
4. every repo holds its **own** state container (no shared object), so there is
   no shared mutable state to leak through;
5. the engine is swept over every actor × repo × action: any ALLOW outside the
   actor's principal scope is reported as an authority leak, and any actor whose
   allowed actions span two repos must belong to a `cross_repo` principal;
6. the `enterprise-controller` reaches both repos (it is the roll-up actor), and
   a scoped fleet attempting `rollup` is denied.

The scenario *derives* its verdict from what the engine actually returned, so
weakening the scoping rule produces a reported finding rather than a passing run.

## 4. Separation of duties — executor ≠ reviewer ≠ auditor

`separation_of_duties(matrix, work_item)` denies, with a specific reason code,
in this order:

| Reason code | Rule |
|-------------|------|
| `sod-duty-unassigned` | a duty with no assigned principal can never satisfy separation |
| `sod-actor-unknown` | the named principal is not declared |
| `sod-executor-equals-reviewer` | the three duties must be three **distinct** principals |
| `sod-executor-equals-auditor` | as above, for the audit of one's own work |
| `sod-reviewer-equals-auditor` | as above |
| `sod-auditor-cannot-execute` | an auditor can never execute what it audits |
| `sod-posture-mismatch` | a duty must be filled by an actor whose `posture` names it |
| `sod-actor-out-of-scope` | a duty may not be filled by another repo's fleet |
| `sod-auditor-tier-below-executor` | an audit cannot be dispatched below the work it audits |

The distinctness rule and the posture rule **overlap on purpose**: an actor
carries exactly one posture, so a posture-correct assignment is also distinct.
Weakening either rule alone still denies (defence in depth); the control set
proves that both must fall before a collision gets through.

## 5. End-to-end closure — both gates, recorded, one commit

`is_closed(matrix, work_item)` is true only when **both** the item's own gate
evidence **and** the repo-level `make verify` evidence are recorded as real
evidence. The repo's gate command is declared per repo in the matrix
(`repos[].gate`), so the model checks evidence against the gate the repo
actually publishes. Every failure is an explicit DENIAL:

| Denial | Meaning |
|--------|---------|
| `closure-<slot>-evidence-missing` | the evidence record is absent |
| `closure-<slot>-evidence-command-missing` | no command recorded — a claim, not evidence |
| `closure-<slot>-evidence-output-missing` | missing, empty or whitespace-only output (issue #150: a missing or empty evidence field is a denial, never a pass) |
| `closure-<slot>-evidence-failed` | a non-zero exit code: **never merge failing work** |
| `closure-<slot>-evidence-sha-missing` | evidence must name a commit |
| `closure-<slot>-evidence-sha-mismatch` | the evidence is not for the item's head commit |
| `closure-<slot>-evidence-repo-mismatch` | evidence recorded in a different repo |
| `closure-<slot>-evidence-recorder-unknown` / `-out-of-scope` | the recorder has no authority on that repo |
| `closure-evidence-sha-divergence` | the gate and verify evidence describe different commits (or none) |

`<slot>` is `gate` or `verify`. Evidence that names a commit cannot be committed
to the matrix: it is stale by construction. The shipped work items therefore
carry **no** evidence and are correctly reported open — the gate asserts exactly
that, so a closure check that passed on an evidence-less item would fail the
gate rather than become a false green.

## 6. Tri-state and the exit-code contract

| Verdict | Gate exit | When |
|---------|-----------|------|
| `ALLOW` | 0 | the action is authorised / the document is valid / every control met |
| `DENY` | 1 | an explicit denial, a real document defect, a failed control |
| `CANNOT-ASSESS` | 2 | the matrix or schema cannot be read, the version is unknown, or the subject is not declared |

The split, precisely:

* **document validity** = schema (types, closed vocabularies, patterns,
  uniqueness) + semantic invariants → NOT-OK;
* **compliance** = assignments and evidence → DENY;
* **cannot decide** = unreadable input, unknown version, unknown subject → CANNOT-ASSESS.

A schema keyword the engine does not implement raises `SchemaError` rather than
being ignored: silently skipping a constraint is a false green, and the engine
refuses to run that way. The aggregate gate reports NOT-OK if any check is
NOT-OK, else CANNOT-ASSESS if any check could not decide, else OK.

## 7. Blast radius

* **What a defect here can do.** The module decides *admission*: may a principal
  act on a repo, is a duty assignment compliant, may an item be treated as
  closed. A wrong matrix therefore mis-authorises work and can retire an issue
  too early. Both are decision-level: the module performs **no writes**, opens
  no network connection, holds no credentials, and cannot itself touch a repo,
  a branch or an issue. Its only I/O is reading the matrix, the schema and the
  controls file.
* **What it cannot do.** It cannot merge, deploy or push; `merge` here is an
  authorisation question other automation asks, not an action this code takes.
  It cannot widen anyone's authority: the effective rights of a principal are
  always a subset of what the owning repo delegates, and a fleet cannot
  self-grant (`fleet-rights-subset`).
* **Failure mode is fail-closed.** Any parse error, unknown version, unknown
  vocabulary value or unsupported schema keyword yields NOT-OK or
  CANNOT-ASSESS — never ALLOW. The dangerous direction (allowing an
  unauthorised action) requires an explicit, reviewable change to
  `model.py` or `matrix.yaml`, and the declared controls exist to fail when that
  change weakens a rule.
* **Residual risk.** The matrix is a declaration, not an enforcement of the
  filesystem: nothing here prevents a process from editing another repo's files
  outside the model. What it provides is an authoritative admission decision that
  dispatch, merge governance and closure checks consume — and a gate that fails
  when the rule is weakened.

## 8. Provenance (GR-10, cannibalization-first)

Harvested from the fleet's mature vocabulary; nothing here is a parallel
taxonomy. Read-only sources were not modified.

| Source repo | Path | Verdict — what was reused |
|-------------|------|---------------------------|
| `kushin77/capital-underwriting` | `docs/wiki/ELITE_COMMANDER_LTC_CHARTER.md` | Commander = brain, no direct execution, **only role with merge authority**; Lieutenant-Commander = execution arm that absorbs the verification loop; tier ladder (pro / pro-max / flash). Reused as: the `role` vocabulary, `merge_authority` on a commander only, `merge-authority-denied`, and the auditor tier floor. |
| `kushin77/capital-underwriting` | `docs/wiki/ELITE_GENERAL_CHARTER.md` | Authority to gate decisions comes with literal gate results: *"you may never substitute 'looks fine' for a gate result"*. Reused as: evidence carries the command, its literal output and its exit code — a claim is not evidence (`*-command-missing`, `*-output-missing`). |
| `kushin77/capital-underwriting` | `docs/wiki/ELITE_PLATOON_LEADER_CHARTER.md` | Wave ownership through N-1; disjoint-file-set discipline ("two concurrent soldiers never touch the same file"); "a proposal is a claim, not proof". Reused as: one fleet per repo with disjoint scopes (`fleet-scope-single`) and the closure evidence rule. |
| `kushin77/capital-underwriting` | `docs/wiki/ELITE_AUDITOR_CHARTER.md` | Cross-wave structural audit; **hollow-closure risk** (closures backed only by self-reported status); evidence must be generated after the claimed fix commit; a **vacuous-pass check** is worse than no check. Reused as: the closure rule, the commit-named evidence requirement, and the declared-control gate design. |
| `kushin77/capital-underwriting` | `docs/wiki/ELITE_SCRIBE_CHARTER.md` | "Docs are code"; same-wave doc updates; cross-link integrity; freshness markers verified against a live source. Reused as: this document's freshness note and the docs/gate checks that accompany the module. |
| `kushin77/leaderboard` | `docs/LEADERBOARD_PROTOCOL.md` | Worktree-per-session isolation, *collision → isolation*; "every claim must prove itself against the real gate". Reused as: per-repo state isolation, the demonstrated (not asserted) two-repo scenario, and closure gated on recorded gate output. |
| `kushin77/agent-orchestrator` | `registry/personas/persona-card.schema.json` | The closed `posture: executor\|reviewer\|auditor` vocabulary and its rule that *an auditor persona is never the executing persona of the task it audits*. Reused **verbatim** as the `posture` enum and duty matching; the file is read-only here. |
| `kushin77/agent-orchestrator` | `governance/merge/model.py`, `governance/merge/reviewer.py` | Tri-state verdicts; independent-reviewer separation of duties (AO-GR-14); "an attestation names a COMMIT". Reused as: the same tri-state contract, the same SoD semantics, commit-named closure evidence. |
| `kushin77/agent-orchestrator` | `guardrails/honesty` (issue #28) | Tri-state 0/1/2 with **CANNOT-ASSESS is never a pass**. Reused as the CLI and gate exit-code contract. |

`docs/CANNIBALIZATION.md` is owned by another lane and is deliberately not
edited here; these rows are the authoritative provenance record for this module.

## 9. Handoff (owned by the orchestrator)

This lane ships the module **unwired** because the wiring files are shared and
owned elsewhere:

1. `governance/authority` → `scripts/pytest-suites.txt` (the suite is not
   registered yet; `scripts/check-drift.sh` stays green because its
   unregistered-suite scan covers the pillar roots only);
2. `authority|bash scripts/check-authority.sh` → the check list in
   `scripts/verify.sh`;
3. a row for this document → the index in `docs/README.md`.

Until (2) lands, run `bash scripts/check-authority.sh` explicitly; it is
tri-state, offline and dependency-free (stdlib + PyYAML).
