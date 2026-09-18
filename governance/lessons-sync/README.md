# governance/lessons-sync — the cross-repo lessons-sync contract (#424)

The machine surface for the relationship between this repository's lessons loop
and the peer loops either side of the repo boundary: the one in
`kushin77/deepseek` and the `kushin77/CMR` consolidated index. The contract is
stated in `docs/CROSS-REPO-LESSONS-SYNC.md`; this directory holds the pass and
its pinned inputs.

**Decision (one line):** one authoritative ledger —
`governance/lessons/ledger.jsonl` here is the writer; `kushin77/deepseek` is a
derived view (one-way, agent-orchestrator -> deepseek); the `kushin77/CMR`
`docs/LESSONS.md` consolidated index is the **second** derived view, declared as
an **intent** (direction agent-orchestrator -> CMR) and **not a confirmed
mapping** — never two symmetric stores.

## Layout

| Path | Role |
|---|---|
| `lessons_sync.py` | The pass: pure, offline, deterministic, tri-state exit, `--self-test` |
| `contract.json` | The declared relationships: roles, directions, the deepseek and CMR ledger references |
| `peer-issues.json` | Pinned, read-only snapshot of the peer board items carrying their loop |
| `hints.json` | The dispatch-hint table (lesson id + issue ref + commit) |
| `cmr-ledger.json` | The **frozen** CMR consolidated index (`_provenance`: source path + sha256 + refresh command) and the per-record `local-only` declarations |

## The second ledger, and why it is frozen

The `kushin77/CMR` `docs/LESSONS.md` index lives in the pinned `vendor/CMR`
submodule, which is **unpopulated in a fresh git worktree**. Freezing it as a
committed contract input (the pattern used by
`registry/parity/canonical/cmr-role-vocabulary.json`) keeps the default gate
offline and deterministic anywhere. Every CMR index record carries an explicit
disposition — `hub-only` (org-scoped, deliberately not mirrored here) or
`mirrors` naming the local counterpart it maps to. A record with no disposition,
or a mapping whose counterpart is missing, is reported **by id**, never dropped.
Because the relationship is declared `confirmed: false`, an asserted `mirrors`
mapping is itself a finding (`mapping-unconfirmed`) until the hub confirms it.

## The local side: a declaration, not a silence

The mirror image of that rule is the `local-only` side. Every authoritative
ledger record that **no hub row mirrors** must carry a declaration, and a
declaration only accounts for the record when it is judgeable (#1123):

```json
{"id": "CA-0010", "reason": "no-hub-counterpart",
 "record_ref": "#506", "judged_against": "b6c49aa03992dba9fe4b87b46104b8fc2f69f224"}
```

* `reason` comes from the closed vocabulary `cmr_hub.local_only_reasons` in
  `contract.json` — never a free-text assertion. An undeclared reason is
  `local-only-reason-unknown`, and a reason whose predicate the pass does not
  implement is `local-only-predicate-unknown`.
* `judged_against` is the hub revision the declaration was judged against, and
  the pass compares it to the freeze's `_provenance.vendor_commit`. This is what
  makes a blanket listing safe to *have*: when the hub is re-frozen, every
  declaration goes `local-only-declaration-stale` **by id** instead of silently
  inheriting a judgement nobody re-made.
* `record_ref` must be a string the record itself carries. A declaration citing
  a ref the record does not have is `local-only-record-ref-unresolved`, so a
  declaration cannot be disassociated from the record it covers.
* The predicate can also be **falsified**: the moment a hub row declares
  `mirrors: <id>`, that record's `no-hub-counterpart` reason no longer holds and
  is reported as `local-only-reason-stale`. The reason is never assumed.

A bare id is `local-only-undocumented` and does **not** count as an accounting
for the record, and a ledger record with no declaration at all stays
`local-record-undisclosed`. Both are reported by id, never dropped.

## Run

```bash
bash scripts/check-cross-repo-lessons.sh                     # the gate (offline)
bash scripts/check-cross-repo-lessons.sh --verify-cmr-source # re-resolve the freeze live
python3 governance/lessons-sync/lessons_sync.py --self-test   # provoke every refusal
python3 governance/lessons-sync/lessons_sync.py --live         # resolve the peer ref with gh
```

Refresh the freeze where `vendor/CMR` is populated:

```bash
python3 governance/lessons-sync/lessons_sync.py --refresh-cmr-baseline \
  --cmr-source vendor/CMR/docs/LESSONS.md
```

The refresh carries declarations over **verbatim** — it re-freezes the hub, it
does not judge the local side. A local record with no complete declaration makes
the refresh **refuse** and write nothing, naming the ids: declaring a record
local-only with no reason is exactly the shortcut the pass exists to refuse. And
because each carried declaration keeps its own `judged_against`, a refresh that
moves the pin leaves every declaration stale — reported by id at the gate —
until someone re-judges it against the new revision.

Exit codes are the repo tri-state: `0` OK / `1` NOT-OK / `2` CANNOT-ASSESS.
CANNOT-ASSESS (a missing or unreadable pinned input, or a missing/empty live CMR
source under `--verify-cmr-source`) is never `0`.
