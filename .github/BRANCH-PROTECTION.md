# Branch protection — `master` (declared capture, issue #1765)

**What this is.** The live GitHub branch-protection configuration for the
`master` branch of `kushin77/agent-orchestrator`, captured here as a declared,
in-repo artifact so the repository owns the record of its own git policy. This
file is the `source_file` for the `git` domain in the policy registry
(`governance/policy/domains/git.yaml`).

**When it was read.** `2026-09-21T20:29:03Z`, against the live API.

**How it is applied.** **Manually, through the GitHub API / settings UI today —
not yet IaC.** This is the AO-GR-5 console-click exception applied knowingly: the
fleet rule is that infrastructure is declared and never clicked, and this capture
is the declaration half, while the apply half is still a manual step. Wiring this
to a Terraform/Pulumi resource (flag-gated OFF, per GR-5) is the follow-up; until
then this file records observed state, not desired state that a pipeline enforces.

**How it was read.**

```bash
gh api repos/kushin77/agent-orchestrator/branches/master
gh api repos/kushin77/agent-orchestrator/branches/master/protection
```

## Observed state (verbatim)

`gh api repos/kushin77/agent-orchestrator/branches/master` reported:

| field | value |
|---|---|
| `protected` | `true` |
| `commit.sha` | `0979df35d27021dc4371871eaad187ba05f5a00e` (the `master` tip at read time) |

`gh api repos/kushin77/agent-orchestrator/branches/master/protection` returned a
`200` with a body containing exactly these keys:

| setting | value | meaning |
|---|---|---|
| `required_linear_history.enabled` | `true` | only linear history lands — squash/rebase merges, no merge commits |
| `allow_force_pushes.enabled` | `false` | force-push to `master` is blocked |
| `allow_deletions.enabled` | `false` | `master` cannot be deleted |
| `block_creations.enabled` | `false` | creating a same-named branch is not blocked |
| `enforce_admins.enabled` | `false` | admins bypass the rules above |
| `required_signatures.enabled` | `false` | signed commits are not required |
| `required_conversation_resolution.enabled` | `false` | unresolved review threads do not block merge |
| `lock_branch.enabled` | `false` | the branch is not read-only |
| `allow_fork_syncing.enabled` | `false` | fork syncing is not enabled |

## What is NOT set — recorded honestly

The `protection` object contained **no `required_pull_request_reviews` key** and
**no `required_status_checks` key**. Two consequences are worth stating plainly
because they are easy to assume the other way:

1. **PR-gated `master` is a convention, not a server-side enforcement.** GitHub
   does not require a pull request to push to `master`, and does not require any
   review approval. The "every change lands via a PR; never push straight to
   `master`" rule is carried by `AGENTS.md` (GR-4), by `.github/CODEOWNERS`, and
   by the discipline of the agents working here — **not** by branch protection.
2. **No status check is required to merge.** `make verify` being green is the
   evidence of record (AO-GR-3/AO-GR-11) enforced by the review/merge process and
   the `check-squash-message.sh` gate, not by a required GitHub check. This
   repository has no GitHub Actions (GR-15), so there is no Actions-produced
   required check to name.

This capture invents no setting. Where the API returned nothing for a control,
this file says so rather than describing a state that was not observed.

## The git policy, and where it actually bites

Branch protection is one of four sources of git policy in this repository; they
are cross-referenced here and from `governance/policy/domains/git.yaml`:

1. **This file** — the live branch-protection state above (server-side).
2. **`docs/GOLDEN-RULES.md`** — the prose spine: AO-GR-3 (verify before done),
   AO-GR-8 (SemVer is law), AO-GR-9 (guardrails on every governed surface),
   AO-GR-11 (merge governance: evidence-gated; no direct push to `master`).
3. **`.github/CODEOWNERS`** — the declared reviewer-ownership map (one rule per
   pillar/cross-cutting directory), held to the tree by
   `scripts/check-codeowners.sh`.
4. **`scripts/check-squash-message.sh`** — the composed-message gate whose
   hardcoded trailer regex (`ref_re`, matching `Refs <slug>#<n>`) and its
   trailing-trailer-block rule enforce that a squash merge carries the ticket
   reference; it delegates the commit-level predicate to
   `governance/isolation/trailer.py`.

Local, commit-time enforcement lives in `.pre-commit-config.yaml` (trailing
whitespace, YAML/JSON parse, merge-conflict, secret detection). There is **no
pre-push hook installed** today — the configuration is pre-commit stage only.
