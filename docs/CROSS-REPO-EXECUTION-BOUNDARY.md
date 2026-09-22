# Cross-repo execution boundary

The contract that governs what a repository in the CMR fleet may remediate, and
what it must hand to another repository's board instead. It exists because
EPIC #125 was filed here and then sat inert for months: its backlog belonged to
a dozen *other* repos, so nothing in this checkout could ever move on it.

## 1. The boundary rule

**A repo remediates compliance findings for itself only.**

A finding about another repo is resolved by filing a **direction issue on that
repo's board** — never by an edit, a settings change, or a pull request authored
from here. Concretely:

- A repo may fix its own branch protection posture, its own dependency pins, its
  own guardrail files.
- A repo may **not** enable branch protection, enable Dependabot, add guardrail
  files, or change dependency pins in another repo.
- A repo may not "help" by opening a remediation PR against a peer repo. The
  peer's board owns that work; a PR from here routes around the ownership.

This is not a style preference. It is `AGENTS.md`'s Hard DON'Ts made checkable:

> **Never edit another repo's files** — direction/needs go to that repo's board.
>
> The hub never hosts spoke application code; never do a vendor or spoke's work —
> file a direction issue instead. (**NG4**)

NG4 is the naming used throughout this contract: the *hub* (or any repo in the
fleet) never hosts another repo's application code and never performs another
repo's work. The only sanctioned output of observing a peer's defect is a
direction issue on the peer's board.

## 2. The direction channel

The handover mechanism is the CMR **direction** channel.

`vendor/CMR/channels/spokes.tsv` is the single source of truth mapping each
repo to its channel and direction labels. Its columns are
`repo | role | channel | direction_labels | guard | notes | onboarded |
auto_merge`; each spoke row carries the `cmr:direction` label set that is
applied to the **target** repo when a direction issue is filed.

`vendor/CMR/channels/inbox.tsv` is the ledger of what has actually been sent.
The canonical worked example is the shared-services handover:

```
2026-09-08T00:00:00Z  out  direction  kushin77/CMR  kushin77/shared-services  CMR:ONBOARD-0010  https://github.com/kushin77/shared-services/issues/4040  sent
```

Read that row as the pattern: the hub observed a need, filed
`kushin77/shared-services` issue **4040** on the *target* repo's board, and
recorded the handover — reference `CMR:ONBOARD-0010`, status `sent` — in its own
inbox ledger. A `held(guard)` status is the other legitimate state; a guarded
row holds the direction and records it rather than auto-filing it, and is
overridden only through the channel's documented `approve` path.

**Citation rule:** `vendor/` artifacts are cited in backticks, never as markdown
links. A relative link into `vendor/` resolves on a populated checkout and
breaks the docs gate on a fresh worktree, where the submodule is empty. The
inbox row above is quoted text for the same reason — it is evidence, not a
living link.

## 3. The report-is-read-only rule

The CMR compliance reports are **generated, read-only snapshots**. They are
output, not a control surface.

- A `disabled` value in a report is a **finding about the owning repo**. It is
  not an instruction to this repo, and it is not an action this repo may take.
  The remediation belongs to the owning repo's board (see §1 and §2).
- `Makefile`-driven behaviour, Terraform declarations, and repository settings
  are all declared in code. "Fix it by clicking settings" is forbidden: infra
  lands as a PR, and new infra ships enabled by default once merged and tested
  (AO-GR-6). There is no exception for a
  compliance report that would go green if someone clicked a toggle.
- An org-level signal that reads **`unknown (HTTP 404)`** is **CANNOT-ASSESS**.
  It is not a pass, not a failure, and not evidence of compliance — it means the
  API surface required to assess the signal was unavailable to the toolchain.
  CANNOT-ASSESS must never be reported as a pass, and never aggregated into one.

Measured context for this rule: the vendored hygiene report shows branch
protection and Dependabot `disabled` **fleet-wide** — every repo including the
hub — and the PVR signal reads `unknown (HTTP 404)`. Under §1, none of that is
actionable from this repo; under §3, it is not a green either.

## 4. The observability requirement

**A signal that is not measurable here must be gated here.**

Issue #125 is the proof. Its items lived entirely inside other repos' boards, so
this repo had **no local signal** for the epic at all: nothing in `make verify`,
nothing in the board snapshot, nothing that changes when a foreign repo stays
non-compliant. The measured consequence — a local drift gate reported PASS for
months while 11 of the 12 children (#126–#137, with #130 closed) were still
open. The epic did not fail; it was *unobservable*.

The requirement that follows: when a work item's payload is not measurable in
this repository, the boundary condition around it **must** be gated in this
repository. For #125 that gate is `governance/board/boundary.py`, which makes
the violation class itself the local signal. It carries three checks, each of
which can genuinely fail:

| Check | The violation it names |
|---|---|
| `self-parent` | the body marks its backlog as owned elsewhere (`Parent: #125`) |
| `foreign-repo-issue` | the body references a foreign repo in a `Closes` / `Refs` / `Parent:` form |
| `foreign-repo-declaration` | the body **declares** its repo with the board's own `## Repo` convention and that repo is not this one |

### 4.1 The declaration check, and the false green it closes

`foreign-repo-declaration` is the signal that reads the boundary the way the
board actually writes it. Every child of #125 declares its repo in the board's
own convention — a `## Repo` heading (also accepted: a `Repo:` / `**Repo**:` /
`**Repo**` label, case-insensitively, with the value on the same line after a
colon or on the next non-blank line, bare name or `owner/name`, backticks and
quotes stripped) — and a declaration naming any repo other than this one is a
cross-repo backlog item filed on the wrong board.

**Measured verdict (2026-09-13, live board).** The check flags **11 open
children**, each naming its foreign repo: #126 `saas-rbac`, #127
`github-workflow`, #128 `shared-temporal`, #129 `Shared_Integrations`, #131
`shared-governance`, #132 `shared-services`, #133 `googleworkspace`, #134
`SharedFeatures`, #135 `ERP-CRM`, #136 `code-indexing`, #137 `diagrams`. **#130
(`shared-frontend`) is closed and is deliberately not a finding** — a closed
child is resolved history, not a live violation — and #125 itself declares no
repo and is never flagged. The count is pinned by a committed fixture of the
real issues, `governance/board/tests/fixtures/board-125-children.json`.

**State rule — fail-closed.** The declaration check flags only *open* issues.
Only a literal `closed` state (case-insensitive) suppresses a finding, so a
snapshot that omits `state` can never turn a real violation into a silent pass.
That is the defect this check fixes, measured: the first version of this
detector reported

```
boundary: OK — 13 issue(s), no boundary violation
```

for the real board when it was pointed at this repo's own board-export shape
(`number` / `title` / `state` — no body field), which is the shape a local gate
would actually read from the committed board snapshot. The two original checks
had nothing to read, so a missing field produced a pass. The declaration check
closes that class: it needs no legacy marker (the `Parent: #125` line survives
on only part of the backlog — #130 already dropped it), it names the foreign
repo in the finding, and it distinguishes a live violation from resolved
history.

The detector is tri-state on the same honesty contract as every other gate here:
`0` OK, `1` NOT-OK (findings), `2` CANNOT-ASSESS (snapshot missing, unreadable,
or empty). CANNOT-ASSESS never exits `0`.

## 5. The closure doctrine

**A cross-repo epic closes only when every child is terminal — or is explicitly
quarantined by name with the issue that tracks it.**

Two consequences follow, and both are load-bearing:

1. **Never close on an unachievable gate.** #125's stated completion gate is
   "*the CMR hygiene report is green*". That gate cannot be achieved from this
   repo: it requires actions in a dozen repos this repo may not edit (§1), and
   enabling branch protection or Dependabot anywhere is forbidden outright. A
   gate that cannot be satisfied by the work available is not a completion
   criterion — it is a prohibited action wearing a completion criterion's
   clothes. Closing on it would be a false green.
2. **Quarantine is by name.** An item that will not be closed locally is listed
   explicitly, with the tracking issue, alongside the epic. The alternative —
   closing the epic and letting the remainder evaporate — is exactly how the
   backlog went silent the first time. Quarantine shrinks as the tracked items
   close, and the shrink is observable because it is recorded.

The honest closure of #125 is therefore: freeze this boundary (§1–§4), detect
the violation class automatically (§4), keep the technical debt visible as the
quarantined children (§5), and hand the cross-repo remediation to the already
filed per-repo children — #126–#137, one direction issue per repo, with #130
closed.

## 6. Blast radius

A boundary violation's blast radius is **the one wrongly-touched repo plus the
one lane that touched it**. It does not fan out.

That containment is structural, not aspirational:

- The rule is **PR-gated**. Every change lands through a pull request against
  `master`; nothing is pushed directly. A violation stops at review rather than
  propagating.
- The rule is **per-lane**. One issue equals one lane equals one branch
  (`AGENTS.md` rule 2), and no two lanes share a file in a wave, so a lane that
  oversteps cannot silently sweep a second repo along with it.
- Ownership is **disjoint**. Lanes do not edit files another lane owns, so a
  violation cannot present itself as someone else's diff.

Combine those and a boundary violation is contained by construction: it is a
single lane touching a single repo, caught at that lane's gate. It can never
become a fleet-wide sweep, because no mechanism exists by which one lane's
edits reach more than one repo — which is precisely why the direction issue
(§2), and not an edit, is the only way work crosses the boundary.

## 7. Enforcement

| Surface | Role |
|---|---|
| `governance/board/boundary.py` | The detector: `self-parent` + `foreign-repo-issue` + `foreign-repo-declaration`, tri-state exit |
| `governance/board/tests/test_boundary.py` | Behavioural proof the detector fires, including a negative control per check |
| `governance/board/tests/fixtures/board-125-children.json` | The real #125-#137 issues; pins the live verdict at exactly 11 open children |
| This document | The contract the detector enforces |

The detector is pure, offline, and stdlib-only: it reads a JSON snapshot of
already-fetched issues, never calls the network, and never runs `gh`. That keeps
it runnable inside the sandboxed gate and keeps every finding reproducible from
an archived snapshot.
