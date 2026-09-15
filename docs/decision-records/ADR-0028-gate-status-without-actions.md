# ADR-0028 — A code-native gate becomes a *required* protection via a commit status, with Actions banned

**Status:** Accepted
**Date:** 2026-09-15
**Issues:** #803 (P0-2), #807, #808
**Supersedes:** nothing
**Numbering note:** `0028` was free at claim time — highest file was `0027`; a tree-wide grep
(excl. `vendor/`, `.research/`, `.board/`) found **zero** citations of `0028`, and no open
issue or PR claimed it. `0029` and `0030` *are* cited (in `ADR-0023` and `ADR-0025` prose), which is
why this record takes `0028` and not the next number in sequence.

---

## Context

`make verify` is this repository's **gate of record** — 128 checks, provably able to fail, with
`gate-coverage` refusing an unwired check by name. It is genuinely good.

And nothing on the platform required anyone to run it. Measured 2026-09-15:

```
gh api repos/kushin77/agent-orchestrator/branches/master/protection
{"message":"Branch not protected", ..., "status":404}
```

`AGENTS.md` claimed `master` was "protected by convention" and that branch protection as code
"ships with issue #6". Neither was true. #807 / PR #808 fixed the branch protection itself
(linear history, no force-push, no deletion) and made it **declared and read-back verified**. It
deliberately did **not** require a status check, and recorded why:

> a required check needs a check-run; a check-run needs an App or Actions; **Actions is banned by
> GR-15**.

That is the tension this record resolves. A required status check is the only mechanism GitHub
offers that makes a merge *impossible* without green evidence, and the fleet's operating model
(GR-15, no GitHub Actions) appears to have ruled out the only way to produce one.

## Decision

**The gate of record is published as a GitHub _commit status_ posted by the code-native runner, and
that status context is what branch protection requires.**

Mechanism, proven empirically before this record was written:

```
POST /repos/kushin77/agent-orchestrator/statuses/<sha>
  state=success context="ao/gate-probe"
→ {"id":54169930500, "context":"ao/gate-probe", "state":"success"}

GET /repos/.../commits/<sha>/status
→ {"state":"success", "statuses":[{"context":"ao/gate-probe","state":"success"}]}
```

with the existing token scope (`repo`) and **no Actions, no App, no check-run**. The context name
is `ao/gate-of-record`; the states are the gate's own tri-state, mapped honestly:

| gate outcome | status posted |
|---|---|
| `verify: PASS` (exit 0) | `success` |
| `verify: FAIL` (exit 1) | `failure` |
| **cannot assess** (exit 2 — no python3, unreadable snapshot, a skipped-by-construction check) | `error` |
| the run starts, before any verdict | `pending` |

`error` is not cosmetic. Folding "I could not check" into `success` is the #739 defect class (an
unreadable HEAD that read back as `healthy`), and folding it into `failure` would train the operator
to ignore a red gate. Distinguishable states are the point.

## Options considered

**A. GitHub Actions running the gate.** *Rejected — banned.* GR-15: automation is code-native `make`
targets run by the ops runner and cron. Adopting it would also re-introduce the very dependency
this fleet removed, and Actions is unavailable to a repo whose automation is deliberately
runner-driven.

**B. A GitHub App posting check-runs.** *Viable, not chosen.* `controller/app-token.sh` mints an
installation token, and check-runs give a richer UI (annotations, re-run). But it needs a
`checks:write` permission on the App, a JWT-minting dance, and an App installation per repo — three
moving parts where a single authenticated `POST` suffices. **Re-open this if** per-line annotations
or re-run-from-UI become a requirement; the poster's seam (`scripts/gate-status.sh`) is deliberately
the only thing that would change.

**C. Commit status via the API (chosen).** One authenticated POST, existing scope, works from cron,
auditable in the same place every other check appears. The tradeoff is that a commit status carries
only a `description`, not structured annotations — accepted.

**D. Required PR + linear history only (the status quo after #808).** *Rejected as insufficient.* It
makes a merge **reviewable** but not **gated**: an agent with a token can still merge a red commit.
It is exactly the shape #803 was filed about — a control that is declared and not enforced.

## Consequences

**The deadlock risk is real and is the reason this ships in two steps.** If
`required_status_checks.contexts` names a context that nothing posts, **every merge in the
repository is blocked**, including the fix. Therefore:

1. the poster and its gate land **first**, and are proven to post correctly (this change);
2. declaring the context in `governance/platform/branch-protection.yaml` is a **separate,
   deliberate step**, taken only once a real status has been observed on a real commit.

That ordering is not caution — it is the whole point. #724 is this repository's own precedent: an
admission control that shipped and was **inert** because nobody applied it, with its own gate unable
to see that. A required check that cannot be produced is worse than an absent one, because it is
indistinguishable from a red gate.

**The fleet merge path must integrate.** `gh pr merge --squash` is how every lane lands work. Once
the context is required, a lane whose head commit carries no `ao/gate-of-record` status **cannot
merge**, and will look like a broken PR rather than a missing poster. The runbook must name this,
and the failure must be legible ("required status missing for <sha>") rather than an opaque refusal.

**A fail-closed poster can wedge the queue.** If the runner stops posting, every PR stalls. The
operator escape hatch is `allow_deletions`-adjacent: the protection is declared, so
`bash scripts/branch-protection.sh apply` can be re-run with `required_status_checks` removed, and
the record of that decision lives in the policy file rather than in someone's memory. That escape
must be documented in the runbook, not discovered during an incident.

**Repo-visible side effect, disclosed.** Proving this mechanism posted a real `ao/gate-probe`
`success` status onto `master`'s head commit. It is inert (no protection requires that context) and
cannot be deleted — GitHub exposes no delete for commit statuses. It is recorded here rather than
quietly left for someone to find.

## Verification

The decision is verifiable, not asserted:

- `scripts/gate-status.sh post` posts the mapped state; `show` reads the combined status **back**.
- `scripts/check-gate-status.sh` **provokes** the mapping offline: each of PASS/FAIL/CANNOT-ASSESS
  must map to success/failure/error, and an *unknown* outcome must be refused rather than defaulted.
  Asserting only the success path would pass a mapper that always returns `success`.
- A **dry-run mode** proves the poster builds the right request without writing to the repository,
  so the gate is runnable in `make verify` without mutating GitHub state on every run.

## Follow-up

- Declare the context in `governance/platform/branch-protection.yaml` once a real status is observed.
- Name the fallback git-checkout instruction in the runbook, since a failed poster must not look
  like a failed gate (#803 P0-1's remedy text already says "`gh` unavailable" — extend it to
  "required status absent").
