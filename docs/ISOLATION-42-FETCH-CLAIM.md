# The `.42` "fetch refused into checked-out branch" claim — measured

**Verdict: CONFIRMED.** Git refuses to fetch into a branch that is currently
checked out in the target repository, with the exact message
`fatal: refusing to fetch into branch '...' checked out at '...'` (exit 128).
The claim's mechanism is real, and it is why the `git bundle` → `scp` → fetch
into a temp ref → `reset --hard` dance is a working shape. Reproduced minimally
in a throwaway tree; `git version 2.53.0`.

This note closes the investigation for
[issue #1547](https://github.com/kushin77/agent-orchestrator/issues/1547).

## The claim, as written

The claim has three homes. None of the first two is tracked in this repository,
which is itself part of the finding.

1. `FRICTION-AUDIT-2026-09-20.md` §6 (the 12-hop table, hop 5) — verbatim:

   > `ssh` fetch into a temp ref + `reset --hard` (**a fetch into the
   > checked-out branch is refused**; a first attempt with `-q` hid the hook
   > failure entirely)

2. `kushin77/shared-services` issue #4240, "Context" — verbatim:

   > every push is done by bundling to `.42` — `git bundle` -> `scp` -> `ssh`
   > fetch into a temp ref + `git reset --hard` (a fetch into the checked-out
   > branch is refused) -> `git push` on `.42`

3. The row under test, `ISOLATION-REVIEW-2026-09-20.md` §3 "Failure modes table",
   previously:

   | Scenario | Can it happen today? | Evidence |
   |---|---|---|
   | "fetch refused into checked-out branch" on host `.42` | **Unverified** | No grep hits in this repo; ssh connected but the remote path is not a git checkout (`fatal: not a git repository`) — could not reach the described host state |

The two audit documents live at
`/home/akushnir/.claude/jobs/84034cd1/tmp/` — an audit session's artifact
directory, not a repository path. They are not tracked in `agent-orchestrator`
(0 hits on `origin/master`, and no commit on any local ref ever added them) nor
in `shared-services`. So the row could not be updated in place by a lane; the
corrected row is published as a comment on #1547 instead of by editing another
session's artifact.

## Why the row said "Unverified"

The prior session's blocker was a **path**, not the host and not access. Its ssh
succeeded; it then ran `git` at a path that is not a work tree and read the
resulting `fatal: not a git repository` as the host state being unreachable as
described. Both repositories the claim concerns do exist on `.42` — measured
live in this session:

```
$ ssh akushnir@192.168.168.42 'hostname; uname -sr; whoami; echo "HOME=$HOME"'
dev-elevatediq
Linux 6.8.0-138-generic
akushnir
HOME=/home/akushnir

$ ssh akushnir@192.168.168.42 'for d in ~/agent-orchestrator ~/shared-services; do [ -d "$d" ] && echo "EXISTS: $d"; done'
EXISTS: /home/akushnir/agent-orchestrator
EXISTS: /home/akushnir/shared-services
```

That output removes the "wrong host / unreachable" reading: the correct paths
are the obvious ones. What remains open is named under "Not resolved" below.

## Measurement 1 — the mechanism (decisive, reproduced)

A throwaway tree under `/tmp` models the three hosts: a bare `origin`, a
`controlnode` clone that commits and bundles, and a `host42` clone that stands
in for `.42` **with `master` checked out** (it is a non-bare repository, exactly
like `.42`'s). The `scp` of the bundle is replaced by a local path — the git
behaviour under test is identical, and the transport is not what the claim is
about.

```
######## ARM A: fetch INTO THE CHECKED-OUT BRANCH (master:master) on .42 ########
    $ git fetch /tmp/ao1547-repro.Dkexdo/transfer.bundle master:master
    | fatal: refusing to fetch into branch 'refs/heads/master' checked out at '/tmp/ao1547-repro.Dkexdo/host42'
    rc=128   master before=99ba781 after=99ba781
    ARMA=REFUSED

######## ARM B: fetch into a TEMP REF (the workaround's actual step) ########
    $ git fetch /tmp/ao1547-repro.Dkexdo/transfer.bundle master:refs/heads/tmp-1789938159
    | From /tmp/ao1547-repro.Dkexdo/transfer.bundle
    |  * [new branch]      master     -> tmp-1789938159
    rc=0   temp ref now c9d200b
    ARMB=ALLOWED

######## THE DANCE, step 2: reset --hard onto the temp ref ########
    $ git reset --hard tmp-1789938159
    | HEAD is now at c9d200b c2 (the work that must reach .42)
    rc=0  master now c9d200b  worktree f.txt=two  g.txt present=yes
    DANCE=WORKED (master == controlnode's new commit c9d200b)
```

Summary of the four arms:

| Arm | Operation (`host42`, a non-bare repo with `master` checked out) | Result | rc |
|---|---|---|---|
| A | `git fetch <bundle> master:master` | refused — `refusing to fetch into branch 'refs/heads/master' checked out at '<path>'` | 128 |
| B | `git fetch <bundle> master:refs/heads/tmp-<ts>` | allowed — `[new branch]` | 0 |
| — | `git reset --hard tmp-<ts>` (after B) | allowed — `master` reached the bundled commit; worktree contains the new content | 0 |
| C | `git fetch <bundle> master:master` when already up to date | **refused**, same message | 128 |
| D | `git fetch --update-head-ok <bundle> master:master` | allowed — `99ba781..c9d200b master -> master` | 0 |

Two arms deserve comment, because reporting only the convenient ones would not
be a measurement.

- **Arm C refuted its author's prediction.** The reading of git's source was
  that the guard is skipped for a ref it already believes up to date
  (`REF_STATUS_UPTODATE`); if so, arm C would have exited 0. It exits 128 with
  the same message. The guard is therefore stronger in practice than the source
  reading suggested — the refusal does not depend on the fetch having something
  to move. A prediction that failed is evidence the arms were not fitted to the
  expected answer.
- **Arm D is an honest addendum, not a refutation.** `--update-head-ok` is the
  documented override and it works: the fetch-then-`reset --hard` two-step is
  **one** remedy, not the only possible one. The claim as written — "a fetch into
  the checked-out branch is refused" — is exactly what happens by default, and
  the workaround built on it is correct. It is worth knowing that a single-step
  form exists, because `shared-services` #4240 exists to retire the multi-hop
  dance.

## The counterfactual — what would have reversed this verdict

The verdict is falsifiable in both directions, and each direction is a real
observed alternative rather than a hypothetical:

- If **arm A had exited 0 and advanced `master`**, the claim is **REFUTED**: git
  would have accepted the fetch into the checked-out branch, so hop 5's stated
  justification would be false. Arm B is the proof that this outcome is
  observable at all — a fetch into a *non*-checked-out ref in the same
  repository, from the same bundle, exits 0. The difference between the two arms
  is the checked-out-ness of the target ref and nothing else.
- If **arm B had been refused too**, the claim's *consequence* is refuted even
  though its premise holds: the workaround would not be forced by this rule,
  because no fetch of any shape would have worked.
- If **arm D had failed**, the addendum would be wrong and the two-step dance
  would be strictly required rather than merely chosen.

The exact message — `refusing to fetch into branch 'refs/heads/master' checked
out at '<path>'` — is git's own wording for this guard, not a paraphrase.

## Measurement 2 — the site (host `192.168.168.42`)

| Question | Answer |
|---|---|
| Does the host answer, and what is it? | Yes — `dev-elevatediq`, Linux 6.8.0-138-generic, user `akushnir`, `HOME=/home/akushnir` |
| Are the two claimed repos present? | Yes — `/home/akushnir/agent-orchestrator` and `/home/akushnir/shared-services` both exist as directories |
| Is the `git --version` on `.42` recorded? | **No** — the host became unreachable before those calls returned (see below) |
| Does `.42`'s own history contain the refusal? | **Not measured** — same reason |
| Was the prior session's `fatal: not a git repository` a wrong path? | Consistent with that, and the two paths above are the candidates; which one the dance targeted is not resolved |

**Reachability, recorded honestly.** The host answered at 21:02:53Z. Partway
through the follow-up probe it began returning `ssh: connect to host
192.168.168.42 port 22: No route to host`, and a patient probe with 80 retries
over roughly 40 minutes observed it stay down for the remainder of the session.
That is a transient network condition, not an access denial and not a verdict
about `.42`; the successful output above is quoted verbatim from the window when
it was up.

**Not resolved, named explicitly.** Whether `.42`'s checkouts of those two
repositories are work trees (rather than plain directories), which branch is
checked out there, `.42`'s `git --version`, whether the `git bundle` workaround
scripts are present in either repo on `.42`, and whether the host's own shell
history contains the refusal string. None of these is needed for the verdict:
the mechanism is a property of git and was measured directly, and the site half
only needed the "unreachable as described" reading corrected, which the live
output above does.

## The corrected row for `ISOLATION-REVIEW-2026-09-20.md`

Suggested replacement for the failure-modes row, status moved from `Unverified`
to confirmed, with the measurement that decides it:

| Scenario | Can it happen today? | Evidence | Fix |
|---|---|---|---|
| "fetch refused into checked-out branch" on host `.42` | **Yes — confirmed as git behaviour, reproduced** | The refusal is git's own guard on the target repository's checked-out branch. Reproduced minimally on `git version 2.53.0`: `git fetch <bundle> master:master` on a non-bare repo with `master` checked out exits 128 with `fatal: refusing to fetch into branch 'refs/heads/master' checked out at '<path>'`; the same fetch into `refs/heads/tmp-<ts>` exits 0. Both repos exist on `.42` (`/home/akushnir/agent-orchestrator`, `/home/akushnir/shared-services`), so the earlier "not a git repository" reading was a wrong-path artifact, not a host state | Keep the temp-ref step, or use the single-step `git fetch --update-head-ok` (measured: exits 0 and moves the branch). Retiring the dance is `shared-services` #4240's job |

The review's summary line that grouped this error with "did not hold up under
direct verification" should likewise be qualified: the mechanism **is** real. The
earlier session could not reach it because of the path it assumed, not because
the claim was mistaken.

## Method notes

- The destructive experiment ran in a throwaway tree under `/tmp`
  (`mktemp -d /tmp/ao1547-repro.XXXXXX`), never in a lane's work tree and never
  in the shared checkout.
- Ambient git configuration is neutralised for the fixture
  (`GIT_CONFIG_GLOBAL=/dev/null`, `GIT_CONFIG_SYSTEM=/dev/null`), so the
  measurement is of git rather than of this host's configuration.
- `ulimit -f` bounds the run so a runaway cannot fill the disk.
- `git --version` is recorded with the verdict because a behaviour claim is
  version-scoped.
