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

There is a **second** lessons ledger, and this document declares it too — the
`kushin77/CMR` `docs/LESSONS.md` consolidated index. **Declared (intent, not yet
confirmed):** `governance/lessons/ledger.jsonl` in `kushin77/agent-orchestrator`
is the **writer** for agent-orchestrator-scoped records; the `kushin77/CMR`
`docs/LESSONS.md` index is the org-level **derived view**; the direction of flow
is **one-way: agent-orchestrator -> CMR**. This is a **declared intent, not a
confirmed mapping**: measured 2026-09-14, the two ledgers share no record ids and
no cross-reference, so there is no record-level mapping to assert. The direction
issue in §7 asks the hub to confirm the direction or declare the mapping — a
truthful "declared intent, unconfirmed" beats a fabricated crosswalk.

Where the physical constraint of the boundary (§2) means a second on-disk store
must exist, the roles are fixed and disjoint:

| Side | Role | Direction of data flow |
|---|---|---|
| `kushin77/agent-orchestrator` `governance/lessons/ledger.jsonl` | **writer** (authoritative) | out only |
| `kushin77/deepseek` | **derived view** (read-only projection) | in only |
| `kushin77/CMR` `docs/LESSONS.md` | **derived view** (declared intent, unconfirmed) | in only |

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
governance/lessons-sync/contract.json      # the declared relationships (roles, direction, references)
governance/lessons-sync/peer-issues.json   # the pinned peer board items that carry the peer loop
governance/lessons-sync/hints.json         # the dispatch-hint table (§4)
governance/lessons-sync/cmr-ledger.json    # the FROZEN CMR consolidated index (§1, the second ledger)
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
| the second ledger is declared | an undeclared CMR-hub ledger (`ledger-undeclared`) |
| the CMR reference resolves | a CMR ledger reference that does not resolve against the frozen baseline (`ledger-ref-unresolved`) |
| no silent drop (CMR side) | a CMR index record with no disposition — `hub-only` or `mirrors` (`cmr-record-undisclosed`, naming the id) |
| counterpart by id (CMR side) | a `mirrors` mapping whose local counterpart is missing (`local-counterpart-missing`, naming the id) |
| intent is not a mapping | a `mirrors` mapping asserted while `confirmed: false` (`mapping-unconfirmed`) |
| no silent drop (local side) | a local ledger record that is neither a mirror target nor `local-only` (`local-record-undisclosed`, naming the id) |

**No silent close.** A `Closes` / `Fixes` / `Resolves <owner>/<repo>#<n>`
reference to a foreign repo is refused: same-owner cross-repo `Closes` *does*
auto-close the foreign issue, so it must never be written by accident. The
boundary hands work off — it does not resolve it, and **zero items are closed on
the peer board from here**.

### 6.1 The exit contract, and why a missing input is not a pass

The pass is offline and deterministic by default: stdlib only, one read per
pinned input, no network, no `gh`, no wall clock — so two runs over the same
pinned inputs produce byte-identical reports. Live peer reads happen only behind
the explicit `--live` flag (which calls `gh api`). The CMR freeze is re-resolved
against the live `vendor/CMR/docs/LESSONS.md` only behind `--verify-cmr-source`;
a missing, unreadable, or empty live source is **CANNOT-ASSESS (`2`), never `0`**
— and a fresh worktree leaves the submodule unpopulated, which is exactly why the
default mode reconciles against the frozen baseline instead of the live file.

| rc | Meaning |
|---|---|
| `0` | OK — the contract holds |
| `1` | NOT-OK — one or more findings, each named |
| `2` | CANNOT-ASSESS — a required input is missing, unreadable, empty, or malformed |

CANNOT-ASSESS is **never** `0`. In `--live` mode a peer source that is
unavailable is CANNOT-ASSESS (`2`), never a clean pass.

## 7. The direction issues

Each peer half is requested — and only requested — by **one** direction issue on
that peer's board. Two boards are declared in §1, so there are two.

The deepseek half, which states our authoritative-ledger decision, names what we
need from their side, and cites `kushin77/agent-orchestrator#424` and
`docs/CROSS-REPO-EXECUTION-BOUNDARY.md`:

- **`kushin77/deepseek#118`** —
  <https://github.com/kushin77/deepseek/issues/118>

It references `kushin77/deepseek#84` and `#79` without closing or modifying
anything on that board, and carries no `Closes` / `Fixes` reference to any peer
issue.

The CMR-hub half, which records the **declared, unconfirmed** relationship from
§1 and asks the hub to confirm the writer/derived-view direction or declare the
record-level mapping, citing `kushin77/agent-orchestrator#424` and the boundary
doc:

- **`kushin77/CMR#1009`** —
  <https://github.com/kushin77/CMR/issues/1009>

It closes nothing on that board and carries no peer-closing reference either.

These two are the only sanctioned cross-repo writes; everything else here is
read-only.

## 8. Enforcement

| Surface | Role |
|---|---|
| `governance/lessons-sync/lessons_sync.py` | The pass: deterministic, offline, tri-state exit, self-test negative controls |
| `governance/lessons-sync/{contract,peer-issues,hints,cmr-ledger}.json` | The pinned and frozen inputs |
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
- `kushin77/CMR#1009` — the direction issue that asks the hub to confirm the second
  ledger's writer/derived-view direction (filed by this lane, cited in §7).
