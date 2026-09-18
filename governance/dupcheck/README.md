# governance/dupcheck — canonical-copy duplicate detection

Automated duplicate detection for the canonical fleet docs this product
**consumes but must never fork** ([ADR-0010](../../docs/decision-records/ADR-0010-canonical-copy-ownership.md),
[decision-spike report](../../docs/spikes/47-canonical-copy-ownership.md)).

The detector is now **`scripts/check-duplicates.sh`** and it is **wired into the
gate of record** (issue #1164). This directory documents the rule and hosts only a
forwarding stub at the old path; it carries no second implementation of it.

## Canonical homes (single source of truth per doc)

| Doc set | Canonical home | Mirrors / consumers (reference-with-provenance, never fork) |
|---|---|---|
| `MODEL-PROFILES.md` · `SME-PROFILES.md` · `SOLUTION-CLASSES.md` (the fleet-standard trio) | `kushin77/CMR` `docs/` | `shared-frontend` `docs/` (origin lineage) · `code-indexing` `docs/` (consumer) |
| `agent-identity.md` + identity schema set (`agent-identity-jwt`, `agent-action`, `agent-oidc-config`, `agent-task`) | `kushin77/shared-governance` `GLOBAL_STANDARDS/` | Reserved pillar ADRs ADR-0002/ADR-0007 (adopt by reference) |
| `kushin77/dprs` (whole tree) | `kushin77/git-rca-workspace` | `dprs` is a **byte-identical** duplicate — never double-count assets |

## The gate (this is what runs)

One implementation of the rule, and it lives where a gate can see it:
`scripts/discover-checks.sh` auto-wires every `scripts/check-*.sh` into
`make verify` (#698), so `scripts/check-duplicates.sh` runs as the `duplicates`
check and `scripts/check-gate-coverage.sh` reads it as WIRED rather than
baselined. `governance/dupcheck/check-duplicates.sh` is a **forwarding stub**
kept only because decision records and the knowledge catalogue name this path;
it carries no copy of the rule and forwards every invocation to the gate.

```text
bash scripts/check-duplicates.sh                     # the gate: self-test + this repo
bash scripts/check-duplicates.sh scan [--root DIR]   # the detector alone, over DIR
bash scripts/check-duplicates.sh compare <A> <B>     # byte-identity (files or dirs)
bash scripts/check-duplicates.sh demo-cases          # OPT-IN: scan + the dprs demo
bash scripts/check-duplicates.sh --self-test         # the provocation alone
```

`scan` refuses any same-named protected doc found outside `vendor/` (the pinned
`vendor/CMR` submodule is the repo's own read-only mirror of the CMR trio) and
`.research/` (read-only source clones) — exit 1, naming the fork. Exit codes are
the repo's honesty tri-state: `0` = PASS, `1` = FAIL (a fork, or a byte
difference), `2` = CANNOT-ASSESS (bad invocation, `--root` that is not a
directory, no `find`, no scratch directory for the self-test) — **never** a pass,
and a non-zero code is never collapsed into one.

### It proves it can fail, on every run

A gate that cannot fail is a formality (AO-GR-4, GR-12), so before it looks at
this repository at all the gate provokes itself against a scratch tree outside
it:

| Half | What it asserts |
|---|---|
| planted forks | one file per protected name is refused, and every planted path is named |
| clean tree | a clean tree, and a tree holding an unrelated file, produce no finding |
| mirrors | the same names inside `vendor/` and `.research/` are not findings |
| mutant | with the protected-name declaration neutralised the plant is accepted, and a bad invocation is still CANNOT-ASSESS |
| bad invocation | a bad invocation exits 2, never 0 |

`demo-cases` is the **explicit opt-in**, and the gate never runs it: it compares
the `.research` clones of `dprs` and `git-rca-workspace` to re-verify the
documented byte-identical finding (930 files each, zero diff excluding `.git`),
so it is not storage-free. Point `RESEARCH_BASE` at a checkout that holds the
clones if they are not under this repo's `.research/`:

```bash
RESEARCH_BASE=/path/to/agent-orchestrator/.research \
  scripts/check-duplicates.sh demo-cases
```

## Why

Two phases adopting two drifted copies of the same fleet doc is how standards
fork. ADR-0010 names the single canonical home per doc; this helper makes the
"never fork" half of that decision mechanical (AO-GR-4 — the check can
genuinely fail) instead of prose.
