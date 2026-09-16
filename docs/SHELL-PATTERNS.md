# Shell patterns — the shapes this repository refuses (EPIC #616 · issue #621)

> **Rule.** Every shell script this repository ships is written to the patterns
> below, and the ones marked **ENFORCED** are refused mechanically — by name —
> by [`scripts/check-shell-patterns.sh`](../scripts/check-shell-patterns.sh),
> which [`scripts/discover-checks.sh`](../scripts/discover-checks.sh) auto-discovers
> (#698), so the gate runs in `make verify` with no hand-edit to
> [`scripts/verify.sh`](../scripts/verify.sh) or the `Makefile`.
>
> A refusal is the gate teaching you the shape. Every entry below carries the
> good shape that replaces the bad one, and the fix is almost always one line.

Two things this doctrine deliberately does **not** do. It does not confuse
*declared* with *enforced* — a pattern that could only be enforced by reddening
`master` today is graded **DECLARED** below, with the measured sites and the issue
that retires them, rather than being silently grandfathered, allowlisted, or
softened into a rule that matches nothing (GR-12). And it does not state a rule
without the failure that put it there: each pattern was harvested from a lesson
that was paid for somewhere, and the ones this box paid for are marked as such.

## Provenance (GR-10)

Harvested and **adapted** from the fleet hub's canonical doctrine — not copied.

| | |
|---|---|
| Source repo | `kushin77/CMR` |
| Source doc | `docs/SHELL-PATTERNS.md` @ `15b32671d06376f4fe1a5b55ffc2b02cd1a34c41` (2026-09-12, "add check-silent-catch gate and record GR-32") |
| Source gate | `scripts/check-shell-patterns.sh` @ `f8e3cd259849e731ee587b6d0ac58e20e2cc08fe` (2026-09-13) |
| Adapted by | issue #621, EPIC #616; measured against this repository at `dc3de7f0` (2026-09-16) |
| License | the hub checkout carries no `LICENSE`/`COPYING`/`NOTICE` at its root (measured 2026-09-16), so no license is asserted here: the provenance is the commit, not a claim |
| Not present locally | `vendor/CMR` (the pinned submodule) does not carry `docs/SHELL-PATTERNS.md` at its pinned commit, so the source is named by repo + commit rather than by a local path |

**What was adapted, what was dropped, and why.** CMR's doctrine assumes a fleet
hub whose scripts talk to GitHub through a pinned `GH=` indirection and whose
files carry a DR-045 header block. This repository does neither (measured: **0**
files resolve `gh` through a `GH=` indirection; **0** files carry a DR-045
header), so those two patterns are **not adopted as rules** — see the gap table.
CMR's gate also *skips its own source* (`SELF_REL`, the LESSON-008 self-match
doctrine); this one is **scanned like any other shell file**, and every shape it
looks for is assembled there from fragments, so the checker cannot be a hiding
place (the self-test asserts exactly that, and it is a strictly stronger
property than an exemption). Finally, CMR's nine patterns are its own fleet's
lessons; the set below is the intersection of those lessons with failures
**measured on this box**, plus two shapes this repo paid for and the hub never
had.

## The patterns (ENFORCED)

| id | the shape | the failure it prevents | local example |
|----|-----------|-------------------------|---------------|
| `SP-1` | a cleanup trap armed on the `RETURN` pseudo-signal | it re-fires on every later function return, and under `set -u` the captured scratch path is already out of scope: the script dies *after* the real work | 89 sites use the good shape (`trap … EXIT`), e.g. [`check-docs.sh`](../scripts/check-docs.sh); 0 arm `RETURN` |
| `SP-2` | a standalone field-separator assignment line | it collapses adjacent tabs and empty fields, so a TSV row is mis-split and a field silently shifts | the same-line idiom is the repo's habit: `IFS='\|' read -r -a parts <<< "$entry"` (14 sites, e.g. [`check-reconcile.sh`](../scripts/check-reconcile.sh)) |
| `SP-3` | `IFS=','` followed by `read` on one line | a lone `read` consumes the whole record; the extra names stay empty and every later field is read from the wrong value | 0 sites — the split is done with `tr ',' '\n'` *before* the loop, as CMR's lesson records |
| `SP-4` | a shell function named after an external command (`gh`, `git`, `jq`, …) | it shadows the binary for the whole shell, so a later check reads the stub and reports green | 0 sites; 11 shell files call `gh` (measured), and no file pins it, so a stub would be read as an answer by all of them |
| `SP-5` | signalling by name (`pkill` / `killall`) | on this shared box it matches **every other lane's worker**: one such call matched 14 processes box-wide, a gate's worker died mid-run, and a *neighbour* reported a failure that was not its own | kill by recorded PID: `kill -TERM "$pid"` ([`check-fleet-runner-preflight.sh`](../scripts/check-fleet-runner-preflight.sh)) |
| `SP-6` | declaring and capturing in one statement (`local x=$(cmd)`) | the declaration's own status masks the command's: a failed command looks successful, and the branch that reports it never runs | the two-step form, used throughout: `local out` then `out="$(…)"` |
| `SP-7` | a fetch piped straight into a shell (`curl … \| bash`) | it executes whatever the network answers with: no review, no pin, no digest, last-write-wins | 0 sites — infra is declared and flag-gated (GR-5), never installed from a pipe |
| `SP-8` | a bare `gh issue view <n>` in command position | it goes through the deprecated classic-Projects query and returns empty/stale output on these repos, so the title and body a lane acts on are the wrong ones | 0 sites; the repo's own environment notes already require REST ([`AGENTS.md`](../AGENTS.md)) |

**Enforced by**, for every row: the gate's own provocation. Each pattern has a
planted violation (`plant-<id>.sh`), and the gate refuses each plant **by name** —
`SP-<id>`, the label, the file and line, the code the rule matched, and why —
*before* it looks at the repository. The refusal a real file gets is byte-for-byte
the same report.

### SP-1 — one global scratch + one `EXIT` trap, never a `RETURN` trap

**What it is.** Cleanup declared with the `RETURN` pseudo-signal, typically armed
inside a function right after the scratch directory is created.

**The failure it prevents.** A `RETURN` trap is armed on the *return* of every
function, and it keeps firing while installed — including for functions that are
nothing to do with the scratch path. Under `set -u` (which every script here sets)
the captured variable is out of scope by then, so the trap dies with an unbound
variable read: the work is done, the script exits non-zero, and the failure looks
like a work failure.

```bash
# BAD — armed per call, fires again on every later return, dies under set -u
run_one() {
  local d; d="$(mktemp -d /tmp/x.XXXXXX)"
  trap 'rm -rf "$d"' RETURN
}

# GOOD — one global scratch variable, one EXIT trap, `|| true`-safe body
TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT
```

The gate itself is written this way (`TMPD`, `cleanup`, one `trap cleanup EXIT`) —
a doctrine whose own instrument does not obey it teaches nothing.

### SP-2 — the field separator belongs on the `read`, not on its own line

**What it is.** A line that assigns the field separator and nothing else, above a
later `read` ("the IFS-set-then-read leak").

**The failure it prevents.** The assignment mutates the field splitting of
everything that follows, and it collapses adjacent tabs and empty fields — so a
TSV record with an empty field silently shifts every field after it. The row looks
parsed; it is mis-split.

```bash
# BAD — IFS leaks onto its own line and collapses empty fields
IFS=$'\t'
read -r state labels < "$row"

# GOOD — same-line read: one record, field-split, no leak
while IFS=$'\t' read -r state labels; do
  printf '%s\n' "$state"
done < "$row"
```

### SP-3 — a comma list is not split by one `read`

**What it is.** `IFS=','` on the same line as a `read` with several names, used to
split a comma-separated value.

**The failure it prevents.** A single `read` consumes the whole record and assigns
it to the first name; every later name gets nothing, and the code that follows
reads fields that were never split. The fix is to normalize *before* the loop.

```bash
# BAD — one string, not one field per name
IFS=',' read -r one two three <<< "a,b,c"

# GOOD — normalize, then read one value per line
while IFS= read -r label; do
  printf '%s\n' "$label"
done < <(printf '%s\n' "$labels" | tr ',' '\n')
```

### SP-4 — nothing in this repository defines a function named after a binary

**What it is.** A shell function whose name is an external command (`gh`, `git`,
`jq`, `grep`, `sed`, `awk`, `curl`, `python3`, `mktemp`, …), the shape a test
harness reaches for when it wants to stub a tool.

**The failure it prevents.** The function replaces the binary for the whole shell
— not for one call. A check that stubs `gh`, then reads a value through `gh`
later, reads the stub and reports green; the failure is silence, which is the one
thing a gate must never produce. Where a tool must be injected, it is injected
through a *variable* the caller can set (`GH="${GH:-/usr/bin/gh}"`), never by
shadowing the name.

```bash
# BAD — the stub replaces the binary for the rest of the shell
gh() { :; }

# GOOD — inject through a variable, call "$GH"; tests shadow the variable
GH="${GH:-/usr/bin/gh}"
state="$("$GH" api "repos/$REPO/issues/$N" --jq .state)"
```

### SP-5 — kill the PID you recorded, never a name

**What it is.** Signalling a process by name (`pkill`, `killall`), usually to
"reduce contention" or clean up a helper.

**The failure it prevents.** Measured on this box: a call aimed at one lane's own
worker matched **14 processes box-wide**, because the same script name runs in
every other lane's gate at the same time. A gate's worker dying mid-run makes a
**neighbour's** gate report a failure that is not its own — the exact thing lane
isolation exists to prevent. Kill the PID you recorded (`$!`, a pidfile), or
narrow the match to your own worktree path and check every hit's `cwd` first.

```bash
# BAD — kills every lane's worker of the same name
pkill -f "bash scripts/verify.sh"

# GOOD — record the PID, signal that PID
setsid nice -n 10 bash "$driver" >"$log" 2>&1 &
pid=$!
kill -TERM "$pid" 2>/dev/null || true
```

### SP-6 — declare, then capture

**What it is.** `local x=$(cmd)` — a declaration whose value is a command
substitution.

**The failure it prevents.** The status reported is the *declaration's*, not the
command's, so a failing command reads as a success. A check that captures in this
shape never sees its own probe fail, and the branch that would have reported it is
unreachable.

```bash
# BAD — the command's status is discarded; the capture "succeeded"
local head=$(git rev-parse HEAD)

# GOOD — declare, then capture; the status is the command's
local head
head="$(git rev-parse HEAD)" || exit 2
```

### SP-7 — nothing fetches and executes in one pipe

**What it is.** `curl`/`wget` piped into `bash`/`sh`.

**The failure it prevents.** The bytes that run are whatever the network answered
with: unreviewed, unpinned, undigested, and installed last-write-wins. This
repository's infrastructure is declared (GR-5) — tools arrive through IaC, not
through a pipe — so the shape is refused on sight rather than argued about.

```bash
# BAD — the network decides what runs
curl -fsSL https://example.invalid/install.sh | bash

# GOOD — fetch to a file, verify, then run (or declare it in IaC)
curl -fsSL -o /tmp/install.sh https://example.invalid/install.sh
sha256sum -c /tmp/install.sh.sha256 || exit 2
```

### SP-8 — read the board through REST, not through `gh issue view`

**What it is.** A bare `gh issue view <n>` in command position.

**The failure it prevents.** On these repositories it reads through the
deprecated classic-Projects query and comes back empty or stale — the local-code-first
first note in [`AGENTS.md`](../AGENTS.md) records it, and this repository has
paid for it (a lane acted on a title/body that never existed). The fix is REST:
`gh api repos/<owner>/<repo>/issues/<n>`, or a whole-board list.

```bash
# BAD — deprecated query: empty or stale, and it looks like a real answer
gh issue view 621 --json title,body

# GOOD — REST, deterministic
gh api repos/kushin77/agent-orchestrator/issues/621 --jq '.title, .body'
```

## How the gate proves itself (GR-12)

A gate that cannot fail is a formality, so the provocation runs **on every
invocation**, before the repository is read at all — and it drives the *same*
`scan_paths` function the repository run uses, never a copy:

```
$ bash scripts/check-shell-patterns.sh
== the patterns, provoked ==
  OK    each of the 8 planted violations is refused, by name
  OK    a file with no violation produces no finding
  OK    the same shape inside a comment is not an occurrence (a comment cannot run)
  OK    the checker's own source carries no occurrence (shapes are assembled from fragments)
  OK    every detector is load-bearing: with it disabled, its own plant is accepted and others are not
check-shell-patterns: self-test OK — 8 patterns, refused by name, both ways
  scanned 162 shell file(s)
check-shell-patterns: OK — 8 patterns clean over 162 shell file(s), 0 corpus fixture(s) reported
```

Each half exists because its absence is a specific hole:

- **refused by name** — a rule that matches nothing, or that refuses everything if
  any one thing is wrong, would pass a coarser assertion. Each pattern has its own
  plant and its own label, so "8 of 8 refused" is per pattern, not per batch.
- **vacuity** — a clean file must produce **no** finding, or the refusals above
  could come from a rule that matches everything.
- **the comment half** — the same shape inside a comment must **not** be refused.
  A comment cannot run, so it cannot be an occurrence; and without this half the
  rule would refuse the prose that documents it. (Issue #804 measured exactly that
  trap one level up, in the marker rule, where the explanation of the rule tripped
  it.)
- **the checker's own source** — the checker is scanned like any other file, so if
  any shape were spelled whole in it, this is where it would show. Every literal
  is assembled from fragments.
- **load-bearing, by mutation** — for each of the 8 patterns the gate builds a
  copy of itself with **that one detector** replaced by a rule that matches
  nothing, and requires two things: its own plant is then *accepted*, and another
  pattern's plant is still *refused*. The first says the refusal came from the rule
  and not from the file existing; the second says the mutation disabled one rule
  rather than breaking the script. A control that cannot fail is a formality, and
  this is the half that stops the *proof* from being one.

The tri-state contract is exercised too: a clean file exits `0`, a violating file
`1`, a missing file or an unknown argument `2` (CANNOT-ASSESS — never a pass).

A real refusal, on a real file planted in the tree (removed again before commit):

```
$ bash scripts/check-shell-patterns.sh
  REFUSED  SP-1 trap-return
           scripts/zz-negative-control.sh:9
             trap 'rm -rf "$d"' RETURN
           why: a cleanup trap armed on the RETURN pseudo-signal fires again on every
                later function return, and under set -u the captured scratch path is
                out of scope by then: the script dies after the real work
  REFUSED  SP-6 capture-in-declaration   scripts/zz-negative-control.sh:10
  REFUSED  SP-5 signal-by-name           scripts/zz-negative-control.sh:11
check-shell-patterns: NOT-OK — 3 occurrence(s) refused above (docs/SHELL-PATTERNS.md)
```

## What the gate scans, and what it does not

- **In scope:** every tracked `*.sh` plus every untracked-but-not-ignored one —
  the same "tree the repository owns" rule as
  [`check-docs.sh`](../scripts/check-docs.sh), so a lane's new script is scanned
  before it is committed, and gitignored runtime state (a gate must not read an
  artifact the repo does not own, #764) never is.
- **The code part of a line only.** A shape is an occurrence if it is in the code;
  the comment half of the line is not an occurrence (a comment cannot run).
- **A `fixtures/` path is reported, not counted.** A negative-control corpus *is*
  the definition of the violation, so such findings are printed on every run with
  a `NOTE` prefix instead of failing the gate. That is a deliberately bounded
  hole: a production script cannot sit under a directory named `fixtures/` without
  being printed every time the gate runs.
- **Named gap — not covered:** Makefiles, and the bodies of inline heredocs. A
  shell shape can hide in both (the hub's doctrine carries a Makefile-heredoc
  pattern for exactly that reason). This gate does not claim them; they belong to
  the follow-up below rather than to a silent assumption of coverage.

## Declared, measured, not yet enforced

Enforcing any of these today would red `master` on land, and a gate that is red on
master is either disabled or ignored — strictly worse than no gate. So they are
measured here, graded honestly, and retired by **[#877](https://github.com/kushin77/agent-orchestrator/issues/877)**
(the two with sites) rather than grandfathered. When #877 lands, these rows move
into the enforced table above.

| id | the shape | measured (2026-09-16) | why it is declared, not enforced |
|----|-----------|-----------------------|----------------------------------|
| `SP-9` | a scratch directory created with no template — it lands in the shared, periodically-cleaned `$TMPDIR`, which is not private, and can vanish mid-run | **5 sites**: `check-chronological-dispatch.sh:106`, `check-fleet-runbook.sh:130`, `check-orphan-handoff.sh:173`, `check-terraform.sh:59`, `check-verdict-contains.sh:214` | the good shape is already the majority habit (**10** sites use `mktemp -d /tmp/<name>.XXXXXX`, and [`SCRATCH-SPACE-DISCIPLINE.md`](SCRATCH-SPACE-DISCIPLINE.md) is the canonical statement), so this is a six-line retrofit — [#877](https://github.com/kushin77/agent-orchestrator/issues/877) |
| `SP-10` | a `cd` whose failure is not handled | **1 site**: `infra/cloudflare/ao-ssh-access.sh:68` | same retrofit — [#877](https://github.com/kushin77/agent-orchestrator/issues/877) |
| CMR §3 | `gh` reached through a pinned `GH=` indirection | **0** files use a `GH=` indirection; **11** shell files call `gh` | adopting the rule means editing 11 files' call sites in one lane, and nothing owns that lane today; the *enforceable* half (SP-4) already refuses the shadowing mechanism |
| CMR §4 | a gh state compared against a lowercase literal | **0** sites with the narrow detector | a bracket test on a variable cannot be told from a legitimate comparison without knowing where the value came from; the hub anchors its detector to a harness probe, and that probe is a lane of its own |
| CMR §5 | mode flags resolved last-wins in a hand-rolled argument loop | **18** loops iterate `"$@"`; **10** sites already parse with `while [ $# -gt 0 ]; case` ([`check-fleet-template.sh`](../scripts/check-fleet-template.sh)) | the bad shape is structural (a loop *body* that assigns a mode variable), not a line shape; a line-level rule cannot express it without false refusals on legitimate per-file loops |

See [`GIT-TEMPLATES-GAP-ANALYSIS.md`](GIT-TEMPLATES-GAP-ANALYSIS.md) for the gap
that created this lane (#608's measurement: a repo with no shell-pattern doctrine
while the hub carries a gate-enforced one).

## Enforced elsewhere — do not re-implement here

One shape in this family already has a gate, and this doctrine names it rather
than duplicating the detector:

- **A pipe into a quiet `grep` (`cmd | grep -q`) is not a containment test.** The
  producer is killed by SIGPIPE on `grep`'s first match, and `set -o pipefail`
  promotes that 141 to the status of the whole pipeline — so under a negated test
  the answer is `absent` for text that is present, and the branch that proves a
  control works is the branch that gets skipped. It is refused, with its own
  shrink-only record of legacy sites, by
  [`check-verdict-contains.sh`](../scripts/check-verdict-contains.sh) (#852/#866),
  and the bash-native containment forms it asks for are what this gate's own
  `.verify`-reading siblings use. **Enforcer:** that gate — not this one.

## Using this doctrine

1. Write a new shell script the way an existing one is written: the good shapes
   above are the majority habit, not an aspiration.
2. Run the gate on what you wrote: `bash scripts/check-shell-patterns.sh --files scripts/your-new.sh`.
   `--list` prints the pattern table; `--help` the usage.
3. Read a refusal as a teaching moment, not a wall: it names the pattern id, the
   label, the file and line, the matched code, and why the shape fails — and the
   good shape for that id is a section above.
4. `make verify` runs the gate on every pass, so a violation that reaches a branch
   is refused before it reaches a review.

A pattern is added here only with (a) a failure that was paid for — measured on
this box or recorded in a lesson — (b) a detector that is a *line shape*, and (c)
a plant that proves it refuses by name. A pattern that cannot meet all three is
declared in the table above, with its measurement, and is not pretended away.
