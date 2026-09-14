# Cross-repo sync owner

Who owns cross-repo sync in `kushin77/agent-orchestrator`, and the machine
surface that makes "the peers' boards point at us" a **scheduled,
evidence-producing pass** instead of something rediscovered when a lane happens
to look. Filed as issue #427; it re-files the gap-register items 9 and 10 that
#181 closed without leaving a standing mechanism.

## 1. The gap this closes

Issue #181 closed with two gaps recorded and no mechanism left behind:

- **gap 9** — *"a standing lane that reviews their boards each wave and files
  direction issues"*;
- **gap 10** — *"their enhancements enter our waves only via reviewed
  direction/harvest issues with provenance; no silent adoption"*.

The hub names the same hole one level up: `kushin77/CMR#865` (board sync is
one-directional — spoke to CMR, with no path back down), `kushin77/CMR#867`
(the sync script has no unattended trigger) and `kushin77/CMR#864` (a
fully-built board-sync that was never wired). Nothing in this checkout changed
when a peer board moved, so a peer's need could sit unread indefinitely.

## 2. The boundary this pass obeys

`docs/CROSS-REPO-EXECUTION-BOUNDARY.md` (the NG4 contract) is the rule this pass
exists to mechanize, and `AGENTS.md`'s Hard DON'Ts state it directly:

> **Never edit another repo's files** — direction/needs go to that repo's board.

The pass therefore **never writes to a peer repository**. The only sanctioned
way work crosses the boundary is a **direction issue on the peer's board**, and
the pass only **reports** the direction issues it would file: it is dry-run by
default, exactly like this repository's standards-push tooling. A direction
issue is never filed by the pass itself.

The boundary contract itself is `docs/CROSS-REPO-EXECUTION-BOUNDARY.md`, merged
to `master` via #387 (`de5b7ed`). It is cited here in backticks rather than as a
relative link to keep this section readable; the obligation it states is
binding regardless of how it is referenced.

## 3. What the pass produces

`governance/sync/peer_triage.py` is the pass. For each peer repo it reports:

| Section | What it says |
|---|---|
| `opened_since_last_pass` | issues present now that the previous snapshot did not have |
| `closed_since_last_pass` | issues now `closed` that the previous snapshot had open |
| `candidates` | **open** issues that either **name us** (a name token appears in the title/body) or **map onto a lane we own** (a label ownership maps to one of our lane directories) |
| `direction_issues_to_file` | the direction issues the pass *would* file on peer boards (dry-run, `filed: false`) |
| `findings` | every contract violation, each naming the item it concerns |

The pass is a pure function of three **pinned, committed** inputs plus the
ownership map, so it is deterministic and offline:

```text
governance/sync/peer-board/snapshot.json    # this pass's board snapshot
governance/sync/peer-board/baseline.json    # the previous pass's snapshot
governance/sync/peer-board/triages.json     # the authored dispositions
governance/sync/peer-board/ownership.json   # owner repo, name tokens, lanes, peers
```

The report is written to a file — never streamed into the shared shell. A
live-fetch mode exists behind an explicit `--live` flag (one `gh issue list`
read per peer, injectable runner), but **the gate never uses it**: the default
mode reads the pinned snapshot and calls no network.

## 4. Dispositions — silence is not one

Every candidate carries exactly one disposition from a closed vocabulary:

| Disposition | Meaning | Required |
|---|---|---|
| `track-here` | We own the half. | `local_issue` (the issue that consumes the peer reference) **and** provenance |
| `direction` | They own it (NG4). We would file a direction issue on their board. | a `direction` record (`target_repo`, `title`, `labels`) **and** provenance |
| `no-action` | Neither half is ours. | a `reason` |

**An item with no disposition is a finding, not silence** — the gate fails and
names the item. An unknown disposition value, a missing reason, or a `direction`
record that targets our own repo is a finding too.

## 5. Provenance is mandatory (gap 10)

Anything **adopted from a peer** enters with its source recorded: the peer
`repo`, the peer `issue`, and the `sha` of the artifact — the same pin
discipline `governance/sync/provenance.py` applies to vendored assets, and the
same vocabulary (`repo` + `issue` + pin) this tree already uses. An adopting
disposition (`track-here` or `direction`) without a complete provenance record
fails, and provenance that names a different item than the one adopted fails.

## 6. No silent adoption, no silent close

Two refusals are enforced, not advisory:

- **No silent adoption.** A `track-here` disposition with no `local_issue` is a
  peer reference adopted without a link back to the issue that consumes it — a
  finding.
- **No silent close.** A run that would **close a peer issue from here** is
  refused: cross-repo `Closes` is not used, because the boundary hands work
  *off* rather than resolving it. The refusal fires both on an explicit
  `closes_peer` field and on a textual `Closes`/`Fixes`/`Resolves
  <owner>/<repo>#<n>` reference naming a foreign repo.

## 7. The schedule and the exit contract

**Cron owns the schedule** — not GitHub Actions, which is disabled fleet-wide
(GR-15). The pass is a code-native target a cron job runs; committing a
refreshed `snapshot.json` is what "the last pass" means.

The pass is honest about what it could not read. Exit codes:

| rc | Meaning |
|---|---|
| `0` | OK — every candidate dispositioned, provenance complete, no refusals |
| `1` | NOT-OK — one or more findings (each named in the report) |
| `2` | CANNOT-ASSESS — a pinned input is missing, unreadable, empty, or malformed |

CANNOT-ASSESS is **never** `0`: a missing snapshot is not a clean pass.

## 8. Enforcement

| Surface | Role |
|---|---|
| `governance/sync/peer_triage.py` | The pass: deterministic, offline, tri-state exit |
| `governance/sync/peer-board/*.json` | The pinned inputs (snapshot, baseline, dispositions, ownership) |
| `governance/sync/tests/test_peer_triage.py` | Behavioural proof, including a negative control per refusal |
| `scripts/check-cross-repo-sync.sh` | The gate: runs the pass, propagates the honest tri-state |
| This document | The contract and the schedule |

## 9. Related work

- `#403` (PMO rollup) — a local consumer of this report.
- `#399` (the join layer) — the report is another projection over the same truth.
- `#181` (closed) — its gap register items 9/10 named this; cited for continuity.
- `kushin77/CMR#865`, `kushin77/CMR#867`, `kushin77/CMR#864`, `kushin77/CMR#862`;
  `kushin77/deepseek#53`.
