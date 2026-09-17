# governance/tagging — the tag authority (issue #1175)

One declared vocabulary for every tag this repository puts on a governed
artifact, the derivation from a tag set to the gates that tag set requires, and
a gate that genuinely fails when a tag is unknown, contradictory, or has drifted
from the authority it borrows from.

The operator-facing narrative — the ladder, the posture dimension, the SDLC
stage, and the generated tag → gate matrix — is [`docs/TAGGING.md`](../../docs/TAGGING.md).
This file is the module's own contract.

## Why this exists

Classification already existed in this repository in **five** places, and none of
them could tell you what a tag *implies*:

| Authority | What it governs | What it could not say |
|---|---|---|
| `governance/conformance/policy.yaml` | the `class` ladder; which label *names* are declaring | which *values* are legal for `type`/`priority`/`area` |
| `governance/conformance/surfaces.yaml` | the declared class of a **surface** | the declared class of a **piece of work** |
| `governance/finops/policy.json` | the FinOps tier vocabulary | that an issue is *being worked at* a tier |
| `governance/vocabulary/fleet.yaml` | the fleet's role names | nothing about the board |
| `.github/ISSUE_TEMPLATE/*.yml` | the shape of a form | the vocabulary the form's answers come from |

So a lane could describe work as `class:elite` and nothing anywhere connected
that word to the gates, the FinOps floor, or the lifecycle stage it implies. This
package is that connection, and — just as importantly — it is the **single place**
the vocabularies are reconciled, so drift between them is a gate failure instead
of a surprise.

## Files

| Path | Role |
|---|---|
| `taxonomy.yaml` | **The authority.** Every dimension, its closed vocabulary, and — for every dimension whose vocabulary already has an authority — a `borrowed_from` anchor. It also declares every refusal, by the name the refusal reports. |
| `rules.yaml` | **The derivation.** A tag set → the gates it requires, by channel (`pr`/`ci`/`cd`/`ops`). Every gate is `make:<target>` or `check:<name>` and must resolve. |
| `controls.yaml` + `policy.py` | **The declared controls**, and the reader that holds the authority to them: the required/recommended sets, the refusal severity, the FinOps rank ladder, the contradiction pairs and the limits are restated in the controls and compared against the taxonomy, the rules and `model.FINOPS_RANK`, so relaxing one side is refused by name. |
| `tagging.schema.json` + `schema.py` | **The frozen shapes** of the taxonomy, the rules, the controls and one ledger row, applied through the repository's stdlib-only subset validator (`governance/modules/schema.py`) — no third-party dependency, and a schema using a keyword the validator does not implement is refused rather than silently ignored. |
| `ledger.py` | **The append-only decision ledger.** Every plan derived and board judged is one validated row in `.fleet/tagging/ledger.jsonl`; a malformed row is refused *before* the write, and a hand-written garbage line is reported by line number rather than raising. |
| `live.py` | **The live projection.** The board's declared tags plus the ledger, read-only and offline: coverage, which dimensions are used, which declared ones are unused, and every item the taxonomy refuses — by code and by name. |
| `model.py` | Load, judge and derive. Offline, deterministic, no network. |
| `cli.py` | The verbs: `lint`, `check`, `plan`, `matrix`, `board`, `labels`, `schema`. |
| `provoke.py` | The refusal negative control: one real mutant per declared refusal. |
| `artifacts.py` | The artifact round trips: the shapes, the ledger and the projection, each with its clean twin. |
| `tests/` | The suite, `check-tagging.sh` runs it. |

## The commands

```bash
python3 governance/tagging/cli.py lint      # judge the authority itself
python3 governance/tagging/cli.py check     # lint + matrix freshness
python3 governance/tagging/cli.py plan --tag class:elite --tag posture:iac --tag lifecycle:release
python3 governance/tagging/cli.py matrix    # render; --write updates docs/
python3 governance/tagging/cli.py board     # judge the board's tags (offline snapshot)
python3 governance/tagging/cli.py board --live --max-stale-minutes 60
python3 governance/tagging/cli.py labels    # emit gh label create commands
python3 governance/tagging/cli.py schema --shapes

bash scripts/check-tagging.sh               # the gate (also in make verify)
make tagging                                # the same gate, by name
```

## The gate can fail. Here is how.

`scripts/check-tagging.sh` runs **five** checks and is wired into `make verify`
by auto-discovery (`scripts/verify.sh`, #698), so a new gate in this repository is
enforced the moment it lands and never needs a second file edited.

1. **`tagging-lint`** — the taxonomy's shape, every borrowed vocabulary's
   equality with its authority, every rule's `when` clause, the **resolution of
   every gate name** (against the Makefile's targets and the check registry
   `scripts/verify.sh` builds), every document against its **frozen shape**, and
   the **declared controls** against the authority they govern.
2. **`tagging-matrix`** — the matrix committed in `docs/TAGGING.md` is
   byte-equal to what `cli.py matrix` generates. A hand-edited matrix is stale
   documentation pretending to be generated.
3. **`tagging-suite`** — the module's pytest suite, run *here* rather than
   declared in `scripts/pytest-suites.txt`, so exactly one gate exercises it.
4. **`tagging-refusals`** — the refusal negative control. For each of the **11**
   refusals `taxonomy.yaml` declares, `provoke.py` plants a real mutant (asserting
   by sha256 that the mutation actually landed) and requires the refusal **by code
   and by the token the taxonomy promises it names** — with that input's **clean
   twin accepted**, so a rule that fires on everything is caught. It then asserts
   the declared refusal set equals the set of codes the model can raise: a
   refusal declared but never raised fails, and so does a code raised but never
   declared.
5. **`tagging-artifacts`** — the artifact round trips, each with its clean twin.
   The shape file declares exactly four document shapes; the ledger refuses four
   malformed rows *before* the write and then reads past a hand-written garbage
   line while reporting it by line number; the live projection **names the item
   it refuses** while an all-clean fixture is refused nothing; and a control with
   a relaxed `required` set is refused by name. Each of these can pass while
   doing nothing at all — a schema nothing validates against, a ledger that
   accepts any row, a projection that read zero issues — which is exactly why
   the provoked half is paired with the clean one.

## Design notes that are load-bearing

- **A borrow is mirrored, not read.** A `borrowed_from` dimension writes its
  values out *and* borrows them. Reading the values out of the authority would
  make drift vacuous — the check could never fail. Declaring them and proving
  equality is this repository's own mirrored-constant pattern
  (`governance/vocabulary/fleet.yaml` ↔ `fleet/channel.py`), and it is what stops
  a lane minting a rung or a tier by editing one side.
- **The taxonomy is calibrated, not aspirational.** `required` is the set the
  board already satisfies, so enforcing it is green today and binds new work;
  the two dimensions this issue adds (`posture`, `lifecycle`) are in
  `recommended`, reported as deviations and escalated only under `--strict` —
  the same discipline `governance/conformance/policy.yaml` documents for its own
  expectations.
- **`plan` refuses contradictions.** `posture:no-human-needed` and
  `posture:human-gated` are mutually exclusive by declaration, and both at once
  is refused by name rather than resolved by precedence — silently picking a
  winner is how a plan becomes a guess.

## Surface class

`governance/tagging` is declared in `governance/conformance/surfaces.yaml` at the
rung its own tree **measures**: `elite`. The evidence is the same set the sibling
governance surfaces carry — contract, suite, `controls.yaml` + `policy.py`,
the append-only `ledger.py`, the frozen `tagging.schema.json`, the dedicated gate
`scripts/check-tagging.sh`, and the live projection `live.py` exposed through the
existing `board --live` verb.

Two things are worth stating plainly rather than leaving to be inferred:

- The rung was **measured, not asserted**. The first cut of this package had no
  controls, no ledger and no schema, and the surface-class gate **refused** a
  `pattern` declaration by name (`module-class-above-floor`): the module's
  declared class is `elite`, and a new product surface at `pattern` sets the
  product floor below it. The artifacts were built to meet the measurement, not
the measurement relaxed to meet the declaration.
- `rollback` is a **manual** requirement at this rung, so it is reported on
  every `make surface-class` run (as it is for every elite sibling) rather than
  silently treated as met.
