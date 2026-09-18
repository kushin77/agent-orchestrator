# Scratch-space discipline

Issue **#488**. How this repo keeps an agent's *scratch* from taking the machine
down, where that guard lives, and what is (and is not) mechanical about it.

## The incident (measured, 2026-09-14)

| Measured | Value |
|---|---|
| `/tmp/ao412.makeverify.log` (peak) | **14,837,231,616 bytes ≈ 14.8 GB** |
| tmpfs total / used | 16 GB / **16 GB (100 %)** |
| `/` free at the same moment | 72 GB — the space existed, it was in the wrong place |
| The mechanism | `make verify >> "$L"` plus `tail -6 "$L" >> "$L"` |

`/tmp` on this box is **tmpfs** — RAM. A full tmpfs is not "a full disk": it is a
hard stop for every parallel lane at once, and it is not recovered by
compacting. The knock-on did more damage than the space: `cp` then wrote a
**0-byte** "backup", and restoring from that backup truncated a source file to
empty.

`tail -6 "$L" >> "$L"` reads and writes the **same** file. It cannot converge —
every repetition at least doubles the log. That is a logic bug, not a style nit.
The repo's own tracked tooling is clean (109 tracked `*.sh`, zero hits); the
defect lives in *agent scratch drivers*, which no repo tracks.

## Decision — where the guard belongs (issue #488, item 1)

**The machine-level detector does not belong in this repo, and is not duplicated
here.** It already exists at `~/laptop-manage/bin/scratch-guard`, on its own
timer, outside every fleet repo. That is the right home: a repo cannot own `/tmp`,
and a repo-scoped scanner cannot see the other lanes' scratch — measured on this
box, `/tmp` held **4,523 top-level entries**, of which only 80 matched the `ao*`
agent-scratch convention. The guard's own lesson from that measurement is worth
keeping: *reap only what is recognisably agent scratch; report the rest.*

What **does** belong here is the half a repo can honestly assert about its own
artifacts, deterministically and offline. So this repo **adopts** the machine
guard by consuming its verdict read-only, and owns a gate over its own tree:

| Question | Owner | Mechanism |
|---|---|---|
| Is the box's `/tmp` near full? Are scratch files pathologically large? Are worktrees living on the tmpfs? | `~/laptop-manage/bin/scratch-guard` (15-minute timer) | `scratch-guard --check` — verdict `0 healthy / 1 condition / 2 cannot-assess`. The repo gate runs it and prints the verdict **advisory**. |
| Does this repo's tracked tooling contain the runaway pattern? | this repo | `scripts/check-scratch-safety.sh --lint` — **gating** in `make verify` |
| Can the guard's detectors still fire at all? | this repo | `--self-test` — **gating** in `make verify` |
| Is one specific copy safe to trust? | this repo | `--verify-copy SRC DST` / `--copy SRC DST` |

**Why the live verdict is advisory, deliberately.** A gate that turns red because
a *neighbour* filled the tmpfs reddens an unrelated diff and makes the red
ambiguous — change, or machine? A gate that is red for reasons the author cannot
fix is a gate people learn to ignore, which is strictly worse than no gate. The
machine guard owns that verdict and already has a timer; the repo gate prints it
so nobody is blind, and `--scan` is the verb that **refuses by name** for an
agent or the ops runner that wants the verdict right now.

## The discipline (issue #488, item 2)

The detector is the safety net; these five rules are the fix. Each one names the
mechanism that enforces it.

1. **Never read and write the same log file in one command.** `tail -6 "$L" >>
   "$L"` cannot converge. → `SCRATCH-SELF-APPEND` (gating, refused by file *and*
   line number).
2. **Every appended capture gets a cap.** A bare `make verify >> "$L"` is
   unbounded; a `head -c` / `tail -c` / `truncate` bound is the difference
   between a log and an incident. → advisory `NOTE` from `--lint`, and the
   per-file cap below.
3. **A file count of 1 is not "small" on tmpfs.** One file is all it takes.
   `/tmp` here is 16 GB of RAM shared with every lane. → `SCRATCH-FILE-OVERSIZE`
   (256 MB per file) and `SCRATCH-SPACE-NEAR-FULL` (75 % ceiling, 90 %
   emergency).
4. **Never trust a copy because `cp` exited 0; verify the bytes.** On a full
   filesystem the copy can leave a 0-byte file, and a restore from it is how a
   source file ends up empty. → `SCRATCH-EMPTY-COPY` (the named refusal),
   `SCRATCH-SHORT-COPY`, `SCRATCH-COPY-MISMATCH`; `--copy` also **removes** the
   0-byte artifact it just proved empty, so nothing can restore from it.
5. **Worktrees on disk, not on `/tmp`.** A worktree on tmpfs costs RAM and
   inodes and vanishes on reboot — the sibling fleet hazard tracked on the vendor
   board as `kushin77/code-indexing#158` / `#159`. Measured at the commit that
   landed this guard: **34** registered worktrees under `/tmp`. → reporting by
   `--scan`; reclaiming is [#516](https://github.com/kushin77/agent-orchestrator/issues/516)'s
   and `scripts/prune-worktrees.sh`'s job.
6. **Verify a backup before you restore from it.** `--verify-copy` is the
   mechanical form of "did this copy actually land?" — it answers a question a
   shell `cp` silently does not.

## Naming a scratch directory — the form the gate accepts (#804)

The marker scan in `scripts/check-docs.sh` (the `docs-lint` check) reads every
`*.sh`, `*.py` and `*.go`, so the form *this* document recommends has to be a
form that scan accepts. It is the canonical `mktemp` template, with the trailing
run of X:

```bash
dir="$(mktemp -d /tmp/<name>.XXXXXX)" || exit 2
```

Issue [#804](https://github.com/kushin77/agent-orchestrator/issues/804) measured
the opposite. The scan's token branch ended in a trailing word boundary and had
no leading one, so it matched the *tail* of any run of three or more X — and
`mktemp` requires a template ending in at least three. Every `mktemp` template
therefore failed `docs-lint`, with a message that names a marker rather than a
placeholder, so the flag read as a real defect and the apparent fix was to
delete the template; the lane that hit it worked around the gate with
`python3 -c 'tempfile.mkdtemp()'`. Not a defect of the author and not a defect
of the gate's *intent* — the pattern was missing a boundary, and it now has one.

**Use six X, not three.** A run of exactly three X is still refused: token for
token it is the standalone marker the gate must still refuse (`# XXX: fix`), so
the pattern cannot tell the two apart. A template with four or more X is
unambiguous and accepted, and six is canonical. This boundary is measured, not
assumed: `bash scripts/check-docs.sh --self-test` prints it on every run, and
`scripts/check-marker-scan.sh` provokes both halves against the shipped scanner.

## Using it

```bash
bash scripts/check-scratch-safety.sh                    # the gate (make verify)
bash scripts/check-scratch-safety.sh --self-test         # prove every detector fires
bash scripts/check-scratch-safety.sh --lint "$TMPDIR"    # lint agent scratch drivers
bash scripts/check-scratch-safety.sh --scan /tmp         # refuse, by name, now
bash scripts/check-scratch-safety.sh --copy SRC DST      # copy, then verify the bytes
bash scripts/check-scratch-safety.sh --verify-copy SRC DST
~/laptop-manage/bin/scratch-guard --check                # the machine's own verdict
```

Exit contract, shared with the rest of the fleet's guards: `0` OK / `1` NOT-OK /
`2` CANNOT-ASSESS — never a pass when the question could not be answered.

## What this guard does NOT do (the honest boundary)

- **It does not gate on live machine state.** See the decision above; the
  machine guard's timer owns that.
- **It does not delete anything of yours.** The one removal it performs is a
  0-byte file it has just proved empty, in `--copy`. Reclaiming scratch is
  `scripts/prune-worktrees.sh` and #516. A worktree's HEAD not being reachable
  from `origin/master` by name, or its remote branch having been deleted after
  a squash-merge, does not by itself mean the work is unlanded — the pruner
  also checks CONTENT equivalence (`governance/isolation/worktree.py`'s
  `content_landed`, the same test `--branches` already used for lane branches,
  #1265) before keeping a worktree "just in case." `scripts/check-worktree-cap.sh`
  is the box-wide companion gate: it reds `worktree-cap-exceeded:<n>/<cap>`
  when live worktrees exceed open lane records plus a declared slack
  (`governance/isolation/worktree-cap.yaml`), and `reaper-unscheduled` when
  nothing actually installs `prune-worktrees.sh` in the crontab a scheduler
  reads — a declaration is not an installation.
- **It does not lint untracked scratch.** The gating `--lint` covers the repo's
  tracked `*.sh`. A scratch driver in `/tmp` is linted only when someone runs
  `--lint <dir>` — which the discipline above tells agents to do.
- **It does not replace the machine guard, and does not reimplement it.** The
  `df`/inode/worktree measurements here exist to give the **repo's own users** a
  verdict without leaving the repo; the box-wide verdict is the guard's.

## Provenance (GR-10)

Adapted — not copied — from `~/laptop-manage/bin/scratch-guard` (written
2026-09-14 in response to the measurement above): the self-append predicate, the
exit-code convention, and the allowlist/never-guess reaping lesson that came from
the `--reap` near-miss (`/tmp/.X11-unix` and other lanes' live `mktemp`
directories were offered for deletion by an age-only rule). The report-vs-refuse
split and the no-false-green gate shape follow this repo's own conventions
(`docs/QA-GATE.md`, `docs/GOLDEN-RULES.md`). The worktree-on-tmpfs hazard is
`kushin77/code-indexing#158` / `#159`.

## Re-verify

```bash
bash scripts/check-scratch-safety.sh --self-test            # every detector fires
python3 -m pytest -q scripts/tests/test_scratch_safety.py    # the same contract, out of process
bash scripts/check-scratch-safety.sh --scan /tmp             # rc 1 if the box is in the incident state
make verify                                                  # runs the gate above
```
