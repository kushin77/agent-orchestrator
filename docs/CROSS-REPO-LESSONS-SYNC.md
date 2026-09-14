# Cross-repo lessons sync

The declared relationship between the two lessons loops either side of the repo
boundary — the one in this repository (`#402`/`#403`, on top of
`governance/lessons/`) and the one being built in `kushin77/deepseek`
(`kushin77/deepseek#84`, `kushin77/deepseek#79`). Filed as issue #424; it re-files
the item that `#181`'s gap register named as **gap 6** — *"Lessons-loop sync —
deepseek #84/#79 and our #141 must share one ledger, not two"* — and pointed at
`#141`, which closed while the peer issues stayed open.

## 1. The decision, in one line

**One authoritative ledger.** `governance/lessons/ledger.jsonl` in
`kushin77/agent-orchestrator` is the **writer**; the `kushin77/deepseek` view is
the **derived view** (read-only); the sync direction is **one-way:
agent-orchestrator -> deepseek** — never two symmetric stores.

Where the physical constraint of the boundary (§2) means a second on-disk store
must exist, the roles are fixed and disjoint:

| Side | Role | Direction of data flow |
|---|---|---|
| `kushin77/agent-orchestrator` `governance/lessons/ledger.jsonl` | **writer** (authoritative) | out only |
| `kushin77/deepseek` | **derived view** (read-only projection) | in only |

Peer-originated lessons do **not** flow back into the authoritative ledger by
copy. They enter it the same way every other cross-repo item does — through a
reviewed direction/harvest item carrying provenance (§5). That single-writer rule
is what makes "one ledger" true even when two stores exist.

## 2. The boundary this obeys

`docs/CROSS-REPO-EXECUTION-BOUNDARY.md` (the NG4 contract) is the rule this
relationship lives under, and `AGENTS.md`'s Hard DON'Ts state it directly:

> **Never edit another repo's files** — direction/needs go to that repo's board.

The only sanctioned output of observing a peer's need is a **direction issue on
the peer's board**. This document does not write to the peer repository; it
records the relationship, and the peer half is requested by the direction issue
recorded in §7.

## 3. What "the relationship" means, concretely

The contract is machine-checked by `governance/lessons-sync/lessons_sync.py`
(§6). It is a pure function of three pinned, committed inputs plus this
repository's own ledger:

```text
governance/lessons-sync/contract.json      # the declared relationship (roles, direction, references)
governance/lessons-sync/peer-issues.json   # the pinned peer board items that carry the peer loop
governance/lessons-sync/hints.json         # the dispatch-hint table (§4)
governance/lessons/ledger.jsonl            # the authoritative ledger itself
```

The deepseek **ledger reference** is a real, measured pointer: it resolves to the
peer issue that will host the peer ledger — `kushin77/deepseek#84`, pinned open in
`peer-issues.json` (`state: requested`). It is not asserted to exist yet: the
peer ledger has not materialised, so `peer_lessons` is empty **by measurement,
not by omission**, and the direction issue in §7 asks for it.

## 4. The dispatch-hint half

`kushin77/deepseek#79` asks for lessons to be "fed back via dispatch hints". Our
equivalent of a dispatch hint is the **brain directive / claim chain** — the
directive recorded in `.fleet/sent` that authorises a claim
(`governance/dispatch/`), not a free-floating string. The mapping is therefore:

| Peer (`kushin77/deepseek#79`) | Local equivalent |
|---|---|
| "record EPIC-13 lessons in the CMR ledger" | the lesson record in `governance/lessons/ledger.jsonl` |
| "feed them back via dispatch hints" | a hint that carries the lesson into the brain directive / claim chain |

A hint is a **structured record**, never a bare string: it carries the
`lesson_id`, an `issue` reference, and a `commit` sha. The gate refuses a hint
without that provenance, and a bare-string hint is a finding, not a pass.

## 5. Provenance is mandatory

Anything adopted across the boundary carries its source: the peer `repo`, the
peer `issue`, and a pin. A local lesson is **discoverable from the peer side**
only when its local id resolves in the authoritative ledger *and* its declared
peer counterpart resolves on the peer side; a lesson whose peer-side counterpart
is **missing is reported by id**, never passed over in silence.

## 6. What the gate checks (contract, not prose)

`scripts/check-cross-repo-lessons.sh` runs
`governance/lessons-sync/lessons_sync.py` and propagates its honest tri-state.
The pass checks the contract, and every check can genuinely fail:

| Check | The violation it names |
|---|---|
| one writer / one derived view | a second `writer` — two symmetric stores (`symmetric-stores`) |
| the ledger reference resolves | the deepseek ledger reference does not resolve (`ledger-ref-unresolved`) |
| discoverability, both directions | a lesson recorded on one side is not discoverable from the other (`lesson-not-discoverable`, `local-counterpart-missing`) |
| peer counterpart **by id** | a lesson whose peer-side counterpart is missing (`peer-counterpart-missing`, naming the lesson id) |
| hint provenance | a hint with no issue ref or no commit — a bare string (`hint-without-provenance`) |
| no peer close | a reference that would close a peer issue from here (`peer-close-refused`) |

**No silent close.** A `Closes` / `Fixes` / `Resolves <owner>/<repo>#<n>`
reference to a foreign repo is refused: same-owner cross-repo `Closes` *does*
auto-close the foreign issue, so it must never be written by accident. The
boundary hands work off — it does not resolve it, and **zero items are closed on
the peer board from here**.

### 6.1 The exit contract, and why a missing input is not a pass

The pass is offline and deterministic by default: stdlib only, one read per
pinned input, no network, no `gh`, no wall clock — so two runs over the same
pinned inputs produce byte-identical reports. Live peer reads happen only behind
the explicit `--live` flag (which calls `gh api`).

| rc | Meaning |
|---|---|
| `0` | OK — the contract holds |
| `1` | NOT-OK — one or more findings, each named |
| `2` | CANNOT-ASSESS — a required input is missing, unreadable, empty, or malformed |

CANNOT-ASSESS is **never** `0`. In `--live` mode a peer source that is
unavailable is CANNOT-ASSESS (`2`), never a clean pass.

## 7. The direction issue

The peer half is requested — and only requested — by **one** direction issue on
`kushin77/deepseek`, which states our authoritative-ledger decision, names what we
need from their side, and cites `kushin77/agent-orchestrator#424` and
`docs/CROSS-REPO-EXECUTION-BOUNDARY.md`:

- **`kushin77/deepseek#118`** —
  <https://github.com/kushin77/deepseek/issues/118>

It references `kushin77/deepseek#84` and `#79` without closing or modifying
anything on that board, and carries no `Closes`/`Fixes` reference to any peer
issue. It is the one sanctioned cross-repo write; everything else here is
read-only.

## 8. Enforcement

| Surface | Role |
|---|---|
| `governance/lessons-sync/lessons_sync.py` | The pass: deterministic, offline, tri-state exit, self-test negative controls |
| `governance/lessons-sync/{contract,peer-issues,hints}.json` | The pinned inputs |
| `governance/lessons-sync/tests/test_lessons_sync.py` | Behavioural proof, one negative control per refusal |
| `scripts/check-cross-repo-lessons.sh` | The gate: runs the pass, propagates the honest tri-state |
| This document | The contract the gate enforces |

This gate is **not wired into `make verify`** by this lane (the lane does not own
`Makefile` / `scripts/verify.sh`); the integration lane that owns those files
adds it alongside the other cross-repo gates.

## 9. Related work

- `#402` (lessons register -> ticket facets) — the local half this reconciles
  with; it is the blocker this lane waited on.
- `#403` (PMO rollup + RAID derived views) — a consumer of both.
- `#181` (closed) — its gap register named this as gap 6; cited so the history is
  not lost.
- `#427` (`docs/CROSS-REPO-SYNC-OWNER.md`) — the scheduled peer-board triage pass
  that files direction issues with provenance; this document is the lessons-loop
  sibling of that contract.
- `kushin77/deepseek#84`, `kushin77/deepseek#79`, `kushin77/deepseek#74`,
  `kushin77/deepseek#78`; `kushin77/CMR#865` (board sync is one-directional).
