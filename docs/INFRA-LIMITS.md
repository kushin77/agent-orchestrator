# Infra limits — the sandbox and ephemeral-storage contract (issue #729)

This is the contract for the machine the fleet runs on: what the sandbox
forbids, which storage is ephemeral and shared, and the rule that decides when a
write counts as evidence. It is enforced, not described — the guard that reads it
is [`../scripts/check-infra-limits.sh`](../scripts/check-infra-limits.sh), and the
episode that produced it is recorded as `INC-0009` / `RCA-0009` in
[`../governance/lessons/ledger.jsonl`](../governance/lessons/ledger.jsonl).

## The sandbox (measured on this box)

- **Network is blocked.** A command that needs GitHub or any other network
  access must request it explicitly. A denied command is a *signal*, not an
  obstacle: never retry it verbatim, and never route around the refusal.
- **The filesystem is read-only outside the workspace and `$TMPDIR`.** Writing
  anywhere else needs an explicit grant, so a command that "works" in a full
  shell can be refused here.
- **`$TMPDIR` is a shared, periodically-cleaned cache.** On this box it resolves
  to `~/.cache/leaderboard/tmp`, not to `/tmp`. A bare `mktemp -d` can therefore
  be collected mid-run. `/tmp` is writable — use an explicit template:
  `mktemp -d /tmp/<name>.XXXXXX`. (`$$` is constant in a persistent shell, so
  name logs with `$(date +%s)` instead.)
- **The shell is shared and persistent.** Sibling lanes interleave output into
  the same terminal, so an inline command return can contain another lane's text.
  Redirect to a uniquely-named log and read the log back from the file.
- **A long gate can be interrupted by a neighbour lane's interrupt.** An
  `Interrupt` is not a code failure and not a verdict about the change: detach
  long runs (`setsid ... &`) and retry, then judge by the gate's own attestation
  rather than by the exit code of the shell that started it.
- **The box is shared.** Measured load of ~85–107 with eight concurrent gate
  processes is normal during a wave, so "slow" is usually "busy", and the first
  question is always *whose* work is running.

## `/tmp` is a 16 GiB shared tmpfs, and it has been filled to 100 %

`/tmp` is a tmpfs: it is RAM, it is finite, it is shared by every lane on the
box, and it is not backed by disk. Measured on 2026-09-14: `/tmp` at 16G/16G
(100 %) because a sibling lane had left a **14.8 GB scratch log** that no process
held. The symptom is the dangerous part — see the write rule below.

## The write rule: a write is not a write until it is read back non-empty

On a full filesystem a redirected write fails **silently**:

```bash
cat > /tmp/body.md <<'EOF'   # fails with "No space left on device"
...content...                # the file exists and is 0 bytes
EOF
```

A shell-visible failure is not guaranteed, so anything downstream that trusts
"the file exists" — `gh issue create --body-file /tmp/body.md` is the measured
case — reports **success for an empty body** and returns a URL you believe. The
same trap catches `gh api -f body=@file`, which posts the literal string
`@file` instead of the file's contents.

**The rule:** an artifact-producing path verifies its own write with `wc -c`
before the artifact is treated as evidence. A 0-byte artifact is never evidence;
it is a failed write wearing a filename.

**Recovery, in order:**

1. `du -sh /tmp/* | sort -rh | head` — find the hog.
2. `ls -l /proc/*/fd | grep <name>` — confirm no process holds it.
3. `: > /tmp/<hog>` — **truncate**, do not delete. Truncating frees the space and
   keeps the path, so a writer that still holds the file does not keep filling a
   deleted inode.
4. Re-run the write and read it back: `wc -c < <file>` must be non-zero.

## Thresholds and the guard

`bash scripts/check-infra-limits.sh` enforces the contract against the scratch
filesystem. Four guards, each able to fail by name, plus seven provoked controls
(five refusals, one transient-scan recovery and one positive) that prove each
guard can actually fail.

| Knob | Default | Meaning |
|---|---|---|
| `AO_INFRA_SCRATCH_DIR` | `/tmp` | the scratch filesystem to police |
| `AO_INFRA_MIN_FREE_MB` | `2048` | free space the scratch filesystem must keep, in MiB |
| `AO_INFRA_MIN_FREE_PCT` | `10` | free space floor as a percentage, i.e. at most 90 % used |
| `AO_INFRA_MAX_SCRATCH_MB` | `4096` | ceiling for a *single* scratch file — the orphan-hog guard |
| `AO_INFRA_SCAN_ATTEMPTS` | `3` | attempts a scratch scan gets before it is called a failure |
| `AO_INFRA_SCAN_BACKOFF_MS` | `200` | delay between those attempts, in milliseconds |

Exit-code contract: `0` OK / `1` NOT-OK / `2` CANNOT-ASSESS (a missing scratch
dir, a malformed knob, or no `df`/`find` to measure with).

The guards are:

1. **contract** — this file exists, [`../AGENTS.md`](../AGENTS.md) links it, and
   it declares the knobs, the truncate recovery and the `wc -c` rule above. A
   rule nobody can find is not a rule.
2. **free-space** — the floor and the used-percent ceiling, printed with the
   measured value either way.
3. **orphan-hog** — no single file in the scratch dir exceeds
   `AO_INFRA_MAX_SCRATCH_MB`. One orphan is enough to fill a tmpfs, and it is
   invisible until something else fails.
4. **write rule** — the guard's own evidence artifact
   (`.verify/infra-limits-report.json`) is written through a single path that
   refuses to accept it unless `wc -c` reports a non-empty file.

The controls scale the **threshold**, not the box: a 2 MiB file against a 1 MiB
ceiling provokes the orphan-hog refusal, and a floor one MiB above what the
filesystem actually has provokes the free-space refusal. Provoking a failure must
be cheap on a machine four lanes share — and a real file over a real ceiling is
the same failure either way.

### A transient scan failure is suppressed, then retried (issue #1392)

`find` exits `1` on **any** error, and one of them is routine here: a top-level
`/tmp` entry that vanishes between `readdir` and `stat`, which is exactly what
four lanes churning scratch directories produce. Measured 2026-09-19: **225 of
300** `find /tmp -maxdepth 1` scans returned `rc=1` while a neighbour churned
files at the top level, and **0 of 300** against an unchurned target — one scan
is a ~59 ms window over ~21,000 entries. The guard used to discard the reason
(`2>/dev/null`) and fail closed on any non-zero rc, so the race redded the whole
composite for **every** lane, with nothing in the output saying why.

Three things fix it, each measured rather than assumed. `find`'s own
`-ignore_readdir_race` is the first: it takes the rate from **225/300 to
13/300** — a real improvement, but **not sufficient alone**, because a residual
transient class survives it. It also does not hide a real failure: a target that
genuinely cannot be read still returns `1` with its own message (measured: an
absent target, an unreadable directory, and a path under a regular file all
return `1`, flag or no flag). Second, the scan keeps its stderr. Third, a
non-zero scan is retried up to `AO_INFRA_SCAN_ATTEMPTS` times with
`AO_INFRA_SCAN_BACKOFF_MS` between attempts, and is called a failure only when
**every** attempt failed — which retires the residual class while leaving the
fail-closed property intact. When it does fail, it names what the tool said:

```
  FAIL  orphan-hog: cannot scan /tmp (find rc=1: find: '/tmp/x': No such file or directory)
```

A directory that genuinely cannot be scanned fails every attempt, so the
fail-closed property is intact. Two controls prove both directions: a target that
genuinely cannot be scanned still fails by name (C5), and a transient failure a
retry recovers does not red the gate (C6). Both are **uid-independent** on
purpose — `chmod 0` is invisible to uid 0, so it provokes nothing in the Cloud
Build (root) venue, the trap measured in #1381 / #1383. The free-space guard
carried the same defect class and gets the same treatment, with C7 proving its
refusal.

### The write rule on its own

The write rule is a command, not advice. Any lane that has just produced an
artifact can hold itself to it before treating the artifact as evidence:

```bash
bash scripts/check-infra-limits.sh --artifact /tmp/body.md
```

It exits `1` naming any path that is absent or 0 bytes, and reports the measured
byte count of the rest — the same check the guard applies to its own evidence
(`.verify/infra-limits-report.json`). A 0-byte body is never a body.

## What this contract does not cover

Stated honestly, so the guard is not read as more than it is:

- It polices **one** filesystem, at `maxdepth 1`. A hog nested deeper, or one on
  another mount, is not seen.
- It is a **point-in-time** measurement. A file growing between two runs is
  invisible until it is a hog at the moment the guard runs.
- It does not distinguish an **orphan** hog from one a live process legitimately
  holds — both are reported, and the remediation says to confirm with
  `/proc/*/fd` before truncating.
- It cannot police the workspace, and it does not try.
- **Wiring:** `scripts/verify.sh` registers this check by path in the
  `checks=()` array (`infra-limits|bash scripts/check-infra-limits.sh`,
  issue #729). A delivered `scripts/check-*.sh` that no gate file invokes is
  inert — issue #526's measured failure mode, recorded as `RCA-0013` — so this
  guard ships wired, in the same change that delivers it.

## Provenance

| Field | Value |
|---|---|
| Issue | #729 (EPIC #708, fleet runaway prevention) |
| Incident | `INC-0009` — the tmpfs exhaustion and the silent 0-byte write |
| RCA | [`../governance/lessons/rca/RCA-0009-infra-limits-tmpfs-exhaustion.md`](../governance/lessons/rca/RCA-0009-infra-limits-tmpfs-exhaustion.md) |
| Corrective action | `CA-0013` — this guard and this contract |
| Learning | `SUGGEST-0007` — an infra limit is only a limit once a gate measures it |
