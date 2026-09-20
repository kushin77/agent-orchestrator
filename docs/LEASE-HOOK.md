# File-lease enforcement at commit time — the opt-in `pre-commit` hook (issue #1541)

The claim ledger already records which files a lane leases
(`governance/dispatch/cli.py claim --files '[…]'`, #702) and
[`governance/policy/lease.py`](../governance/policy/lease.py) already declares the
TTL that ends a lease. Enforcement, though, stopped at two points that both
require cooperation: the `claim` CLI at claim time, and the offline lease check in
`make verify` afterwards. A session that bypassed `governance/dispatch` and
committed directly hit nothing.

[`../scripts/git-hooks/pre-commit`](../scripts/git-hooks/pre-commit) is that
missing front-end. It is **opt-in**, it **fails open**, and it is **bypassable by
design**.

## What it refuses

Exactly one thing: a commit whose **staged** file set overlaps a **live** claim
that is not this branch's.

* **Staged**, not the working tree: `git diff --cached`. Unstaged edits are never
  considered — the commit is what writes history.
* **Live**: the reader drops any claim past its TTL
  (`claims.active_claims`, TTL from `lease.CLAIM_TTL_HOURS`), so a lease that has
  expired cannot block anybody, and there is no second per-file TTL to reason
  about.
* **Overlap** is decided by the ledger's own predicate —
  `model.file_claims_conflict`, reached through `claims.find_file_conflict`. The
  hook does not re-implement it. A claim naming **disjoint line regions** of a
  file therefore does not conflict with a change outside those regions, which is
  the behaviour #702 already grants.
* **Whose claim it is** comes from the branch: `issue-<n>` names the issue, and
  the live claim on that issue supplies the `(issue, agent)` pair to exempt. So a
  file leased to *your* claim passes, and a file leased to *anybody else's* is
  refused.
* **A branch with no live claim** — the unclaimed session that the isolation
  review measured committing straight onto a leased file — has no identity to
  exempt, so its staged files are checked against every live claim. Both arms
  refuse the same thing: *this file is leased and you are not the lessee*.

A staged **deletion** of a leased path, a binary change, and any diff whose line
regions cannot be read are all treated as **the whole file** (`regions=None`,
which overlaps every region). The fallback is deliberately conservative: an
unknown region may not be reported as "no conflict".

On a conflict the hook prints the holding claim's **issue number**, its agent and
lane, the held regions, the identity it judged the commit against, and both
opt-outs — then exits 1.

## What it never refuses (it fails open)

A hook that fires on every commit must not block work it does not understand.
Each of these **allows** the commit and says why on stderr:

| situation | why it allows |
|---|---|
| not inside a git work tree | there is no repository to enforce in |
| a detached HEAD | no branch, so no lane claim can be resolved |
| a branch that is not `issue-<n>` | the shared checkout and an operator's scratch branch are not lanes |
| `python3` absent from `PATH` | the ledger cannot be read |
| `governance/dispatch/claims.py` absent, or unimportable | the reader is not available in this checkout |
| the ledger missing, unreadable or malformed | an unreadable ledger is not evidence of a conflict |
| every claim expired / no claims at all | nothing is leased |

`AO_LEASE_HOOK_OVERRIDE` is checked **before** any of the above, so a bypass is
announced even when nothing else runs.

## Installing it (an operator decision, never a side effect)

```bash
make install-hooks                    # DRY RUN: prints exactly what would be installed
AO_HOOKS_APPLY=1 make install-hooks   # installs it
```

Three properties, each measured by the gate rather than asserted here:

1. **Nothing installs it for you.** No other target runs it, `make verify` does
   not run it, and this repository never installs it into a checkout as a side
   effect of a pull request. The dry run is the default and writes nothing.
2. **It tells you the blast radius.** The destination defaults to the checkout's
   **common** hooks directory (`git rev-parse --git-common-dir`/`hooks`) — the
   directory git reads for *every linked worktree* of that checkout — and the
   dry run prints both the path and that fact before you opt in. `HOOKS_DEST`
   overrides it.
3. **It never clobbers a hook it does not own.** An existing `pre-commit` whose
   bytes differ from this repository's is refused by name and left untouched;
   `AO_HOOKS_FORCE=1` is the one documented way past that refusal.

Cost: measured in a lane worktree on 2026-09-20, importing the reader takes
**~110 ms** and the hook end-to-end (staged files resolved and checked) takes
**~194 ms**. That is the price of importing the real reader instead of
re-implementing the predicate.

## The opt-out path (both mechanisms are supported)

1. **`git commit --no-verify`** — Git's own bypass. This hook cannot observe it
   and does not pretend to. It is the mechanism to use when the hook itself is in
   the way, and it is named in every refusal message.
2. **`AO_LEASE_HOOK_OVERRIDE="<justification>" git commit …`** — honoured by the
   hook itself, and the justification is **printed** to stderr, so the bypass
   announces itself instead of being silent. Use it for a legitimate cross-lane
   fix: it leaves a greppable trace of who bypassed what and why.

**Convention.** Whichever mechanism is used, carry the same one-line
justification in the commit body, so the bypass is visible in history and not
only in the terminal that ran it. A bypass is not a violation — an *unexplained*
bypass is. The offline lease check in `make verify` remains the backstop for any
commit the hook could not see.

## The gate

[`../scripts/check-lease-hook.sh`](../scripts/check-lease-hook.sh) is the gate of
record (`make verify` runs it — `scripts/discover-checks.sh` wires every
`scripts/check-*.sh` on sight, with no hand-edit to `scripts/verify.sh`). It does
not read the hook and reason about it: it drives **real `git commit`s** in a real
scratch repository that contains this repository's own claim reader, and asserts
what the hook did.

Its arms:

* **a control** that the fixture's reader resolves a planted lease, read back
  live through `read_ledger`/`active_claims` — judged **before** anything else,
  because a fixture whose claim never landed would make every refusal arm a hook
  failing open;
* **the refusal**, naming the holder's issue number and both opt-outs;
* **four negative controls** — an unleased file, a file leased to this branch's
  own claim, a region-disjoint lease, and a non-lane branch must all commit;
* **the fail-open arms** — no ledger, a detached HEAD;
* **two mutants**, each proven to differ from the shipped hook by sha256 before
  it runs: with the conflict branch neutered the refusal must **stop** refusing
  (so the refusal comes from the predicate, not from the file existing), and with
  the region set forced to whole-file the region-disjoint arm must **start**
  refusing (so the region comparison is load-bearing rather than decorative);
* **the install path** — the dry run writes nothing, the applied install is a
  byte-identical executable copy, a foreign hook is refused and left intact, and
  the checkout's own common hooks directory is hashed before and after to prove
  the gate installed nothing;
* **the hook's static contract** — `bash -n`, the shell-pattern doctrine, and the
  unfinished-marker rule. The hook has no `.sh` suffix (git insists on the name),
  so the repo-wide shell scans cannot see it; these arms are what close that gap.

The gate never mutates the fleet's own `.board/claims/`: the fixture's ledger is
constructed for it, so what is proven is the hook's **decision**, not the current
contents of the fleet's ledger.

## Pointers

* [`../scripts/git-hooks/pre-commit`](../scripts/git-hooks/pre-commit) — the hook.
* [`../scripts/check-lease-hook.sh`](../scripts/check-lease-hook.sh) — its gate.
* [`LEASE-POLICY.md`](LEASE-POLICY.md) — the declared lease/TTL policy the hook's
  liveness rule reads.
* [`../governance/dispatch/README.md`](../governance/dispatch/README.md) — the
  claim protocol, including `--files` leases (#702) and the arbitration that
  refuses an out-of-order claim.
* [`../AGENTS.md`](../AGENTS.md) — rule 15 (session identity and lane isolation),
  which is what makes `issue-<n>` the branch's identity.
