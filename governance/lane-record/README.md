# governance/lane-record -- the lane brief and the lane result are ONE record

> **Status:** normative for the lane record and its gate -- **Ratified by:** issue
> #1270 (EPIC #1268, "mechanical, not doctrinal, between runtimes") -- **Gate:**
> [`../../scripts/check-lane-record.sh`](../../scripts/check-lane-record.sh)
> (auto-discovered into `scripts/verify.sh` by
> [`../../scripts/discover-checks.sh`](../../scripts/discover-checks.sh))

## Why this exists

A lane was given a **prose brief** (a goal, the files it owned, the gates it was
scoped to, a trailer, "do not merge") and returned a **prose hand-back** -- and
the two runtimes did not even agree on the shape of the prose: a Claude session
handed back one way, the DeepSeek sister's `result` mailbox message another way.
So the merge loop read **GitHub** to find out what a lane had done, instead of
reading the lane's own claim.

Two failures were measured on **2026-09-18**, and both were sentences:

* a result touching a file **outside the set it was dispatched to own**;
* a lane working in the **shared checkout** it had been told not to use, instead
  of the worktree its own session was assigned.

The fix is the shape every sibling in EPIC #1268 takes: *the coordination fact is
a file with a schema, and the fact has consequences.* Here the fact is the lane
record, and the consequence is that a lane cannot report a scope it was not given.

## The one rule

**A brief and its result are the same record, and the result is held to its own
brief.**

| | |
|---|---|
| **record** | `.fleet/lane-records/<issue>/<lane>.brief.json` and `<lane>.result.json` (schema `lane-record/v1`, [JSON Schema](schema/lane-record.schema.json)) |
| **shape** | one schema document, `$defs.record` for the shared skeleton, `$defs.brief` and `$defs.result` for the two halves |
| **relation** | [`lane_record.pair_problems`](lane_record.py) -- the half a schema cannot state |
| **enforcement** | [`scripts/check-lane-record.sh`](../../scripts/check-lane-record.sh) in `make verify`, and the same evaluator (`governance/lane-record/cli.py evaluate`) at any dispatch or merge point |
| **declaration** | [`controls.yaml`](controls.yaml), mirroring the code's constants and naming the gate that arms every refusal |

Records live under `.fleet/` because that is fleet **runtime state**: a runtime
writes them, and they are never committed. What ships is the schema, the
derivation, the verbs, the declaration and the gate.

## The shape, in full

A brief -- what a lane is handed:

```json
{
  "schema": "lane-record/v1",
  "kind": "brief",
  "issue": 1270,
  "lane": "governance",
  "runtime": "claude",
  "ts": "2026-09-18T20:00:00Z",
  "owned_files": ["governance/lane-record/lane_record.py", "scripts/check-lane-record.sh"],
  "scoped_gates": ["verify", "lane-record-suite"],
  "forbidden_verbs": ["gh pr merge"],
  "assigned_worktree": "/home/<operator>/ao-worktrees/ao-1270-015c115d",
  "report_shape": { "fields": ["sha", "worktree"] }
}
```

A result -- what it returns, in the same shape:

```json
{
  "schema": "lane-record/v1",
  "kind": "result",
  "issue": 1270,
  "lane": "governance",
  "runtime": "claude",
  "ts": "2026-09-18T20:30:00Z",
  "sha": "0123456789abcdef0123456789abcdef01234567",
  "files_touched": ["governance/lane-record/lane_record.py", "scripts/check-lane-record.sh"],
  "gate_tails": { "verify": "verify: PASS", "lane-record-suite": "39 passed" },
  "mergeable": true,
  "squash_rc": 0,
  "worktree": "/home/<operator>/ao-worktrees/ao-1270-015c115d"
}
```

`schema`, `kind`, `issue`, `lane`, `runtime` and `ts` are carried by **both**
halves. That is what makes them one record rather than two shapes that resemble
each other: one reader, one vocabulary, one identity -- and `kind` is the only
thing that says which half you are holding.

**The two halves are told apart, not blended.** Each half's `properties` list the
shared skeleton plus its own fields with `additionalProperties: false`, so a
result carrying `owned_files` is refused as a malformed record and a brief is
**not** refused for lacking `files_touched`. The gate arms both directions,
because a rule that cannot tell the halves apart is a rule that matches
everything.

## The relation -- what a schema cannot say

| refusal | means |
|---|---|
| `lane-runtime-mismatch:<issue>/<lane>` | the result's `runtime` is not the brief's, so what came back is not what was dispatched |
| `lane-worktree-mismatch:<worktree>` | the lane ran somewhere other than its `assigned_worktree` |
| `lane-worktree-shared:<worktree>` | the lane ran in the **shared checkout**, or was assigned it (AGENTS.md rule 15) |
| `lane-file-outside-scope:<path>` | `files_touched` is not a subset of `owned_files` |
| `lane-gate-tail-missing:<gate>` | a gate the brief scoped carries no tail, so the verdict is not in the record |
| `lane-gate-tail-unscoped:<gate>` | a tail for a gate the brief never scoped |
| `lane-gate-tail-empty:<gate>` | an empty tail is not evidence of a run |
| `lane-report-shape-unallowed:<field>` | the brief demands a result field the schema does not allow |
| `lane-report-field-missing:<field>` | the result omits a field its own brief demanded |
| `lane-squash-not-green:<rc>` | `mergeable` is true while `squash_rc` is not 0 |

`report_shape` is the bridge that makes the two halves one schema
**mechanically**: a brief names the result fields it demands **in the schema's
own vocabulary**, a brief demanding anything else is refused
(`lane-report-shape-unallowed`), and a result omitting something its own brief
demanded is refused (`lane-report-field-missing`). So the brief cannot invent a
result shape, and the result cannot quietly drop one.

`files_touched ⊆ owned_files` speaks the same path vocabulary as the cross-lane
half ([`governance/dispatch/model.py`](../dispatch/model.py)'s `FileClaim`, gated
by [`scripts/check-lane-collision.sh`](../../scripts/check-lane-collision.sh)):
that rule refuses two lanes owning one file, this one refuses one lane leaving its
own scope. Same notion of a claim, both directions.

## Why the shape is frozen and the vocabulary is not

`runtime` is a **plain string** in the schema and a **derived set** in the machine.
Which runtimes exist comes from the registry the repository already owns -- a live
AgentPack identity the gateway catalog carries a transport for -- through
[`governance/notices/runtime_registry.py`](../notices/runtime_registry.py), the
single authority for "what is a runtime". It is **imported, never re-derived**,
because a second derivation is exactly the second vocabulary this EPIC exists to
refuse.

```console
$ python3 governance/lane-record/cli.py runtimes
lane-record: runtimes=5 -- claude, deepseek, hermes, ollama, paperclip
```

Register a runtime and it is writable the same day; a list typed into a schema
would have made the runtime somebody registers next week unwritable. A record
naming a runtime the registry does not carry is refused by name
(`lane-runtime-unregistered`) -- so an identity the fleet **dispatches on**, but
which rides no transport, cannot hand back a result.

## The verbs

```console
$ python3 governance/lane-record/cli.py runtimes                          # the derived set
$ python3 governance/lane-record/cli.py validate --file <record.json>     # one record
$ python3 governance/lane-record/cli.py evaluate [--records <dir>]        # the tree
$ python3 governance/lane-record/cli.py controls                          # the declaration vs the code
```

`evaluate` and `validate` write nothing, so the gate drives them against the live
tree **and** against a planted scratch records tree without either run touching
the other. `--root` decides two things at once -- which registry the runtimes come
from, and what counts as the shared checkout -- which is what lets the control
below run entirely inside a scratch tree.

## The declaration, and what it is held to

[`controls.yaml`](controls.yaml) is the declaration: the version, the two kinds,
the shared skeleton, the two halves' required fields, the records subdirectory,
the bounds this rule holds itself to, and **every refusal it can report with the
script that provokes it**. It is not documentation -- `cli.py controls` reads it
and refuses by name when it stops being true:

| refusal | means |
|---|---|
| `controls-mirror-drift:<field>` | the declared field and the code's constant have stopped agreeing |
| `refusal-unknown:<id>` | the declaration carries a refusal the code never reports |
| `refusal-undeclared:<id>` | the code can report a refusal the declaration omits |
| `refusal-not-provoked:<id>` | the declared `provoked_by` never names it on a **non-comment line**, so nothing can make it fire |
| `limit-file-missing:<id>` | a declared bound names a file that is not there |

That is the `governance/vocabulary/fleet.yaml` pattern the sibling notice rule
also borrows: *declared once, mirrored in code, and a gate that names the drift*.

## The shape cannot grow a requirement nobody measures

The schema is validated by the repository's stdlib-only subset validator
([`governance/modules/schema.py`](../modules/schema.py)), which **refuses a keyword
it does not implement**. So a requirement can never be added by writing `oneOf`
and hoping -- the schema will not load. Two consequences are named rather than
hidden:

* **The halves restate the shared skeleton rather than `$ref` it.** The subset
  validator resolves a local pointer against the document it was *handed* as its
  root, so a half that `$ref`ed `$defs/record` could not be validated as its own
  root -- measured. The three lists are therefore held equal by
  [`lane_record.mirror_problems`](lane_record.py) **in both directions**, which is
  a stronger guarantee than the `$ref` was, because a `$ref` cannot fail.
* **The schema document is metadata and `$defs` only** -- no `type`, no
  `required`, no `$ref` at the top level. A document is validated against
  `$defs/<kind>` and nothing else, so there is exactly **one validation path**; a
  validating keyword at the top level would be a shape that looks authoritative
  and is never run, and the mirror refuses it by name.

## What the gate proves on every run

A check that cannot fail is a formality (GR-12), so
[`scripts/check-lane-record.sh`](../../scripts/check-lane-record.sh) builds a
scratch tree, drives the **real CLI** over it, and mutates it one thing at a time.
Every declared refusal is provoked by name -- from a runtime writing a shape the
schema does not allow (`kind: handback`, the actual Claude hand-back shape), to a
result from another worktree, to a brief demanding a result field the schema does
not declare, to a drifted schema copy.

Two arms carry the weight:

* **Precision.** The clean pair is **not** refused; a brief with no result is a
  lane **in flight**, not a violation; and a brief is not refused for lacking the
  result's fields. A rule that matches everything passes the first arms and fails
  these.
* **The mutant.** `pair_problems`' containment is widened on a **copy** of the
  module (`owned_files` swapped for `files_touched`, so everything is "in scope"),
  and the identical out-of-scope control must then be **ADMITTED** -- while
  `squash-not-green` still refuses, so the mutant is not simply broken. The
  substitution is asserted to have applied: a mutant that silently no-ops would
  test nothing. The real module is untouched.

```console
$ bash scripts/check-lane-record.sh
  OK    file-outside-scope         rc=1  lane-file-outside-scope:docs/not-ours.md
  OK    mutant-admits-control      rc=0  lane-record: records=2 briefs=1 results=1 pairs=1
  OK    records-unreadable         rc=2  CANNOT-ASSESS
  ...
check-lane-record: OK -- a brief and its result are one record for every runtime, and every refusal above was provoked by name
```

Exit contract: **0 OK / 1 NOT-OK / 2 CANNOT-ASSESS**. A refusal is never collapsed
to a pass, and CANNOT-ASSESS is reported as CANNOT-ASSESS -- an unreadable schema,
an unreadable runtime registry or a records tree that was **named** and cannot be
read is never an empty set.

**Named boundary.** A checkout where no lane has written a record yet has no
`<fleet>/lane-records`. That is *zero lanes in flight*, and it is reported **with
its counts** (`records=0 briefs=0 results=0 pairs=0`) so an empty tree cannot look
like a tree nobody opened; a records tree that was named and cannot be read is
CANNOT-ASSESS instead. The two are deliberately different codes.

**Named boundary.** The result's `sha` is checked as `40 lowercase hex` and is
**not** resolved in git: a lane record is written by the lane before its commit is
necessarily reachable from this checkout, so a resolution rule here would refuse
honest records. Nothing is claimed about an object this rule did not read.

## What is not in this lane

`scripts/pr-queue.sh` consuming the record instead of prose is the merge-path half
of issue #1270 and belongs to the lane that owns that file; this lane owns the
schema, the machine and the gate. The record is designed for it: `mergeable` is
admitted only with `squash_rc == 0` and a tail for every scoped gate, so the queue
can read the claim rather than a sentence.
