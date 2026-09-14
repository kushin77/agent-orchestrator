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
| `cmr-ledger.json` | The **frozen** CMR consolidated index (`_provenance`: source path + sha256 + refresh command) |

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

Exit codes are the repo tri-state: `0` OK / `1` NOT-OK / `2` CANNOT-ASSESS.
CANNOT-ASSESS (a missing or unreadable pinned input, or a missing/empty live CMR
source under `--verify-cmr-source`) is never `0`.
