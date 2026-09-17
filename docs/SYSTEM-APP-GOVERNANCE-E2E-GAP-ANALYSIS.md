# System-app + app-model + governance — end-to-end gap analysis (declared → implemented → gated → exercised)

Issue: [#1156](https://github.com/kushin77/agent-orchestrator/issues/1156) ·
Measured: **2026-09-17T16:22Z** · Base: `origin/master` `2fa292cc` ·
Lane: `docs/` + board (filed issues) · Epic: [#878](https://github.com/kushin77/agent-orchestrator/issues/878)

**Re-derive:**

```bash
git rev-parse origin/master
bash scripts/check-system-app-declaration.sh          # the system-app gate, with its own controls
bash scripts/check-docs.sh                            # docs-lint, incl. docs-index membership
bash scripts/check-conformance.sh                     # the filing seam's filing-check half
ls scripts/check-*.sh | wc -l                         # 158 check scripts, all auto-discovered (#698)
grep -rn 'dupcheck' scripts/ Makefile                 # empty == the ADR-0010 detector is unwired
sed -n '79,85p' governance/conformance/policy.yaml   # the filing defaults that derive area:governance
```

## Why this doc exists

Issue #1156 orders a **measured** end-to-end gap analysis of the **system-app + app-model +
governance** path: for every surface in that path, what is *declared*, what is *implemented*,
what *binds* it, and whether the path is *exercised* end to end. It is a measurement, not a
proposal dressed as one: every row below cites a real `file:line`, and every **ENFORCED**
verdict is one seen to fail (the provocations in §6), not a gate's own self-test quoted back.

The point of the exercise is the distinction the repository already names in its own doctrine
([`docs/GOLDEN-RULES.md`](GOLDEN-RULES.md), [`docs/QA-GATE.md`](QA-GATE.md)): *a gate that cannot
fail is a formality*, and *a doc-only rule is advisory; only a platform-enforced gate is binding*.

### Prior art this doc cross-references (it does not re-derive it)

Four enforcement-gap instruments already exist here. This analysis uses them and adds only the
one lens they do not already carry — the **surface** dimension of the system-app/app-model/
governance path (they carry the *rule*, *rung*, *rung-capability* and *artifact* dimensions):

| Instrument | Its axis | Relationship to this doc |
|---|---|---|
| [`docs/CONTROL-COVERAGE.md`](CONTROL-COVERAGE.md) (+ `scripts/check-control-coverage.sh`) | Part-B **rule** → control that runs; measured **7 of 9 rules cited by no check** | The rule axis. This doc's governance rows are surfaces, not rules. |
| [`docs/SURFACE-CLASS.md`](SURFACE-CLASS.md) (+ `make surface-class`) | **surface** → measured rung of the quality ladder | The rung axis. This doc records the **binding check** and the **E2E exercise**, which the rung does not. |
| [`docs/FLEET-CAPABILITY-DRIFT.md`](FLEET-CAPABILITY-DRIFT.md) (+ `scripts/check-fleet-runbook.sh`) | **running rung** → declared capability set | The runtime axis. Orthogonal: this doc is about the tree, not a live process. |
| [`scripts/gate-coverage-baseline.txt`](../scripts/gate-coverage-baseline.txt) (+ `scripts/check-gate-coverage.sh`) | **artifact** → wired into a gate file, or baselined | The wiring axis, and the closest sibling: it already refuses a `scripts/check-*.sh` that no gate invokes. Its blind spot — a detector *wired into a gate* but never *reached by the gate of record*, or one that cannot observe its own claim — is §5. |
| [`docs/MODULE-ADMISSION.md`](MODULE-ADMISSION.md) §9 (issue #945) | the system-app/app-model **exemption** | The primary subject. §9 is real and gated; §4–§5 measure what the gate does and does not bind. |

Existing open trackers in this area are **cited, never re-filed**: [#944](https://github.com/kushin77/agent-orchestrator/issues/944) (declared env-var surface,
`infra/` not a declared surface root), [#1028](https://github.com/kushin77/agent-orchestrator/issues/1028) (`system-app`
named as a governance surface), [#878](https://github.com/kushin77/agent-orchestrator/issues/878) (EPIC: every surface to
elite), [#946](https://github.com/kushin77/agent-orchestrator/issues/946), [#966](https://github.com/kushin77/agent-orchestrator/issues/966), [#967](https://github.com/kushin77/agent-orchestrator/issues/967), [#968](https://github.com/kushin77/agent-orchestrator/issues/968), [#935](https://github.com/kushin77/agent-orchestrator/issues/935), [#936](https://github.com/kushin77/agent-orchestrator/issues/936).

## 1. The verdict vocabulary, and how it maps to the repo's own doctrine

Every surface is classified into **exactly one** of four verdicts:

| Verdict | Meaning | Repo doctrine it restates |
|---|---|---|
| **ENFORCED** | a gate binds the surface **and** it was *seen to fail* on a real violation (provoked in §6, not quoted from the gate's self-test) | "a control that cannot fail is a formality" (AO-GR-4, `docs/QA-GATE.md`) |
| **DECLARED-ONLY** | a doc/rule asserts it, no check binds it, and nothing reads the doc | "a doc-only rule is advisory" (`docs/GOLDEN-RULES.md`) |
| **IMPLEMENTED-UNGATED** | code exists that no check exercises | the class `scripts/check-gate-coverage.sh` refuses for a *script*; this doc extends it to a *capability* |
| **ABSENT** | the step the path needs exists nowhere | `docs/ENTERPRISE-WORKBOOK-GAP-ANALYSIS.md` uses MISSING for the same thing |

The measurement is **not** a citation count. `docs/CONTROL-COVERAGE.md` §1 records the trap
directly: its first draft scored a rule `GAP` on an empty `grep` that searched for the wrong
pattern, and scored another `PARTIAL` because it never looked for the mechanism. Every verdict
here was reached by reading the binding gate and **running it**, then provoking the ones claimed
ENFORCED.

## 2. Coverage matrix — app model / registry lifecycle

| Surface | Declared (file:line) | Implemented (file:line) | Binding check + emitted label | E2E exercise | Verdict |
|---|---|---|---|---|---|
| OS-app exemption (`os_apps`) | `module.json:7` (`"os_apps": {`); `docs/MODULE-ADMISSION.md:203` | `module.json:7-12`; scanner `scripts/check-system-app-declaration.sh:171` (`evaluate`) | `scripts/check-system-app-declaration.sh` → `FAIL  the tree carries a addons/ directory` (`:250`) / `: NOT-OK — N finding(s)` (`:455`) | the check's own 5 provoked controls (`:377-431`) **plus** the external provocation in §6.1 | **ENFORCED** |
| App declaration inside a **non-JSON/YAML** file | `docs/MODULE-ADMISSION.md:206` ("no `category: \"system\"` app anywhere outside `vendor/`") | `scripts/check-system-app-declaration.sh:157` (`if not name.endswith((".json", ".yaml", ".yml"))`) | the same check — but the extension filter means the claim is only observable for three extensions | **none** — no case declares an app in any other language | **IMPLEMENTED-UNGATED** (see §5, §6.2, filed as #1161) |
| Sub-module admission | `docs/MODULE-ADMISSION.md:88` ("valid against `cmr.module/v1`") | `module.json` `submodules[]`; `governance/modules/` | `scripts/check-module-admission.sh` (reads `module.json`, `--verify-peers`/`--peer-facts`) | `docs/MODULE-ADMISSION.md` §6–§8 recorded peer facts; the gate's own provoked refusals | **ENFORCED** |
| Module registry (hub catalog ↔ tree) | `docs/MODULE-REGISTRY.md` | `governance/modules/**` (`registry.py`, `cli.py build`) | `scripts/check-module-registry.sh` | `governance/modules/tests` (declared; needs `vendor/CMR`) | **ENFORCED** (offline = rc 2 CANNOT-ASSESS without the submodule — honest, not a pass) |
| Module brief | `docs/MODULE-BRIEF.md:96` | `governance/modules/health.py` (named by the brief) | `scripts/check-module-brief.sh` | the brief's own health rows | **ENFORCED** (same `vendor/CMR` caveat) |
| `architecture.yaml` declaration | `architecture.yaml:36` (`generator:`) | `architecture.yaml` (whole file) | `scripts/check-diagrams-declaration.sh` | the declaration gate resolves the declared entrypoint/paths | **ENFORCED** |
| `gdc-manifest.yaml` mandatory pins | `gdc-manifest.yaml:33` (`modules:`) | `gdc-manifest.yaml` | `scripts/check-diagrams-declaration.sh` (fails by name on a dropped/moved pin) | pinned-rev parity in `scripts/check-shared-frontend-onboarding.sh` | **ENFORCED** |
| `registry/` pillar | `registry/README.md`; [`docs/SURFACE-CLASS.md`](SURFACE-CLASS.md) row | `registry/**` (profiles, personas, packs, events, service, prompts, chat, sync, parity) | `make surface-class` → `scripts/check-surface-class.sh`; per-package suites in `scripts/pytest-suites.txt` | `registry/*/tests` (declared; most swept-only, baselined #524) | **ENFORCED** |
| This repo's **own** `module.json` against `cmr.module/v1` | `docs/MODULE-ADMISSION.md:30` (a governing repo *publishes* one) | there is no validator for the root manifest anywhere in `scripts/` (the checks only read fields) | **none** | **none** | **ABSENT** (see §4; filed as #1163) |

## 3. Coverage matrix — control-plane system apps

| Surface | Declared (file:line) | Implemented (file:line) | Binding check + emitted label | E2E exercise | Verdict |
|---|---|---|---|---|---|
| Cockpit function registry | `control-plane/functions/functions.yaml` (header) | `functions.yaml` + `control-plane/functions/cockpit_registry.py` | `scripts/check-control-functions.sh` | `control-plane/functions/tests` (declared) | **ENFORCED** |
| Control verbs (RC-2) | `control-plane/control/verbs.yaml` (header) | `verbs.yaml` + `control-plane/control/cli.py validate` | `scripts/check-control-verbs.sh` (`SOURCES` floor assertion) | `control-plane/control/tests` (declared) | **ENFORCED** |
| Control audit | `control-plane/control/verbs.yaml` (`audit:`) | `control-plane/control/cli.py` | `scripts/check-control-audit.sh` | its provoked refusals | **ENFORCED** |
| Control coverage (AO-GR spine map) | `docs/CONTROL-COVERAGE.md` | `scripts/control-coverage.tsv` | `scripts/check-control-coverage.sh` | the map resolves each rule to a control *kind* | **ENFORCED** |
| Terminal cockpit **client** | `control-plane/cockpit/README.md` | `control-plane/cockpit/cockpit/` (the client) | `make cockpit:690` *runs* it; **no** `scripts/check-cockpit.sh` exists | `control-plane/cockpit/tests` — **not** in `scripts/pytest-suites.txt` | **IMPLEMENTED-UNGATED** (see §5; filed as #1162) |
| Cockpit rendered frame (the panels the client draws) | `fleet/console.py` (the sections) | `control-plane/functions/cockpit_render.py:90` | `scripts/check-control-functions.sh` (renders headlessly, section by section) | rendered-frame assertions | **ENFORCED** |

## 4. Coverage matrix — governance path

| Surface | Declared (file:line) | Implemented (file:line) | Binding check + emitted label | E2E exercise | Verdict |
|---|---|---|---|---|---|
| Claim-time issue order | `AGENTS.md` rule 14; `governance/dispatch/README.md:1` | `governance/dispatch/{order,claims,policy}.py` | `check-chronological-dispatch.sh`, `check-issue-claims.sh`, `check-dispatch-queue.sh`, `check-epic-focus.sh`, `check-capacity-gate.sh` | `governance/dispatch/tests` (declared) | **ENFORCED** |
| End-to-end closure | `AGENTS.md` rule 16 | `governance/lifecycle/**` | `scripts/check-github-lifecycle.sh` | `governance/lifecycle/tests` | **ENFORCED** |
| Orphan reconciliation | `AGENTS.md` rule 17 | `governance/reconcile/**` | `scripts/check-reconcile.sh` | `governance/reconcile/tests` | **ENFORCED** |
| Lane isolation | `AGENTS.md` rule 15 | `governance/isolation/**` | `scripts/check-session-isolation.sh` | `governance/isolation/tests` | **ENFORCED** |
| Lessons / RCA ledger | `governance/lessons/rca-template.md` | `governance/lessons/**` | `scripts/check-lessons.sh` (+ the board gate re-runs it) | `governance/lessons/tests` | **ENFORCED** |
| Conformance (class/pattern/template) | `governance/conformance/policy.yaml:1` | `governance/conformance/checker.py` | `scripts/check-conformance.sh` | `governance/conformance/tests` | **ENFORCED** |
| **Filing default derives a label that does not exist** | `governance/conformance/policy.yaml:84` (`area: governance`) | `governance/conformance/filing.py:161` (`derive_declaring_labels`) | `scripts/check-conformance.sh:52` runs `filing-check`, which proves *derivation* only — never that the derived label *exists on the repo* | `governance/conformance/tests` re-derives the same label set | **IMPLEMENTED-UNGATED** (measured live in §6.3; filed as #1160) |
| Surface class ladder | `governance/conformance/surfaces.yaml` | `governance/conformance/surfaces.py` | `make surface-class` → `scripts/check-surface-class.sh` | the rung's negative control (raises a class in a scratch copy) | **ENFORCED** |
| `docs/SURFACE-CLASS.md` **content** | `docs/SURFACE-CLASS.md:36` (the `lifecycle` row) | n/a — the enforcement point is `surfaces.yaml`, not the doc | **none** — the doc's only occurrence in a gate is the docs-index *quarantine list* (`scripts/check-docs.sh:274`) | **none** — corrupting a doc row leaves `make surface-class` at rc 0 (measured #620) | **DECLARED-ONLY** |
| `docs/AUTHORITY-MODEL.md` **content** | `docs/AUTHORITY-MODEL.md` | `governance/authority/matrix.yaml` + `cli.py` | **none** — `scripts/check-authority.sh` reads `governance/authority/controls.yaml` and `cli.py` (`:45`), never the doc (0 refs) | `governance/authority/tests` | **DECLARED-ONLY** |
| Ticket projection | `governance/ticket/README.md` | `governance/ticket/{builder,sources}.py` | `scripts/check-ticket-projection.sh` | `governance/ticket/tests` | **ENFORCED** |
| PMO derived views | `docs/BOARD-ATTACK-PLAN.md` | `governance/pmo/{graph,views}.py` | `scripts/check-pmo-rollup.sh` (7 controls) | `governance/pmo/tests` | **ENFORCED** |
| Authority matrix | `docs/AUTHORITY-MODEL.md` | `governance/authority/**` | `scripts/check-authority.sh` | `governance/authority/tests` | **ENFORCED** |
| Board gate | `governance/board/CHARTER.md` | `governance/board/{gate,boundary}.py` | `scripts/check-board-gate.sh` | `governance/board/reviews` | **ENFORCED** |
| Spawn envelope | `governance/spawn/README.md` | `governance/spawn/render.py` | `scripts/check-spawn-envelope.sh` | `governance/spawn/tests` | **ENFORCED** |
| **Duplicate / canonical-copy (ADR-0010)** | `governance/dupcheck/README.md:1` | `governance/dupcheck/check-duplicates.sh` (3 real subcommands) | **none** — invoked by no gate-invocation file (`grep -rn 'dupcheck' scripts/ Makefile` → empty) | **none** | **IMPLEMENTED-UNGATED** (see §5; filed as #1164) |
| **Independent-review merge machine** | `docs/CONTROL-COVERAGE.md` (AO-GR-13 row) | `governance/merge/{engine,gate,reviewer}.py` | **no** `scripts/check-merge.sh`; the suite is **swept-only** (baselined `#524`), i.e. reached by `make gate`/`make tests`, **not** by the gate of record `make verify` | `governance/merge/tests` | **IMPLEMENTED-UNGATED** (w.r.t. the gate of record) |
| Waves | `governance/waves/` | `governance/waves/{cli,ledger,pins}.py` | named a pytest target by `scripts/check-ungated-suites.sh:207` (issue #891) | `governance/waves/tests` (30 tests) | **ENFORCED** |
| Gate of record (composite) | `scripts/verify.sh:84` (`checks=(`) | 158 `scripts/check-*.sh` auto-discovered (`scripts/discover-checks.sh`) | `make verify` | `.verify/attestation.json` | **ENFORCED** |
| Ungated-artifact detector | `scripts/gate-coverage-baseline.txt:1` | `scripts/check-gate-coverage.sh` | `scripts/verify.sh` entry `gate-coverage` | its own both-directions baseline check | **ENFORCED** |
| Docs index membership | `docs/README.md` | `scripts/check-docs.sh:217-320` (§5) | `make verify` entry `docs-lint`; emits `FAIL  docs/<name> (tracked doc not indexed in docs/README.md)` | provoked in §6.4 against the real new doc | **ENFORCED** |
| Capstone E2E suite | `scripts/pytest-suites.txt` (`e2e`) | `e2e/**` (18 modules + `e2e/tests`) | `scripts/verify.sh:471` names `e2e/tests` as a pytest target | the suite itself | **ENFORCED** — but nothing in it spans the system-app/app-model path (§7, break-place B2) |

### 4.1 Verdict counts

| Verdict | Count |
|---|---|
| **ENFORCED** | **29** |
| **IMPLEMENTED-UNGATED** | **5** ← the app-declaration breadth row, the cockpit client, the filing-default label, `governance/dupcheck`, `governance/merge` |
| **DECLARED-ONLY** | **2** ← `docs/SURFACE-CLASS.md` content, `docs/AUTHORITY-MODEL.md` content |
| **ABSENT** | **1** ← validation of this repo's own `module.json` against `cmr.module/v1` |

### 4.2 The five worst gaps, by name

1. **The default filing path derives a label that does not exist** (`policy.yaml:84` → `area:governance`). Measured: the seam handed `gh issue create` the label set and GitHub refused with `could not add label: 'area:governance' not found`; `filing-check` is green because it proves *derivation*, not *existence*. **Every defaulted filing fails**, and the gate that owns the seam cannot see it. (#1160)
2. **The ADR-0010 no-fork rule is enforced by nothing that runs** (`governance/dupcheck/check-duplicates.sh`). The README's own words: *"This helper makes that rule mechanical."* No gate-invocation file names it, so the rule is advisory. (#1164)
3. **The system-app detector is blind to any declaration outside `.json/.yaml/.yml`** (`check-system-app-declaration.sh:157`). The gate claims *"the tree matches the claim"*; a `category: "system"` in a `.ts` file — the exact shape the OS host itself uses (`shared-frontend` `shell/src/addons.ts`) — is invisible. Proven in §6.2. (#1161)
4. **The independent-review merge machine is not run by the gate of record.** `governance/merge` is the state machine AO-GR-13 depends on, yet its suite is swept-only (baselined #524) and no `check-merge.sh` exists.
5. **This repo's own `module.json` is validated by nothing.** `docs/MODULE-ADMISSION.md:88` requires a child's manifest be *valid against `cmr.module/v1`*; every check here only *reads fields* out of the root manifest. (#1163)

## 5. The inert-guard list

A guard is **inert** when its pass and fail paths collapse to one exit code, when it cannot observe
the thing it claims to, or when it is never reached by the gate of record. Measured here:

| Guard | Why it is inert | Evidence |
|---|---|---|
| `governance/dupcheck/check-duplicates.sh` | implemented (3 subcommands, tri-state) and **invoked by nothing** — not `scripts/verify.sh`, not `Makefile`, not `gate.sh`/`merge-gate.sh`, not any `check-*.sh`; it is inside a package, so `scripts/discover-checks.sh` (which globs `scripts/check-*.sh`) does not auto-wire it either | `grep -rn 'dupcheck' scripts/ Makefile` → empty |
| `scripts/check-system-app-declaration.sh`'s tree scan | cannot observe a `category: "system"` declaration outside `.json/.yaml/.yml` (`:157`), so the `OK the tree matches the claim` line can print while an app declaration exists in another file type | §6.2: `.ts` plant → rc 0; identical `.yaml` plant → rc 1 |
| `filing-check` (the conformance gate's filing half) | proves the label *derivation* and the two refusal halves, but never that the derived labels exist on the repo — so the gate is green while the production filing path is refused by GitHub | §6.3: `filing-check: OK (13 of 13)` **and** `gh` refuses `area:governance` |
| `scripts/check-drift.sh` | **denylisted** from the gate of record (`scripts/check-denylist.txt:18`), and its own history (recorded in `scripts/check-ungated-suites.sh:20-26`) is that its only signal on the ungated-suite class was a `stderr` WARN under rc 0 | `scripts/check-denylist.txt:18`; `check-ungated-suites.sh:20-26` |
| `governance/merge` suite | reached only by `make gate` / `make tests`; `scripts/check-gate-coverage.sh` **accepts** a declared-but-swept-only suite by design (74 baselined), so nothing fails | `scripts/gate-coverage-baseline.txt` (`suite governance/merge swept-only #524`) |

Two controls that are **not** inert, recorded so the list is not read as blanket distrust:

- `scripts/check-system-app-declaration.sh` *itself* is a positive model: it stages a scratch copy of
  exactly the inputs it read and provokes five refusals through the same code path each run (`:377-431`).
- `scripts/check-gate-coverage.sh` checks its baseline in **both** directions, so an entry cannot rot
  into a permanent excuse.

## 6. Provocations — the ENFORCED verdicts, earned

Each provocation plants a **real violation outside the repo** (a scratch tree), runs the real gate,
and asserts **rc and the literal refusal string**. The gate's own self-test is deliberately *not*
the evidence.

### 6.1 `check-system-app-declaration.sh` — `addons/` + a `category: "system"` app

```text
$ bash scripts/check-system-app-declaration.sh --no-controls --root /tmp/aosysapp-prov1/root
  OK    the tree matches the claim: no addons/, no apps/, no category: "system" app outside vendor/
check-system-app-declaration: OK — ...                                        control_rc=0
# plant: addons/some-app/  +  drift-app.yaml (category: "system")
$ bash scripts/check-system-app-declaration.sh --no-controls --root /tmp/aosysapp-prov1/root
  FAIL  the tree carries a addons/ directory at the repository root, so this repo now hosts OS apps — the exemption no longer holds (it is declared in docs/MODULE-ADMISSION.md)
  FAIL  an OS app is declared (category: "system") at drift-app.yaml:2, so this repo now hosts OS apps — the exemption no longer holds
check-system-app-declaration: NOT-OK — 2 finding(s); the declaration and the tree disagree
provoked_rc=1
```

**rc 1**, two refusals, each naming the violation; the clean control is **rc 0**.

### 6.2 `check-system-app-declaration.sh` — the same declaration in a non-JSON/YAML file (the gap)

```text
# plant the SAME declaration shape as a .ts file (the shape shared-frontend uses for shell/src/addons.ts)
$ cat /tmp/aosysapp-prov5/root/addon-registry.ts
export const app = { id: "sys-monitor", category: "system" };
$ bash scripts/check-system-app-declaration.sh --no-controls --root /tmp/aosysapp-prov5/root
  OK    the tree matches the claim: no addons/, no apps/, no category: "system" app outside vendor/
check-system-app-declaration: OK — ...                                        ts_plant_rc=0

# control: the SAME declaration in a .yaml file
$ bash scripts/check-system-app-declaration.sh --no-controls --root /tmp/aosysapp-prov5/root
  FAIL  an OS app is declared (category: "system") at addon-registry.yaml:2, so this repo now hosts OS apps — the exemption no longer holds
yaml_plant_rc=1
```

**rc 0 for `.ts`, rc 1 for `.yaml`** — same declaration, different extension. This is the measured
basis for gap #3 in §4.2 and the filed issue **#1161**.

### 6.3 The filing seam — a derived label that does not exist (live, on the real repo)

```text
$ python3 governance/conformance/cli.py file --title ... --declare area=governance ...
RuntimeError: gh issue create failed: could not add label: 'area:governance' not found
rc=1
$ gh label list --repo kushin77/agent-orchestrator | grep -i '^area'
area:knowledge-index  area:standards  area:incident-response  area:remediation
area:board  area:lessons  area:agent-profiles  area:cto-overlay  area:fleet
area:session-fleet  area:enterprise-workbook  area:erp-module      # NO area:governance

# meanwhile the gate that owns the seam is green:
$ bash scripts/check-conformance.sh
filing-check: OK (13 of 13 expectations held)

# and the seam's OWN refusal (a declaring name the policy does not recognise) works and is offline:
$ python3 governance/conformance/cli.py file --title probe --body probe --class enterprise --declare bogus=1 --dry-run
conformance: FILING REFUSED — does not recognise `bogus:` as a declaring label — this policy declares
class, type, priority, area, gdc, pillar, phase, source. Pass a label the policy does not declare with
`--label` instead; it will not be dropped here. Nothing was filed: ...
RC=1
```

**rc 1 with the literal GitHub refusal**, on a real filing; the exact same call succeeds with an
existing area label (`area:board`). Filed as **#1160**.

### 6.4 `check-docs.sh` — docs-index membership, against this very document

The strongest E2E proof for deliverable #4 is the real artifact: the new doc was staged as a
tracked file **before** it was indexed, and the gate refused it by name.

```text
$ git add docs/SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md
$ bash scripts/check-docs.sh
...
== docs index completeness ==
  FAIL  docs/SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md (tracked doc not indexed in docs/README.md)
docs index completeness: 1 problem(s)
docs: 1 problem(s)
rc=1

# after adding the index row to docs/README.md:
$ bash scripts/check-docs.sh
...
== docs index completeness ==
docs index completeness: OK
docs: OK
rc=0
```

`scripts/check-docs.sh` supports no `--root` flag and tests *tracked* `docs/*.md`, so the plant is
the real doc, staged in the lane worktree — the same artifact this PR lands. **rc 1 with the literal
refusal**, then **rc 0** once indexed. This is the E2E proof for deliverable #4 as well as the
"Docs index membership" row in §4.

## 7. Break-places — where the E2E path is discontinuous

| # | Break-place | Where the hand-off is | Why it is untested |
|---|---|---|---|
| B1 | **Declared exemption → the OS host that owns the model.** `module.json` `os_apps.model_owner` names `kushin77/shared-frontend`; the `rg`-style check verifies the *string*, but nothing verifies the owner repo still declares `registry/modules.json` with `mount.type: native` | `docs/MODULE-ADMISSION.md:219` → `shared-frontend` `registry/modules.json` | `grep -ln 'registry/modules.json' scripts/check-*.sh` → **empty**. The one cross-repo contract in the app model has no spanning test |
| B2 | **System-app/app-model → the capstone `e2e` suite.** The `e2e` suite is named by the gate of record (`scripts/verify.sh:471`) and exercises the golden path, FinOps, chat, ERP and go-live — but **no** `e2e/tests/test_*.py` touches `module.json`, `os_apps`, or the system-app declaration | `scripts/pytest-suites.txt` (`e2e`) → `e2e/tests/` | the gate is unit-level; the E2E path has no case for this surface |
| B3 | **Filing seam → the board's label vocabulary.** `policy.yaml` derives labels; the GitHub repo's label set is the other half, and nothing binds them | `governance/conformance/filing.py:161` → the live repo's labels | measured live in §6.3: they disagree, and no check spans the gap |
| B4 | **`governance/dupcheck` → a gate.** The detector exists and the ADR says the rule is mechanical, but the wiring step never happened | `governance/dupcheck/check-duplicates.sh` → `scripts/check-*.sh` / `Makefile` | a package-local `check-*.sh` is outside `scripts/discover-checks.sh`'s glob, so it is not even "an unwired script" to `check-gate-coverage.sh` |

## 8. Filed issues

Filed through `governance/conformance/filing.py` (declaring labels derived from `policy.yaml`; see
§6.3 for why the *default* area had to be overridden to file at all):

| Issue | Title | Verdict class |
|---|---|---|
| [#1160](https://github.com/kushin77/agent-orchestrator/issues/1160) | the filing default derives `area:governance`, which does not exist on the repo — every defaulted filing is refused by GitHub while `filing-check` stays green | IMPLEMENTED-UNGATED / inert guard |
| [#1161](https://github.com/kushin77/agent-orchestrator/issues/1161) | `check-system-app-declaration.sh` observes `category: "system"` only in `.json/.yaml/.yml`, so its "the tree matches the claim" line cannot see a `.ts` declaration | IMPLEMENTED-UNGATED / narrow observation |
| [#1162](https://github.com/kushin77/agent-orchestrator/issues/1162) | the terminal cockpit client (`control-plane/cockpit/`) is run by `make cockpit` and gated by nothing; its suite is undeclared | IMPLEMENTED-UNGATED |
| [#1163](https://github.com/kushin77/agent-orchestrator/issues/1163) | this repo's own `module.json` is never validated against `cmr.module/v1`, though the admission contract requires it of every child | ABSENT |
| [#1164](https://github.com/kushin77/agent-orchestrator/issues/1164) | `governance/dupcheck/check-duplicates.sh` implements the ADR-0010 no-fork rule and is invoked by no gate — the rule is advisory | IMPLEMENTED-UNGATED / inert guard |

Not filed, only recorded, because they are already tracked or by-design:
`governance/merge` swept-only (baselined `#524`, `docs/QA-GATE.md`), `docs/SURFACE-CLASS.md`
content + `docs/AUTHORITY-MODEL.md` content (DECLARED-ONLY but the enforcement points
`surfaces.yaml` / `governance/authority/controls.yaml` are real and gated — recorded as rows, not
as new issues), and the cross-repo `shared-frontend` contract (B1 — belongs to that repo's board,
`docs/CROSS-REPO-EXECUTION-BOUNDARY.md`).

## 9. What this analysis deliberately did NOT do

- **Did not re-file** anything already tracked: #944, #1028, #878, #946, #966, #967, #968, #935,
  #936 (open, in this area) are cited, not duplicated.
- **Did not touch `.board/snapshot.json`** and did **not** run
  `governance/dispatch/cli.py snapshot --from-github` — a second lane (`pmo-sme`) owns the board
  metadata pass. The stale-snapshot signals seen on this lane are **inherited, not attributed to
  it**: `claim --issue 1156` refused `unknown-issue` (the just-filed issue is absent from the
  committed snapshot) and `eligible` answered `CANNOT-ASSESS — snapshot-stale (126.1m > 15m)`.
- **Did not create or edit milestones, and did not add/remove labels on existing issues.**
- **Did not edit** `governance/conformance/policy.yaml`, `scripts/verify.sh`, or any gate script —
  each gap is filed as an issue so the lane that owns the fix lands it.
- **Did not count citations as coverage** (§1), and did not record a row without a real `file:line`.

## 10. Re-derive

```bash
cd "$(git rev-parse --show-toplevel)"

# the system-app surface: declared, implemented, gated
sed -n '7,12p' module.json
sed -n '200,275p' docs/MODULE-ADMISSION.md
sed -n '150,170p' scripts/check-system-app-declaration.sh     # scan_tree, incl. the extension filter
bash scripts/check-system-app-declaration.sh                  # rc 0 + 5 provoked controls

# the governance path: which check binds which package
ls scripts/check-*.sh | wc -l                                 # 158, all auto-discovered
grep -rn 'dupcheck' scripts/ Makefile                         # empty == unwired
sed -n '480,489p' Makefile                                    # surface-class runs surfaces.yaml

# the filing seam (the live half is network-gated)
sed -n '79,85p' governance/conformance/policy.yaml
bash scripts/check-conformance.sh                             # filing-check: OK (13 of 13)
gh label list --repo kushin77/agent-orchestrator | grep -i '^area'   # no area:governance

# the gate of record
make verify
```

## 11. Retrospective

The measured lesson worth keeping: **an ENFORCED gate and a binding gate are different claims, and
the second one needs the gate to be reached with an input it can actually see.** The system-app
declaration is genuinely ENFORCED — it fails, by name, on a planted `addons/` — yet the same
detector cannot observe the declaration shape the app model's own owner uses, and the conformance
gate is green while the filing path it owns is refused in production. Both are the class
`docs/CONTROL-COVERAGE.md` §1 already named: *"searching for a name instead of reading the
mechanism"*. A gate's green is only as wide as its observation and its reach.
