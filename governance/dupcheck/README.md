# governance/dupcheck — canonical-copy duplicate detection

Automated duplicate detection for the canonical fleet docs this product
**consumes but must never fork** ([ADR-0010](../../docs/decision-records/ADR-0010-canonical-copy-ownership.md),
[decision-spike report](../../docs/spikes/47-canonical-copy-ownership.md)).

## Canonical homes (single source of truth per doc)

| Doc set | Canonical home | Mirrors / consumers (reference-with-provenance, never fork) |
|---|---|---|
| `MODEL-PROFILES.md` · `SME-PROFILES.md` · `SOLUTION-CLASSES.md` (the fleet-standard trio) | `kushin77/CMR` `docs/` | `shared-frontend` `docs/` (origin lineage) · `code-indexing` `docs/` (consumer) |
| `agent-identity.md` + identity schema set (`agent-identity-jwt`, `agent-action`, `agent-oidc-config`, `agent-task`) | `kushin77/shared-governance` `GLOBAL_STANDARDS/` | Reserved pillar ADRs ADR-0002/ADR-0007 (adopt by reference) |
| `kushin77/dprs` (whole tree) | `kushin77/git-rca-workspace` | `dprs` is a **byte-identical** duplicate — never double-count assets |

## Usage

```text
check-duplicates.sh scan                  # no forked canonical docs in this repo
check-duplicates.sh compare <pathA> <pathB>  # byte-identity check (files or dirs)
check-duplicates.sh demo-cases            # scan + dprs ≡ git-rca-workspace demo
```

`scan` excludes `vendor/` (the pinned `vendor/CMR` submodule is the repo's own
read-only mirror of the CMR trio) and `.research/` (read-only source clones).
Any protected doc name found elsewhere in the repo is a fork and fails the
check — exit 1. Exit codes: `0` = PASS, `1` = FAIL (fork / byte difference),
`2` = usage or path error.

`demo-cases` compares the `.research` clones of `dprs` and
`git-rca-workspace` to re-verify the documented byte-identical finding
(930 files each, zero diff excluding `.git`). Point `RESEARCH_BASE` at a
checkout that holds the clones if they are not under this repo's `.research/`:

```bash
RESEARCH_BASE=/path/to/agent-orchestrator/.research \
  governance/dupcheck/check-duplicates.sh demo-cases
```

Wiring `scan` into `make verify` (the `scripts/` lane) is a tracked follow-up;
until then run it on demand or in review.

## Why

Two phases adopting two drifted copies of the same fleet doc is how standards
fork. ADR-0010 names the single canonical home per doc; this helper makes the
"never fork" half of that decision mechanical (AO-GR-4 — the check can
genuinely fail) instead of prose.
