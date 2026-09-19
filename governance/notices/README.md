# governance/notices -- standing notices require an ack from every REGISTERED runtime

> **Status:** normative for the notice record and its gate -- **Ratified by:** issue
> #1269 (EPIC #1268, "mechanical, not doctrinal, between runtimes") -- **Gate:**
> [`../../scripts/check-notice-acks.sh`](../../scripts/check-notice-acks.sh) (auto-discovered
> into `scripts/verify.sh` by [`../../scripts/discover-checks.sh`](../../scripts/discover-checks.sh))

## Why this exists

On **2026-09-18** the merge-path rules went out both ways -- a `SendMessage`
broadcast to the Claude sessions and a hand-built mailbox directive to the DeepSeek
sister -- and **zero acks were recorded**. Nothing in the tree could tell "sent"
from "received", so a rule that had been broadcast to every runtime was, in
evidence, a rule that had reached none.

The fix is not a better broadcast. It is a **record with an acknowledgement**, and a
gate that refuses by name while one is missing. That is the shape every sibling in
EPIC #1268 takes: *the coordination fact is a file with a schema, and the fact has
consequences*.

## The one rule

**A standing notice is not satisfied until every registered runtime has acked it.**

Where the notice goes, and what makes it a rule:

| | |
|---|---|
| **record** | `.fleet/notices/<id>/notice.json` (schema `notice/v1`, [JSON Schema](schema/notice.schema.json)) |
| **fan-out** | `.fleet/notices/<id>/pending/<runtime>.json` -- one copy per registered runtime, naming the transport that carries it |
| **ack** | `.fleet/notices/<id>/ack-<runtime>.json` (schema `notice-ack/v1`, [JSON Schema](schema/notice-ack.schema.json)) |
| **audit** | `.fleet/notices/ledger.jsonl` -- one hash-chained line per write, carrying the record's digest ([`ledger.py`](ledger.py)) |
| **enforcement** | [`scripts/check-notice-acks.sh`](../../scripts/check-notice-acks.sh) in `make verify`, and the same evaluator (`governance/notices/cli.py evaluate`) at any dispatch point |

Records live under `.fleet/` because that is fleet **runtime state**: a transport
writes them, and they are never committed. What ships is the schema, the
derivation, the verbs, the declaration and the gate.

## Why the requirement cannot be hand-maintained

The load-bearing word is **registered**. A list of who must ack, typed once, is
exactly how a notice stops covering the runtime somebody registers next, which is
the failure this issue exists for at one remove.

So the set is **read** ([`runtime_registry.py`](runtime_registry.py)) from the
**one runtime authority** (issue #1412, from #1385), through the one loader
[`fleet/runtimes.py`](../../fleet/runtimes.py):

1. **`fleet/runtimes.yaml`** -- the contract's `runtimes:` rows. Their `id` is the
   runtime, their `identity` names the family it speaks for, and the provider is
   whatever the gateway catalog carries for that identity.
2. **`gateway/catalog/modules/*/module.json`** -- read only for that provider (and,
   in a tree **without** a contract, for the pre-contract derivation below).

A tree with no contract at all keeps the pre-contract derivation -- a **live** pack
(`registry/packs/releases/*.yaml`, `lifecycle: live`) bundles an identity the
catalog carries a transport for -- because a fixture built before #1376 must still
be judgeable. That path computes a set from declarations the tree owns; it never
restates an id list, and it is not consulted when the contract is present.

Measured on this tree (`python3 governance/notices/cli.py runtimes`):

```console
$ python3 governance/notices/cli.py runtimes
notice-acks: runtimes=7 roles=4
  runtime claude-session (provider anthropic, transport mailbox:fleet/channel.py)
  runtime claude-subagent (provider anthropic, transport mailbox:fleet/channel.py)
  runtime deepseek-sister (provider deepseek, transport mailbox:fleet/channel.py)
  runtime deepseek-executor (provider deepseek, transport mailbox:fleet/channel.py)
  runtime copilot-agent (provider copilot, transport mailbox:fleet/channel.py)
  runtime hermes (provider hermes, transport adapter:integrations/hermes/cli.py)
  runtime paperclip (provider paperclip, transport adapter:integrations/paperclip/cli.py)
  role coder (registered identity, no transport: owes no ack)
  role data-agent (registered identity, no transport: owes no ack)
  role ollama (registered identity, no transport: owes no ack)
  role orchestrator (registered identity, no transport: owes no ack)
```

Before the reconciliation this module answered with **five of its own ids**
(`claude`, `deepseek`, `hermes`, `ollama`, `paperclip`) against the contract's
seven -- two vocabularies, neither authoritative, with every lane record validated
against whichever one its caller reached. Two differences were real and both are
now decided by the contract:

* **`copilot-agent`** owes an ack (the contract registers it; the old derivation
  never saw a `copilot` pack profile);
* **`ollama`** no longer does: a live pack bundles it and the catalog carries a
  transport for it, but no contract row registers it, so it is a role here.
  `contract_gaps()` names it -- a demotion reported, never silently absorbed -- and
  `fleet/tests/test_runtime_vocabulary.py` pins it, so registering `ollama` is a
  one-line act that fails the pin instead of leaving this prose stale.

`coder`, `data-agent` and `orchestrator` are identities the fleet **dispatches on**;
they are not execution surfaces and can acknowledge nothing. Nobody classified them
by hand -- the intersection did.

**Consequences, and they are the whole point.**

* Adding a runtime is a **registration act** (a row in `fleet/runtimes.yaml`), and
  the moment it lands every standing notice requires its ack. No notice is edited.
* A notice whose `requires_ack` names ids instead of the registry is **refused by
  name** (`hand-maintained-ack-list`).
* A runtime registered *after* a notice was issued owes a **copy** as well as an ack
  (`notice-not-fanned-out`), so "it was published before you existed" is not an
  answer.

## The verbs

```console
$ python3 governance/notices/cli.py runtimes                  # who is registered, and why
$ python3 governance/notices/cli.py publish --id <id> --subject <text> --body <text>
$ python3 governance/notices/cli.py ack --notice <id> --runtime <runtime> --evidence '<what was read>'
$ python3 governance/notices/cli.py evaluate                  # what is owed, what is outstanding
$ python3 governance/notices/cli.py ledger --verify           # the chain, and every recorded digest
$ python3 governance/notices/cli.py controls                  # the declaration vs the code vs the arms
```

`evaluate` writes nothing, so the gate can drive it against the live tree **and**
against a planted scratch fleet without either run touching the other. The verbs
write only under the fleet directory (`$AO_FLEET_DIR`, else `<root>/.fleet`); a
single `--root`/`--fleet` pair redirects both, which is what the control below
uses.

## The declaration, and what it is held to

[`controls.yaml`](controls.yaml) is the **declaration**: the selector, the status
set, the ledger's path and event kinds, the bounds this rule holds itself to, and
every refusal it can report with the script that provokes it. It is not
documentation -- `cli.py controls` reads it and refuses by name when it stops
being true:

| refusal | means |
|---|---|
| `controls-mirror-drift:<field>` | the declared field and the code's constant have stopped agreeing |
| `refusal-unknown:<id>` | the declaration carries a refusal the code never reports |
| `refusal-undeclared:<id>` | the code can report a refusal the declaration omits |
| `refusal-not-provoked:<id>` | the declared `provoked_by` never names it outside a comment, so nothing can make it fire |

That is the `governance/vocabulary/fleet.yaml` pattern this borrows: *declared
once, mirrored in code, and a gate that names the drift* -- so a lane cannot mint a
second vocabulary by editing one side, and a refusal cannot be declared while
nothing can make it fire (GR-12: a rule a gate cannot fail on is a formality).

[`ledger.py`](ledger.py) is the **audit trail**: one hash-chained line per write
(a publish, an ack) carrying the digest of the record it wrote. A record edited
after the fact no longer matches its digest; a ledger edited after the fact no
longer chains. The shape is `registry/packs/pack_events.py`'s, so there is one
chain idiom in this repository and not two.

## The refusals, by name

A refusal is `code:subject`, and every one of them is provoked by the gate or
pinned by the suite:

| refusal | means |
|---|---|
| `notice-unacked:<id>:<runtime>` | a registered runtime has not acked -- the rule's own refusal |
| `notice-not-fanned-out:<id>:<runtime>` | no copy was ever addressed to it (registered after the notice was issued?) |
| `ack-from-unregistered-runtime:<id>:<runtime>` | an ack from an identity the registry does not carry, so it proves nothing |
| `ack-without-evidence:<id>:<runtime>` | an ack that says only that something wrote a file |
| `ack-runtime-mismatch:<id>:<runtime>` | the filename and the record disagree about who acked |
| `hand-maintained-ack-list:<id>` | `requires_ack` names ids instead of the registry selector |
| `empty-runtime-registry:(none)` | nothing is registered, so nothing is owed -- **never** a pass (the vacuity guard) |
| `transport-surface-missing:<runtime>:<path>` | a runtime's declared transport is gone from the tree |
| `malformed-record:<path>` | the notice or ack record the schema refuses |

Exit codes are the repo's honesty tri-state: **0 OK / 1 NOT-OK / 2 CANNOT-ASSESS**.
An unreadable registry is **2**, never an empty set -- an empty set would make every
notice trivially satisfied, which is the one failure a rule about acknowledgement
must not have.

## The gate proves itself

`scripts/check-notice-acks.sh` drives the real CLI over a scratch registry it builds
and then **mutates**, and it fails if any arm behaves as anything other than the
refusal it expects (GR-12: a check that cannot fail is a formality):

| arm | provocation | expected |
|---|---|---|
| fixture-planted | a notice nobody has acked | rc 1, **both** runtimes named |
| fixture-one-silent | one runtime silent; an ack with no evidence | rc 1 naming **only** it; rc 1 by name |
| fixture-clean | the same notice, fully acked | rc 0, `runtimes=2 roles=1 notices=1` |
| fixture-unfanned | the pending copy for one runtime removed | rc 1, by name |
| fixture-ghost-ack | an ack from an unregistered id | rc 1, by name |
| fixture-hand-list | `requires_ack` rewritten to `["alpha"]` | rc 1, by name |
| ledger-clean / ledger-digest | the chain; then one recorded byte appended to an ack | rc 0; rc 1 naming the record |
| ledger-chain-broken | the first entry's link rewritten | rc 1, by name |
| ledger-malformed | a line that is not JSON | rc 1, by name |
| controls-clean | the shipped declaration | rc 0, every refusal armed |
| controls-mirror-drift | the declared selector changed | rc 1, by name |
| refusal-unknown / refusal-undeclared | an id the code never reports; one dropped from the declaration | rc 1, by name (both directions) |
| refusal-not-provoked | a refusal pointed at a script that never names it | rc 1, by name |
| controls-unreadable | the declaration removed | rc 2 (never 0) |
| fixture-empty | no live pack registers anything | rc 1 (never 0) |
| fixture-unreadable | the pack registry removed | rc 2 (never 0) |
| live-planted / live-acked | the **real** registry, nothing then everything acked | rc 1, all five runtimes named; rc 0 |
| live-tree / live-controls | this checkout's registry, `.fleet` and declaration | rc 0, and the derivation reported |
| suite | `governance/notices/tests` | rc 0 |

`runtimes=2 roles=1` in `fixture-clean` is deliberate: the fixture's live pack
bundles three identities and its catalog carries two, so that line *is* the
intersection being measured.

## Limits, stated rather than implied

* **The gate reads the registry; it does not police the transports.** An ack is the
  record a runtime's transport returned, and the gate can prove it is well-formed,
  from a registered runtime, and present. It cannot prove the far end *understood*
  anything -- that is what `evidence` is for, and it is a string, not a proof.
* **Liveness is not this lane's.** EPIC #1268's sibling issue #1271 owns per-runtime
  heartbeats. Until it lands, a runtime with no beat is still a registered runtime
  that owes an ack: a dead runtime means the notice is **outstanding**, and the
  refusal says exactly that. When heartbeats land, "dead" becomes a third verdict
  here -- not a pass.
* **The ledger records writes, not reads.** `evaluate` appends nothing, so a gate
  run leaves no trace of its own; and a record that is *deleted* is not a ledger
  finding, because the fleet's notice directory is runtime state and is legitimately
  cleaned, while the ledger is the trace that outlives it. The refusal is for a
  record that **changed**.
* **The dispatch gate is the evaluator, not a new refusal in `governance/dispatch`.**
  `evaluate` is the callable a dispatch point consults
  (`notice-unacked:<runtime>` is the refusal it returns, named as this issue's brief
  states it); wiring it into the fleet's dispatch admission is the dispatch lane's
  edit, and this lane does not own that file.
