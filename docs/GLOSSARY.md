# Glossary — the fleet's role vocabulary

> **Status:** normative pointer (issue #777) · **Single authority:**
> [`../governance/vocabulary/fleet.yaml`](../governance/vocabulary/fleet.yaml)

The fleet's role names are **wire values**, not comments: the envelope carries
them, `fleet/channel.py` refuses any sender or recipient outside the closed set,
and `fleet/brain.py` / `fleet/terminal.py` branch on them. That makes the
vocabulary part of the protocol — so it has exactly **one** authority, the
glossary file above, and this page renders it so a reader has an entry point.

**This page is not the authority.** If this page and the glossary disagree, the
glossary wins and `scripts/check-fleet-vocabulary.sh` — run by `make verify` —
fails by name. The same gate holds the glossary, `fleet/channel.py`'s mirrored
constants and `fleet/schema/message.schema.json`'s role patterns in agreement, so
no lane can mint a third name by editing one side.

## The four roles

| Role | Runtime | Authority |
|---|---|---|
| **principal** | the human, and the override terminal | Orders the director, and only the director. It does not address the dispatcher directly: a chain the principal can skip is not a chain. |
| **director** | Copilot advisor session, maximum DeepSeek vPro | The only directive issuer. Steers, verifies, merges, and takes orders only from the principal. |
| **dispatcher** | DeepSeek v4.1 Flash, thinking effort **off** (DSv4FNone) | Executes directives, and only directives: drains the inbox, spawns one executor per directive, reports results back. It never picks work of its own. |
| **executor** (`executor-<name>`) | model, tier and thinking chosen by the director's FinOps block | Executes the one issue its directive names. One issue = one lane = one executor. It cannot escalate its own tier. |

The four terms name the **function** in the chain, not a metaphor. The
repository's existing vocabulary — *authority*, *tier*, *capability*, *lane*,
*claim* — is preferred over inventing a second dialect.

## The invariants, which outlive the words

1. The dispatcher never picks work; it executes only the directives it was given.
2. The director is the only issuer of directives.
3. One issue = one lane = one executor.
4. The principal orders the director, never the dispatcher.

These are stated as **rules**, not carried by a word: the retired vocabulary
described them by metaphor ("a dumb terminal"), and a metaphor is not a rule a
gate can read. `scripts/check-fleet-vocabulary.sh` pins each of the four in
`fleet/CONTRACT.md` §1, strips them, and requires the strip to be detected.

## The envelope carries its version

The migration is a **protocol** migration, because a fleet is running while it
lands. The envelope gained a `schema` field:

| `schema` | Dialect | Emitted | Accepted on read |
|---|---|---|---|
| absent (means `1`) | retired | only to a recipient that cannot read schema 2 | yes |
| `1` | retired | — | yes |
| `2` | current | to a recipient whose heartbeat declares `envelope_schema: 2` | yes |

**Dual-accept on read, single-emit on write.** A schema-2 envelope carrying a
retired role is **refused by name** — two vocabularies must never both be quietly
authoritative. The write seam is *negotiated*, never guessed: the emitter reads
the recipient rung's own heartbeat, emits what that recipient can read, and
**records a downgrade by name** (`channel: legacy-dialect — ...`) rather than
making it silently. An unreadable heartbeat is *cannot-assess*, never read as
"current": a downgrade is readable by both builds, the reverse is not.

The deprecation window's terminus is therefore **mechanical**, not a date: it
closes for a given rung the first time that rung restarts on a build that
declares `envelope_schema` in its heartbeat. Nothing has to be remembered.

A principal (or a gate) can pin one dialect end to end with
`AO_FLEET_ENVELOPE_SCHEMA=1|2`.

## What a retired term may still be

<!-- legacy-gloss:start -->
The names below were the vocabulary before this migration. They are still
**accepted on read** for the window above, so they remain declared — as a gloss,
never as a live name.

| Retired | Was | Replaced by |
|---|---|---|
| **operator** | the human at the override terminal | **principal** |
| **brain** | the advisor session, the only directive issuer | **director** |
| **sister** | the Flash/no-thinking loop that drains the inbox | **dispatcher** |
| **subagent** (`subagent-<name>`) | the epic-focused worker | **executor** (`executor-<name>`) |

The metaphor is kept only where it described a real invariant: "a dumb terminal"
said that the dispatcher never picks work, and that rule survives above as a
declared invariant rather than as a word.
<!-- legacy-gloss:end -->

A retired term may also survive as an **artifact name** — a path, a command, a
heartbeat filename, a Makefile target, a mailbox directory — and as a **code
identifier**. Those are filesystem and telemetry identities, not envelope roles.
`governance/vocabulary/fleet.yaml` declares that boundary by name, including the
concrete cases (`fleet/brain.py`, `brain-inbox`, `make operator`, `--rung brain`,
and the `[brain] ...` lines those commands print as recorded evidence).

## Where to look next

- [`../governance/vocabulary/fleet.yaml`](../governance/vocabulary/fleet.yaml) — the single authority.
- [`../fleet/CONTRACT.md`](../fleet/CONTRACT.md) §1 renders the roles, §3 the envelope, §4 the trust model.
- [`../fleet/README.md`](../fleet/README.md) — the runbook: bootstrap and day-to-day commands.
- [`OPERATOR-ACCESS.md`](OPERATOR-ACCESS.md) — every way in, and what each one needs.
- [`../scripts/check-fleet-vocabulary.sh`](../scripts/check-fleet-vocabulary.sh) — the gate that holds all of the above in agreement.
