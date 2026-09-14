# Session fleet sync — sync contract and gap register

Issue #181: bidirectional capability feedback loop between this repo and the
`kushin77/deepseek` and `kushin77/code-indexing` modules. The two boards already
point at us, and their enhancements must flow into every consecutive wave of
this repo — each wave faster, cheaper, and smarter than the last.

This file is the **sync contract** (what every wave must do) plus the **gap
register** (what the owner's intent implies that is not yet built, and which
issue owns each gap). It is the written half of the contract; the behavioural
half is the wave tooling under [`governance/waves/`](../governance/waves/cli.py)
and the cross-repo chain-edge parser in
[`governance/dispatch/snapshot.py`](../governance/dispatch/snapshot.py).

## The sync contract (what each wave must do)

1. **Wave bootstrap = a query, not memory.** Each wave starts by running a
   code-native report: what merged/opened in deepseek + code-indexing since the
   last wave, plus the codeidx context-pack deltas. The report is an artefact,
   not a conversation
   ([`governance/waves/bootstrap.py`](../governance/waves/bootstrap.py)).
2. **Consume, don't re-implement.** Any indexing / prompt-cache / tool-shape
   work in our waves must consume the codeidx context-pack (code-indexing #128)
   and cite the DR-069 baselines (code-indexing #132). Re-derivation is a
   conformance failure.
3. **Push back.** Every wave closes with direction issues to their boards for
   what the next wave needs (the bidirectional half of "perfect sync").
4. **Pin for reproducibility.** Each wave pins the SHAs of the deepseek module
   and the codeidx context-pack it consumed (GR-10 provenance). The pin file is
   [`governance/waves/pins.json`](../governance/waves/pins.json).
5. **Gate on health, don't wait on maturity.** The module health gate (#122) is
   a wave precondition that **degrades the wave scope** — a missing enhancement
   becomes a deferred, flag-gated item plus a direction issue, never a blocked
   wave.
6. **Measure the loop.** Waves get a ledger — duration, cost per issue, model
   escalations, verify-fail rate — so "faster / cheaper / smarter" is provable,
   not asserted
   ([`governance/waves/ledger.py`](../governance/waves/ledger.py)). deepseek's
   metering must be truthful first (deepseek #115).

## Gap register (what the owner's intent is missing)

| # | Gap | Owning issue |
|---|-----|--------------|
| 1 | Cross-repo chain edges — our `Blocked-by:` parser only understands same-repo issue numbers; `Blocked-by: kushin77/code-indexing#128` must parse and gate | this issue (#181) |
| 2 | Wave-bootstrap report (the delta query) — code, not prose | this issue (#181) |
| 3 | Wave ledger + SLOs (duration / cost / escalations / verify-rate per wave) | this issue (#181) |
| 4 | Codeidx context-pack consumption contract in our engine/memory (#129/#130) | direction to our board |
| 5 | Steering contract unification — brain/sister vs deepseek steer-dispatch (#91) must be ONE contract across runtimes | #161 |
| 6 | Lessons-loop sync — deepseek #84/#79 and our #141 must share one ledger, not two | #141 |
| 7 | Board-sequencing doctrine sync — deepseek #74 vs our dispatch rules must agree | CMR#992 |
| 8 | Fleet worktree policy — code-indexing #158/#159 flag `/tmp` (tmpfs) worktrees as an inode/RAM hazard; our waves must stop using `/tmp` worktrees | this issue (#181) |
| 9 | Wave triage lane — a standing lane that reviews their boards each wave and files direction issues | this issue (#181) |
| 10 | Trusted feed — their enhancements enter our waves only via reviewed direction/harvest issues with provenance; no silent adoption | this issue (#181) |

Gaps 1, 2, 3, 8, 9, and 10 are closed by this issue. Gaps 5, 6, and 7 are owned
by the issues named. Gap 4 is not our code — it is a direction issue filed back
to our own board, because the vendor module must publish the contract before we
can consume it.

## Fleet worktree policy (gap 8)

code-indexing #158/#159 flag `/tmp` worktrees as a hazard on the shared
workstation: `/tmp` is a tmpfs mount, so a worktree there consumes inodes and
RAM that the machine does not have to spare, and it vanishes on reboot. The
rule for every wave lane:

- **Never** create a worktree under `/tmp` (or a bare `mktemp -d` there).
- Use a **unique** path under `~/ao-worktrees/`, derived from the issue number
  *and* the session/runtime — never a bare issue number, because two sessions
  on the same issue would collide in the same directory.
- Commit with **explicit paths**, never `git add -A`, so a peer's files cannot
  be swept into the commit.

This is the same discipline the lane isolation tooling mints for every session;
the wave triage lane (below) inherits it.

## Wave triage lane (gap 9)

Bidirectional sync needs an owner. Each wave, a standing triage lane:

1. Reviews the deepseek and code-indexing boards (what merged, what opened).
2. Files **direction issues** on their boards for what the next wave needs.
3. Records those issues in the wave ledger's `direction_issues` field, so the
   push-back half of the contract is provable rather than asserted.

The triage lane's work is a wave of its own and is subject to the same ledger
and SLO reporting as every other wave.

## Trusted feed (gap 10)

Their enhancements enter our waves **only** through reviewed direction/harvest
issues that carry provenance (source repo, path, license — GR-10). There is no
silent adoption: an enhancement that appears in our tree without a
direction/harvest issue behind it is a conformance failure, exactly as if the
code were cannibalized without provenance.

## Verification

```bash
make verify
python3 governance/knowledge/cli.py validate
python3 governance/waves/cli.py bootstrap --since <iso|sha> --out /tmp/report.md
python3 governance/waves/cli.py ledger-query --ledger governance/waves/ledger.jsonl
```

The offline bootstrap consumes `governance/waves/pins.json` — a test seam, not
the real feed (see `governance/waves/bootstrap.py`). The real feed is the
network path (`--online`), which the gate never runs.
