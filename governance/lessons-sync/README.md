# governance/lessons-sync — the cross-repo lessons-sync contract (#424)

The machine surface for the relationship between this repository's lessons loop
and the one in `kushin77/deepseek`. The contract is stated in
`docs/CROSS-REPO-LESSONS-SYNC.md`; this directory holds the pass and its pinned
inputs.

**Decision (one line):** one authoritative ledger —
`governance/lessons/ledger.jsonl` here is the writer, `kushin77/deepseek` is the
derived view, and the sync direction is one-way
(agent-orchestrator -> deepseek), never two symmetric stores.

## Layout

| Path | Role |
|---|---|
| `lessons_sync.py` | The pass: pure, offline, deterministic, tri-state exit, `--self-test` |
| `contract.json` | The declared relationship: roles, direction, the deepseek ledger reference |
| `peer-issues.json` | Pinned, read-only snapshot of the peer board items carrying their loop |
| `hints.json` | The dispatch-hint table (lesson id + issue ref + commit) |

## Run

```bash
bash scripts/check-cross-repo-lessons.sh          # the gate (offline)
python3 governance/lessons-sync/lessons_sync.py --self-test   # provoke every refusal
python3 governance/lessons-sync/lessons_sync.py --live         # resolve the peer ref with gh
```

Exit codes are the repo tri-state: `0` OK / `1` NOT-OK / `2` CANNOT-ASSESS.
CANNOT-ASSESS (a missing or unreadable pinned input) is never `0`.
