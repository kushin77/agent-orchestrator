# `governance/futureproof/` — the classification-mechanism capstone

One offline, stdlib-only end-to-end proof of the repository's whole
classification surface: for each of the ten mechanisms the operator named —
`class`, `pattern`, `template`, `rca`, `system`, `app`, `env-var`, `gov`,
`issues`, `index` — the chain

```
authority-declared → gate-wired → gate-falsifiable → assesses-real-tree
```

plus the repository-wide `mechanisms-complete` and `mechanisms-disjoint` halves.

| File | What it is |
|---|---|
| `e2e.py` | **The capstone.** Tri-state (`0 OK / 1 NOT-OK / 2 CANNOT-ASSESS`), reports a verdict per mechanism and a count per verdict. |
| `known-gaps.json` | Named, **shrinking** exemptions for gaps the capstone measured. Exactly matched (mechanism + link + gate); a broken link with no entry fails by name, and an entry that stops matching fails as a stale exemption. |
| `tests/` | The suite, run by `scripts/check-futureproof-e2e.sh` — not declared in `scripts/pytest-suites.txt`, so the gate that owns the capstone is the gate that exercises it. |

```bash
python3 governance/futureproof/e2e.py             # the capstone
python3 governance/futureproof/e2e.py --list      # the mechanism table
python3 governance/futureproof/e2e.py --root DIR  # assess ANOTHER tree
python3 governance/futureproof/e2e.py --json      # the report as JSON
bash scripts/check-futureproof-e2e.sh             # the gate (auto-discovered)
```

## The four links, and why each can fail

1. **`authority-declared`** — every declared authority path exists in the tree.
   A mechanism whose authority is gone is `ABSENT`, not "passing".

2. **`gate-wired`** — the gate is a non-empty `scripts/check-<gate>.sh` that the
   repository's **own** discovery layer wires into `make verify`. This link runs
   `scripts/discover-checks.sh`'s `discover_check_scripts` rather than
   re-deriving the naming convention, and it consults `scripts/check-denylist.txt`
   — so "wired" means wired by the machinery that decides, not by a convention
   this file happens to agree with. A gate that exists but is not discovered is
   `IMPLEMENTED-UNGATED`; the measured class is **#1164**, where a control exists
   and is invoked by NOTHING.

3. **`gate-falsifiable`** — the gate *declares* a provocation (a negative
   control, a `--self-test`, a mutation, a declared behavioural control) and
   carries a verdict vocabulary. A gate that cannot fail is a formality (GR-12 /
   AO-GR-4).

4. **`assesses-real-tree`** — the gate is **run on this tree** and must reach a
   **verdict** (rc 0 or 1). Anything else — rc 2 CANNOT-ASSESS, a hang, a gate
   that cannot be executed — is the mechanism being `DECLARED-ONLY`.

## Why link 4 exists

`scripts/verify.sh` records rc 2 as `SKIP` and still prints
`verify: PASS (… N skipped)`. A gate that is **permanently** CANNOT-ASSESS is
therefore invisible to every other link: discovered, executable, carrying a
negative control, and assessing nothing. Two live witnesses were measured on
pristine `origin/master`:

```
bash scripts/check-paperclip-routines.sh   → rc 2  could not build the dropped-entry tree
bash scripts/check-dispatch-queue.sh       → rc 2  could not assess against the current board
```

`check-paperclip-routines.sh` is tracked by #1176 and `check-dispatch-queue.sh`
hides the six queued-but-closed issues of #1189. **A gate that never assesses is
a formality that hides in the `skipped` bucket** — the same doctrine the capstone
already quotes, applied one level deeper. The general form (should the
composite's `skipped` bucket ratchet?) is filed as #1199 rather than fixed here.

## Verdicts

The four words are **borrowed** from the measured vocabulary of
[`docs/SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md`](../../docs/SYSTEM-APP-GOVERNANCE-E2E-GAP-ANALYSIS.md)
(issue #1156) so two documents cannot disagree about what "enforced" means:

| Verdict | Meaning |
|---|---|
| `ENFORCED` | every link held, **including** the gate assessing this tree |
| `DECLARED-ONLY` | the authority is declared but the gate cannot fail, or cannot assess (rc 2) |
| `IMPLEMENTED-UNGATED` | the gate exists but the discovery layer does not wire it |
| `ABSENT` | an authority path, or the gate script itself, is missing |

## The mechanism table

Each mechanism names the **authority** it is declared in and the **gate(s)**
that enforce it. Editing the table is editing the proof, so
`mechanisms-complete` refuses a dropped or unknown mechanism by name and
`mechanisms-disjoint` refuses a gate claimed by two mechanisms — a hidden
authority conflict is worse than a missing one.
