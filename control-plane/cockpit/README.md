# control-plane/cockpit — the terminal cockpit (RC-11, issue #566)

The **Bloomberg-terminal-grade operator surface** for the fleet: live, dense,
keyboard-first, drillable, role-tiered — and a **client**, never an owner of
fleet state (ADR-0026 D5/D6).

```bash
python3 control-plane/cockpit/cockpit/__main__.py              # interactive (alternate screen)
python3 control-plane/cockpit/cockpit/__main__.py --once       # one frame, then exit
python3 control-plane/cockpit/cockpit/__main__.py --follow     # follow the two SSE streams
python3 control-plane/cockpit/cockpit/__main__.py PAUSE        # one declared function, once
python3 control-plane/cockpit/cockpit/__main__.py --dry-run PAUSE   # print, send nothing
# from inside control-plane/cockpit/, the module form works too: python3 -m cockpit
```

## The stack (ADR-0026 D5.1 — the stdlib ruling)

The client is **Python stdlib only** (plus the repo's accepted PyYAML),
rendering with the same alternate-screen technique
[`fleet/console.py`](../../fleet/console.py) proves in-tree (an ANSI palette
plus `\033[?1049h`). There is **no TUI framework**: the issue body's
"Python + Textual" parenthetical is **superseded by ADR-0026 D5.1**
([`../../docs/decision-records/ADR-0026-control-substrate.md`](../../docs/decision-records/ADR-0026-control-substrate.md))
— naming a TUI framework would be a supply-chain decision smuggled in as a
rendering choice, and the repository carries no dependency manifest. Textual
would require a **new ADR**, not an import line.

## What the cockpit consumes (never restates)

| Concern | Authority the cockpit consumes |
|---|---|
| the function set it renders | [`../functions/functions.yaml`](../functions/functions.yaml) — the RC-10 registry, loaded and rendered via [`../functions/cockpit_registry.py`](../functions/cockpit_registry.py) / [`../functions/cockpit_render.py`](../functions/cockpit_render.py) |
| the verb vocabulary | [`../control/verbs.yaml`](../control/verbs.yaml) (RC-2), resolved through the registry — no ad-hoc verbs |
| the control API | [`../../portal/server/control_api.py`](../../portal/server/control_api.py) (RC-3), spoken through the RC-5 client half [`../cli/aoctl/`](../cli/aoctl/) |
| the live streams | [`../../portal/server/fleet.py`](../../portal/server/fleet.py) (`fleet_projection`, event `snapshot`) and [`../../portal/server/live_feed.py`](../../portal/server/live_feed.py) (`telemetry_live_feed`, event `telemetry`) |
| the session identity | the console session cookie (`os-session-token`), attached last-moment — the cockpit holds **no credential of its own** (ADR-0025 D2) |
| the feature flags | [`../../infra/feature-flags/registry.yaml`](../../infra/feature-flags/registry.yaml) — `surfaces.cockpit` gates startup |

## The keys

The command line accepts **only declared RC-10 mnemonics** plus these fixed
keys:

| Key | What it does |
|---|---|
| `?` / `h` | the help frame |
| `q` | quit |
| `c` / `v` / `m` / `a` | workspace: **CTO** / **VP-Eng** / **Manager** / **Analyst** |
| `d` | drill down (one keystroke per level) |
| `b` | drill back up |
| `1`–`9` | select the drill row before `d` |
| `k` | acknowledge the selected alert |
| `<MNEMONIC> [name=value ...]` | one declared RC-10 function; an irreversible function needs `confirm=<MNEMONIC>` at the end |

## The four role workspaces — lenses, not permissions

CTO / VP-Eng / Manager / Analyst are arrangements of **one** function set
(ADR-0026 D8): the workspace is RC-10's role *recommendation*, additive
filtering only. Permission is `identity/rbac`'s, rechecked per call by the
control API. A lens can only narrow: selecting a function outside the lens is
refused by name (`role_lens`), and the tests prove a role's rendered set is a
subset of the declared set and a cross-role read sends nothing.

## Drill-down

`d` descends one level — `org → lane → issue → agent → call → tool-call` —
with the breadcrumb always visible. Each level names the declared RC-10
function it consumes (`BOARD`, `CLOSURE`, `RECOVER`, `EVENTS`); the level map
is **fixture data** ([`cockpit/fixtures/drill.json`](cockpit/fixtures/drill.json)),
and a fixture that names an undeclared function is refused **by name** at
render time.

## The alert ticker

Severity coding is RC-2's closed enum (`info | warn | critical`, the one
`channel.escalate` declares). `k` acknowledges the selected alert **through
the control API** as the declared steering command `SEND` — there is no
`ack` verb in the vocabulary and the cockpit invents none — so the audit
record lands on RC-4's rails and the cockpit writes no local ack state. An
alert renders acked only from the receipt the plane returned.

## Receipts and refusals

Every action prints a receipt (the RC-3 effect record) or one **named**
refusal — `401 unauthorized`, `403 verb_not_exposed` / `scope_denied` /
`permission_denied`, `405 method_not_allowed`, `409 duplicate_command` (a
replay hands back the **original** receipt), `422 unknown_verb`, `503
lever_unreachable`. Exit codes are the repo's tri-state: `0` OK · `1`
REFUSED · `2` CANNOT-ASSESS — an **unreachable plane**, an **unpermitted
verb** and a **duplicate command** each exit non-zero with a named reason,
never a silent no-op.

## Flag gating

`surfaces.cockpit` ([`registry.yaml`](../../infra/feature-flags/registry.yaml),
default **off**) is read **fail-closed**: an absent or unreadable registry or
an off flag renders the named `FLAG_OFF` condition and exits `2`. A function
whose own surface flag is off renders `disabled <flag>` — "off" is never
shown as data. `NO_DATA` and `unreachable` are named conditions too: no pane
ever reads healthy without evidence.

## Honesty (ADR-0026 D10, carried in this client)

1. A panel that cannot load renders `FAILED` with its reason.
2. `NO_DATA` is rendered as absence, never as a green state.
3. An unpromoted surface is invisible **and the flag is named**.
4. Every action prints a receipt or an explicit refusal.

## Tests — offline by construction

```bash
python3 -m pytest control-plane/cockpit -q
```

Every test runs with injected seams (no network): a recording transport built
on the boundary's typed errors, a temp flag registry, and the committed
fixtures. The suite includes the **registry-conformance gate** (every rendered
function and every accepted mnemonic resolves to a declared entry, with a
provoked mutant fixture refused by name) and the **headless driver** asserting
the drill-down on the real rendered frames.

The suite is deliberately **not** declared in `scripts/pytest-suites.txt`:
RC-8 (#559) is this EPIC's single writer for the shared build files.
