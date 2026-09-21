---
id: ADR-0034
status: accepted
date: 2026-09-20
deciders: [kushin77]
req: [issue #1523]
supersedes: []
---

# ADR-0034: the live-data bridge is an external-API contract, not a console pane

## Status

`accepted`.

**Numbering note (measured 2026-09-20 at `origin/master` `5357190f`).** The
highest ADR *file* was `ADR-0032`; `ADR-0033`, `0034` and `0035` had **zero**
files, **zero** tree-wide citations (`grep -rhoE 'ADR-00[0-9]{2}'`, `vendor/`
excluded) and **zero** claims across every open or closed issue on the board
(`gh issue list --state all`). `0033` is the next free number in sequence and was
taken on that measurement, per the procedure recorded for ADR-0018/0022/0025.

## Context

`GET /api/v1/bridge` and its family reads (`registry`, `gateway`, `telemetry`,
`guardrails`) plus the `stream` change signal shipped in issue #339 as the
contract `ao.bridge/v1`. The route is implemented (`portal/server/app.py`
`_route_bridge`), the contract is written down (`docs/LIVE-DATA-BRIDGE.md`), the
flag is declared (`infra/feature-flags/registry.yaml`, `surfaces.live_bridge`,
OFF), and the backend has its own suite (`portal/tests/test_bridge.py`).

**What was never decided is who the consumer is.** Nothing in the tree said
whether the bridge is a console-internal read (and therefore owes the console a
caller and a widget) or a machine-facing contract for clients outside the
browser. The consequence is not theoretical: an audit read the route as an
*uncovered console gap* — a backend endpoint with a test and no caller — because
the only way to answer "is this orphaned?" was to guess the author's intent from
the code. A route whose consumer is undecided will keep being re-discovered as a
defect, and the cheap "fix" for each re-discovery is a speculative caller, which
is how a surface acquires a consumer nobody wanted.

The measurement that settles it (all at `5357190f`):

- **No static asset references it.** `grep -rn 'api/v1/bridge'` over
  `portal/static/**` returns nothing: no `fetch` in any `js/*.js` or view.
- **The consumers it does have are out of process.**
  `integrations/paperclip/api/README.md` names `portal/server/bridge.py` and
  `/api/v1/bridge*` as *the serving layer* beneath the paperclip-shaped HTTP
  projection, and describes the boundary explicitly ("Transport is not this
  lane"). `docs/REMOTE-CONTROL-GAP-ANALYSIS.md` analyses the family as an API.
- **It is a versioned address, which is a cost the console does not pay
  elsewhere.** Every console read (`/api/fleet/*`, `/api/telemetry/*`,
  `/api/finops/*`, `/api/ops/*`) is same-origin and unversioned, because the page
  and the server ship as one artifact and move together. A version is what a
  client that *cannot* move with this repo needs.
- **The console already reads all four state families directly**, at the
  unversioned addresses above, each owned by the lane that owns the data. A
  console consumer of the bridge would be a *second* read path in the console for
  data it already has, and the console's own doctrine is that its operator
  terminal "invents nothing".

## Decision

**The bridge is an external-API contract.** It is consumed by out-of-process
clients — today the paperclip HTTP projection across the process boundary — and
it is **not** a console pane. The browser console will not grow a bridge caller,
and the bridge owes the console no widget. This is recorded here, and in
`docs/LIVE-DATA-BRIDGE.md` under "Consumers", so the route's intended consumer is
answered by the repository rather than inferred from the absence of a caller.

Two consequences are part of the decision, not follow-up:

1. **An internal surface does not acquire bridge reads.** The four console
   families above remain the console's contract, each owned by its data's lane.
   A future internal surface that wants *versioned* reads is making a new
   decision, and the place to make it is a new ADR — not a quiet second caller.
2. **The absence of a console caller is not a defect and must not be re-filed
   as one.** The bridge's coverage is its own suite
   (`portal/tests/test_bridge.py`), the contract document, and its out-of-process
   consumer. An audit that wants to call a route uncovered must state the
   consumer it believes is missing.

**Reversal condition, stated so the decision cannot be quietly re-litigated:** if
an out-of-process consumer is *not* the intent — i.e. if the bridge was meant to
be the console's own typed read layer — then the right move is a superseding ADR
that says so and retires the versioned surface, not a console caller bolted onto
a contract that exists precisely to be versioned. Nothing in this repo currently
supports that reading.

## Consequences

- **Positive:** the route stops reading as an orphaned console gap; the audit
  question ("what is this for?") has a committed answer with its evidence; the
  console keeps exactly one read path per state family, so no figure acquires a
  second projection to drift from; the versioned contract keeps its one reason to
  exist (clients that cannot move with this repo).
- **Negative:** the bridge's own coverage rests on an out-of-process consumer,
  so a break in the paperclip boundary would not be caught by any *console* test.
  That is accepted: the boundary has its own suite
  (`integrations/paperclip/api/tests`), and a console test could not have
  exercised the boundary it does not cross.
- **Neutral:** nothing about the runtime changes. The route, the flag, the
  contract and the suite are as they were; what changes is that the decision is
  now written down where a reader (human or agent) will find it.
- **Follow-ups:** none required. `docs/LIVE-DATA-BRIDGE.md` gains the
  "Consumers" section this ADR names, which is the document an auditor reading
  the contract already opens.

**Related:** ADR-0024 (pin-is-the-contract — the pinned document this repo
serves unchanged), ADR-0025 (remote-control transport — the sibling decision
that *did* put a control surface behind a session, and deliberately delegated
rather than restated). Contrast: the bridge is the case where the consumer is
outside the console, so the console owes it nothing.
