# Re-verifying the "issue #4210" claim against its actual GitHub body

Lane `issue-1546`, parent epic #1510. Verification-only: this document is the deliverable — the
measurement that decides the claim, not a fix. The fix, where one turned out to be warranted, is
its own lane ([#1642](https://github.com/kushin77/agent-orchestrator/issues/1642)).

## The verdict, split

#1546 bundles five assertions. They do not share an answer, so they are adjudicated separately
rather than answered with one word.

| assertion, as #1546 phrases it | verdict | what decides it |
|---|---|---|
| "issue #4210" is an issue **of this repo**, describing a repro in this repo | **REFUTED** | It is a **merged pull request** in `kushin77/shared-services`; its three files are shared-services paths and none exists here |
| a prior review "found zero trace of issue #4210 in commit history or grep of this repo" — so the number may not exist at all | **REFUTED** | The number resolves: `kushin77/shared-services` pull request #4210 |
| "#4210 describes a `git -C` call that doesn't respect `GIT_DIR` in a risky, write-capable context" | **CONFIRMED** | Quoted from its body in section 2, and independently re-measured in section 4 |
| the prior session's "the two `git -C` call sites [here] are both safe `rev-parse` reads" | **REFUTED** | Census: **285** `git -C` matches across 33 files, **141** of them write-capable (section 5) |
| the risk, as it applies to **this** repo | **real, and latent** | this repo's own fixture-seeding gates carry the defect; nothing in-repo exports `GIT_DIR` today (section 6) |

Net: the claim's **attribution** is refuted, its **mechanism** is confirmed, and the mechanism
reaches this repository's own gates. That second half is not what #1546 sent the lane to look for
— it is what the measurement found on the way.

## 1. Where #4210 actually lives

`gh issue view` is not the decisive instrument here, and it misleads twice. Verbatim, all three
repositories #1546 named:

```
$ gh issue view 4210 -R kushin77/agent-orchestrator
GraphQL: Could not resolve to an issue or pull request with the number of 4210. (repository.issue)
rc=1

$ gh issue view 4210 -R kushin77/shared-frontend
GraphQL: Could not resolve to an issue or pull request with the number of 4210. (repository.issue)
rc=1

$ gh issue view 4210 -R kushin77/shared-services
GraphQL: Projects (classic) is being deprecated in favor of the new Projects experience, see:
https://github.blog/changelog/2024-05-23-sunset-notice-projects-classic/.
(repository.issue.projectCards)
rc=1
```

The third line is the trap, and it is the one this repo has already legislated against: SP-8 in
`docs/SHELL-PATTERNS.md` refuses a bare `gh issue view` precisely because it "comes back empty or
stale — and it looks like a real answer". Here it came back as a **deprecation error on the
repository that did hold the number**: a rendering failure (a deprecated project-card field) that
reads exactly like "not found". Read literally, all three lines are the same answer, and that
answer is wrong. The REST route is unambiguous:

```
$ gh api repos/kushin77/shared-services/issues/4210 --jq '.state, .title'
closed
[CLI] fix(host-firewall): host-independent ssh_key trigger + stop test suite leaking into the real repo — Issue #3867
```

and the same call is a genuine 404 elsewhere:

```
$ gh api repos/kushin77/agent-orchestrator/issues/4210
{"message":"Not Found","documentation_url":"...issues#get-an-issue","status":"404"}
$ gh api repos/kushin77/shared-frontend/issues/4210
{"message":"Not Found","documentation_url":"...issues#get-an-issue","status":"404"}
$ gh api repos/kushin77/CMR/issues/4210
{"message":"Not Found","documentation_url":"...issues#get-an-issue","status":"404"}
$ gh api repos/kushin77/shared-governance/issues/4210
{"message":"Not Found","documentation_url":"...issues#get-an-issue","status":"404"}
```

**Resolved repository: `kushin77/shared-services`** — and the entity is a **pull request**, not an
issue (`html_url` is `.../pull/4210`):

```
merged=true  state=closed  merged_at=2026-09-19T14:15:35Z
merge_commit=dc41f56a4c89bd9bcb1e7111df8f77db71490a6a
base=main  head=lane/3867-host-firewall-ssh-key-trigger  changed_files=3
```

Its three files are `infra/MODULE-LOCK.json`, `infra/modules/host-firewall/main.tf` and
`scripts/tests/propagate-node-checkout-classify.test.sh` — **all shared-services paths**. Nothing
at those paths exists in this repo, so the repro #1546 went looking for here was never here. (The
resolution was not a guess: the `kushin77` org's 114 repositories were enumerated, and the five that
could plausibly hold a 4-digit issue number — `agent-orchestrator`, `shared-services`,
`shared-frontend`, `CMR`, `shared-governance` — were probed by REST; one answered 200.)

The repository's own cross-repo references agree on the number *range* without naming this one:
`docs/CROSS-REFERENCE-SPINE.md` and the four `docs/CROSS-REPO-*.md` files carry no `4210`, the peer
board (`governance/sync/peer-board/`) carries none either, and the same grep finds this repo's
recorded shared-services edges in the 4000s (`kushin77/shared-services#4040`, `#4192`) against this
repository's own ~1600. So the number belonged to the other repo's range, and no recorded reference
had to be reconciled — the resolution is from REST, and the references corroborate it rather than
source it.

## 2. What #4210 actually says, quoted

From its body, verbatim:

> Root cause: git exports `GIT_DIR` to hooks (always in a linked worktree, where `.git` is a
> file), and **`git -C <path>` does not override an exported `GIT_DIR`**. Every containment guard
> written for #2663/#2669 used `git -C "$FIX"`, so under the hook `git -C "$FIX" init`
> re-initialised the real gitdir (`re-init: ignored --initial-branch`) and `add -A` staged the
> 4-file fixture as the whole tree.

> Fix: `unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_COMMON_DIR GIT_PREFIX`
> before anything runs, plus an assertion immediately after `init` that the fixture's resolved
> gitdir is `$FIX/.git` and not the real one.

And the consequence it measured there:

> On the first push of this branch, `pre-push → lint-all.sh` ran the suite and it committed `c1`
> (author `fixture <test@example.invalid>`) on top of the lane, deleting 7,286 tracked files, and
> rewrote the *shared* local git identity to the fixture's.

Its own comment records a fourth recurrence twenty minutes later, on a different lane. So the
mechanism is not speculative: a sibling repo hit it four times, and fixed it there on 2026-09-19.

## 3. The one assertion that mattered, and why it was wrong

The prior session's grep concluded that this repo holds exactly two `git -C` call sites,
`governance/lifecycle/tests/test_reclaimed_lane_evidence.py:110` and `scripts/check-landing.sh:497`,
both safe `rev-parse` reads. Both of those lines are real:

```
governance/lifecycle/tests/test_reclaimed_lane_evidence.py:110
  'printf \'%s %s\\n\' "$PWD" "$(git -C "$PWD" rev-parse HEAD)" >> "$STUB_GATE_CWD"\n'
  --> a string being *written into* a stub script; a read when the stub runs

scripts/check-landing.sh:497
  lane_new "$fx"
  --> not a `git -C` line at all: it is the line number a later grep landed on, inside the
      evidence-controls block; the file's own `git -C` calls are at 239, 241, 348-367, 516, 737
```

The conclusion drawn from them — "two call sites, both reads" — is off by two orders of
magnitude, and it is the assertion a follow-up decision would have rested on. Section 5 is the
census that replaces it.

## 4. The reproduction that decides it

Run in throwaway repositories under `/tmp` only — never a lane's tree, never the shared checkout,
and no real repository used as a victim. Ambient git configuration was left at default so the
measurement is of git, not of this host.

### 4a. Does git export `GIT_DIR` to a hook?

```
=== pre-push, from the MAIN worktree ===
  HOOK: .git is a DIR
  HOOK: GIT_PREFIX=
  (no GIT_DIR exported)

=== pre-push, from a LINKED worktree (.git is a FILE) ===
  HOOK: .git is a FILE
  HOOK: GIT_DIR=/tmp/ao1546-r1.a37dm2/main/.git/worktrees/link
  HOOK: GIT_PREFIX=

=== pre-commit, from the MAIN worktree ===
  (no GIT_DIR exported)

=== pre-commit, from a LINKED worktree ===
  PRE-COMMIT HOOK: GIT_DIR=/tmp/ao1546-r1c.UQSdHi/main/.git/worktrees/link
  PRE-COMMIT HOOK: .git is a FILE
```

#4210's parenthetical — "always in a linked worktree, where `.git` is a file" — is exact, and the
qualifier is the load-bearing half: in the main worktree nothing is exported, which is why the
shape hides until a hook inside a worktree runs it. It matters here because **every lane in this
repo is a linked worktree** (golden rule 15: a lane is its own `git worktree`).

### 4b. Does `git -C <fixture>` really fail to contain the write?

The code under test is extracted **byte-for-byte** from this repository —
`scripts/check-isolation-landed.sh:178-188`, sha256
`31ac7fcfd58b39d63f514bf1df95e453e886177c4a118a4b20605a195cf8eec0`, identical to the bytes in the
script (the extraction is verified by comparing the hash of the extracted block against the hash of
those lines in place):

```
seed_repo() { # seed_repo <dir>
  mkdir -p "$1"
  git init -q -b master "$1" >/dev/null 2>&1 || return 1
  git -C "$1" config user.name "Gate Human" >/dev/null 2>&1
  git -C "$1" config user.email "gate-human@example.invalid" >/dev/null 2>&1
  printf 'seed\n' >"$1/seed.txt"
  git -C "$1" add seed.txt >/dev/null 2>&1
  git -C "$1" commit -q -m "seed: the enforcement boundary" \
    -m "Refs kushin77/agent-orchestrator#287" >/dev/null 2>&1 || return 1
  git -C "$1" rev-parse HEAD
}
```

**Arm A — the code as written, `GIT_DIR` exported (the hook case), on a fresh throwaway victim:**

```
victim BEFORE HEAD=c38b7b7a96c4f848f6c5504e2267ff221a720cad  tracked=[real.txt]
seed_repo output: 4cf9aed5822e1de1d792ac5939ebc63c8db858b8
victim AFTER  HEAD=4cf9aed5822e1de1d792ac5939ebc63c8db858b8
seed_repo's own stderr: warning: re-init: ignored --initial-branch=master
fixture got its own .git? NO   <-- the fixture never became a repository
VICTIM COMMITS: 2  (was 1)
VICTIM TRACKED: [real.txt seed.txt]
VICTIM LOG:
   4cf9aed seed: the enforcement boundary
   c38b7b7 victim: real repository
VICTIM STATUS: D seed.txt      <-- the victim's HEAD tracks a path its worktree does not have
BREACH = YES
```

The containment guard did the opposite of containing: the fixture never became a repository, and
the seed commit landed in the repository the guard existed to protect. `seed_repo` then returned
the SHA of the commit **it made in the victim**, so its caller cannot tell — the function believes
it seeded a fixture. (The trailing `D seed.txt` is the tell that this is a corrupted index rather
than a tidy commit into the wrong repo: the path is in the victim's tree but the blob's file lives
in the fixture directory.)

**Arm B — the same bytes, with the environment neutralised first (the remedy #4210 landed), on a
second fresh victim:**

```
victim BEFORE HEAD=c38b7b7a96c4f848f6c5504e2267ff221a720cad
seed_repo output: 248776fdfa8332b271fe4ca7eb9e0989801768ac
fixture got its own .git? YES
fixture HEAD = 248776fdfa83
VICTIM COMMITS: 1  (was 1)
BREACH = NO
```

**The counterfactual, stated so the verdict is falsifiable.** Arm A breaches only because `GIT_DIR`
was exported; arm B is the same function body with the variable removed, so the difference between
the arms is the export and nothing else. If arm B had also written into its victim, the mechanism
would not hold here and the finding below would be wrong. If arm A had contained, there would be no
finding at all. Note also what arm A rules out: the four scripts that `unset GIT_AUTHOR_NAME
GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL` are running arm A with the identity
variables removed — and the commit still landed in the victim. That partial defence covers half the
defect class and none of the half that matters here.

## 5. The census the prior session did not run

`git grep -E 'git -C'` over tracked files, `vendor/` excluded:

```
all 'git -C' matches      : 285
of which WRITE-CAPABLE    : 141
of which read-only verbs  : 134
files touched             : 33
```

"Write-capable" is a verb classification (`init`, `add`, `commit`, `checkout`, `worktree`, `push`,
`branch`, `merge`, `reset`, `rm`, `clean`, `fetch`, `stash`, `apply`, `restore`) against read-only
(`rev-parse`, `ls-files`, `log`, `show`, `config`, `ls-remote`, `diff`, `rev-list`, `merge-base`,
`symbolic-ref`, `for-each-ref`, `check-ignore`).

Every file that seeds a throwaway repository and then writes into it — the shape #4210 found, and
the shape section 4 breaches — against whether it treats `GIT_DIR`:

| file | write-capable `git -C` | `GIT_DIR` neutralised |
|---|---|---|
| `scripts/check-pr-contract.sh` | 44 | **no** |
| `scripts/check-isolation-landed.sh` | 22 | **no** (`unset GIT_AUTHOR_*` only, line 77) |
| `scripts/check-session-isolation.sh` | 18 | **no** (`unset GIT_AUTHOR_*` only, line 68) |
| `scripts/check-reconcile.sh` | 10 | **no** |
| `scripts/check-worktree-cap.sh` | 9 | **no** |
| `scripts/prune-worktrees.sh` | 9 | **no** |
| `scripts/check-lifecycle-reclaim.sh` | 8 | **no** |
| `scripts/check-landing.sh` | 7 | **no** |
| `scripts/check-scratch-safety.sh` | 3 | **no** |
| `scripts/check-watchdog-bounded.sh` | 2 | **no** |
| `scripts/check-gate-publish.sh` | 1 | **no** |
| `scripts/verify.sh` | 1 | yes — `env -u GIT_DIR -u GIT_WORK_TREE`, line 918 |

Four scripts carry the partial defence — the identity variables and nothing else:

```
scripts/check-isolation-landed.sh:77    unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
scripts/check-session-isolation.sh:68   unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
scripts/check-lifecycle-closeout.sh:32  unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
scripts/check-reconcile-orphans.sh:54   unset GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL
```

That is the half of the defect class that rewrites identity, and it works. It does nothing about the
half that repoints the repository — which is the half arm A exercised.

And the repo already knows the idiom, in three places:

```
scripts/check-ratchets.sh:677  # exported GIT_DIR/GIT_WORK_TREE/identity would repoint or re-sign these repos.
scripts/check-ratchets.sh:678  env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_AUTHOR_NAME \
scripts/check-venue-invalid.sh:441  ... env -u GIT_DIR -u GIT_WORK_TREE git -C "$root" rev-parse HEAD ...
scripts/verify.sh:918          venue_sha="$(env -u GIT_DIR -u GIT_WORK_TREE git -C "$root" rev-parse HEAD ...)"
```

So the finding is not "this repo lacks the knowledge". It is that the knowledge is applied in three
places and missing in seventeen — the same inconsistency, one repository over, that #4210 was
written to fix.

## 6. Reachability: real, and latent

A defect that cannot be reached is not a live breach, so this is scoped rather than asserted:

- **No hook is installed here.** The common hooks directory holds only `.sample` files, and no
  tracked script sets `core.hooksPath` or installs a hook. The fixture-seeding gates run from
  `make verify`, in a plain shell where `GIT_DIR` is not exported. Nothing in this repository's own
  automation reaches arm A today.
- **The one deliberate export in the repo is correct.**
  `governance/isolation/worktree.py:539-545` sets `GIT_DIR` plus a scratch `GIT_INDEX_FILE` to run
  `git read-tree` and `git apply --cached --check` against the *same* repository. That is the
  variable used as intended, and it does not run a `git -C <fixture>` helper. It is not part of
  this finding — it is the counter-example that shows the variable is understood here.
- **The precondition is this repo's normal shape.** Lanes are linked worktrees (rule 15), and a
  hook inside a linked worktree does receive `GIT_DIR` (4a). The only missing step is "a hook, or
  any other caller that exports the variable, invokes one of these gates".
- **That step is being built.** PR #1613 (issue #1541, in flight) adds
  `scripts/git-hooks/pre-commit` and `make install-hooks`, which installs it into *the common hooks
  dir* — the Makefile's own text: "the hook then fires for every linked worktree of this checkout".
  Its current hook body does **not** run these gates (measured: it is a lease check that reads the
  claim ledger), so installing it today would not breach. The point is narrower and still
  load-bearing: this repository is deliberately acquiring a hook plane — the first one it has had —
  and the fixture helpers assume the environment `make verify` gives them, not the one hooks give
  them.

Verdict on severity: **a real containment defect in this repository's gates, currently
unreachable, whose precondition is the environment this repository already works in.** Reported,
not patched.

## 7. Follow-up

Per #1546 step 4 the fix is its own lane, filed as **#1642**: neutralise `GIT_DIR` (and its
siblings) at the entry point of every fixture-seeding check, add **SP-11** to
`docs/SHELL-PATTERNS.md` and `scripts/check-shell-patterns.sh` so the shape cannot come back, and
add a dynamic arm where the fixture is built, since a textual rule cannot prove containment. That
issue owns the durable gate; this lane does not add one.

## 8. Pre-existing red

None in this lane's files: the diff is one new `docs/*.md` plus its index row, and the docs gate is
green over it. Separately, and not this lane's work: `reconcile`, `reconcile-orphans` and
`worktree-cap` are red on this host because the declared `ao-fleet-*` cron rung is not installed
here — a host-level cause, covering nothing this lane touched, never repaired from here. The full
composite gate was **not** run: the box-wide permit cap is saturated and it would PARK.

## 9. Reported, not done

- **No fix.** Nothing under `scripts/` is modified; the 141 sites are measured, not changed. The
  remedy is #1642's.
- **No host state touched.** No `git config` written, no worktree pruned, no hook installed. All
  reproduction ran in `mktemp -d /tmp/...` fixtures.
- **No other repo mutated.** #4210 is read, never commented on: it is another repository's merged
  work, and a finding about it belongs in a direction issue, not in an edit to it.
- **Not measured, named rather than omitted:** whether `shared-services`' remaining gates carry the
  same shape in other scripts (out of lane, and its own repo's business); whether any external
  caller — cron, the fleet runner — exports `GIT_DIR` into `make verify` (the in-repo callers were
  the question this lane could settle, and the answer is that none does); and the behaviour of the
  git versions other than the one below.

`git version 2.53.0`, recorded because arm A is a behaviour claim, and behaviour claims are
version-scoped.

## Classification

class: pattern
posture: overall
lifecycle: verify
pillar: governance
pattern: none
lane: issue-1546
