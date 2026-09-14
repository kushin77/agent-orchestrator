---
id: ADR-0025
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0025: The remote control transport and the authority split

## Status

`accepted` — ratified on the PR for issue #552, the keystone child of EPIC #551
("Remote control command center CLI — a reachable, identified, audited control
channel for the fleet"). It supersedes no earlier decision and it operates
strictly inside boundaries already frozen: [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md)
(*map the policy, do not couple the runtime*), [`ADR-0013`](ADR-0013-paperclip-ing-integration.md)
(adopt the upstream CLI over HTTP — a process boundary, not a fork and not an
embed), [`ADR-0016`](ADR-0016-paperclip-boundary-single-module.md) (one home for
the boundary adapter) and [`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md)
(the authority split, the no-second-dashboard refusal, and refusal 4 —
*signal → ticket → human*).

Downstream consumers of this record, all blocked on the five decisions below and
required to consume them verbatim rather than re-deciding:

| Child | What it consumes from here |
|---|---|
| #553 (RC-2, the control-verb vocabulary) | D2's capability model, D3's three properties |
| #554 (RC-3, the control API) | D1's home, D2's AuthN/AuthZ, D3's refusal shape |
| #555 (RC-4, exactly-once control + audit) | D3's one-record rule and the existing rails |
| #556 (RC-5, the remote command center CLI) | D1's direction, D2's session, D5's import rule |
| #557 (RC-6, the upstream control-verb mapping) | D1's refusal (a mapping is never an authority) |
| #558 (RC-7, the seam mismatch rows) | D1's refused inbound model |
| #559 (RC-8, gate wiring + docs index) | the whole record |

**Numbering note (issue #552 named no number; re-derived at lane start).**
Enumerated 2026-09-14 on this lane's base, `origin/master` `3af12cb`, three ways:

1. **Files.** `docs/decision-records/` carries `ADR-0001`–`ADR-0018`, `ADR-0022`
   and `ADR-0023`. `ADR-0019`–`ADR-0021` have no file, by the index's own rule:
   they are CMR-hub records cited from this repo, "not this repo's to take".
2. **Citations.** `grep -rn "ADR-0024"` over the tracked tree (excluding
   `vendor/`) returns **exactly one** hit — `portal/server/surfaces.py` (merged
   on `master`, commit `599ce34`): *"The pin-is-the-contract rule (ADR-0024) is
   not the problem"*. That is the **hub** record `kushin77/CMR`
   `docs/decision-records/ADR-0024-one-writer-many-reporters.md`, which the hub's
   own `ADR-0037-portal-catalog-projection.md` names as the pin rule. So `0024`
   is *already cited from this repo, unprefixed, for a different decision* — the
   same condition that made `0019`–`0021` unavailable. `grep -rn "ADR-0025"`
   returns **zero** hits: no file, no citation, no claim.
3. **Claims.** No issue or PR, open or closed, names `ADR-0024` or `ADR-0025`
   (checked 2026-09-14 against the full board and every PR body). The only
   `ADR-00NN` numbers the board names are `0022` (#495, landed), `0023` (#501,
   landed) and `ADR-0030` (#358/#329, a wave plan).

**Therefore this record lands as `ADR-0025`, not `ADR-0024`.** The next free
number is one above the highest number *already referenced by this repo's own
tree*; `0024` is referenced (by a foreign, unwritten record) and `0025` is not.
Taking `0024` would silently re-point an existing citation in merged master at a
different decision. The hub carries an unrelated `ADR-0025-sbom-provenance`; as
with `0020`/`0021`/`0022-finops-doctrine`, a hub number is always cited with its
repo prefix and is never merged with this repo's sequence.

*(The index's prose note — "so this repo's next free number is that one", i.e.
`0023` — was written when `0023` was the frontier and is stale independently of
this lane. This lane owns only its own index row, so the corrected derivation
lives here rather than as an edit to that paragraph.)*

## Context

EPIC #551 exists because the fleet can be **commanded only from its own
keyboard**. That is not an impression; it is measured in
[`../REMOTE-CONTROL-GAP-ANALYSIS.md`](../REMOTE-CONTROL-GAP-ANALYSIS.md) (issue
#360, merged), which is this record's evidence base. Three of its findings force
this decision:

- **Nothing we ship can be commanded from off the machine.** §2.6: every control
  verb is reachable *locally only*; verbs available remotely: **zero**. §1.1: the
  transport for a control message is a local filesystem mailbox plus `os.kill`
  signals, chosen deliberately by [`ADR-0011`](ADR-0011-session-fleet-transport.md).
- **The fleet↔upstream seam is a read/projection seam with no inbound path.**
  §5 proves it in code, four ways: the committed `openapi.json` is *introspected*
  from the adapter's client and skips every method that takes an argument
  ("a mutation is skipped: this is the *read* surface"); the client groups its
  reads first and puts its three mutations under a separate heading; exactly one
  of those mutations (`create_issue`) is ever called; and nothing in this repo
  parses an inbound request body — the seam's transports are the outbound
  `HttpTransport` and the test `FixtureTransport`.
- **There is no caller identity on the control path at all.** §2.6, verbatim:
  "The local shell. There is **no** caller identity on the control path to
  authenticate."

Three sibling lanes (#553 the verb vocabulary, #554 the control API, #557 the
upstream mapping) are blocked on this record precisely because the opposite is
the default: each would otherwise pick a transport and produce three
incompatible imaginaries of "the control channel". The transport is the one thing
that **cannot** be chosen later — it is what the verbs are authorised against and
what the audit records are attributed to.

### The measured runtime this decision must fit

| Surface | Measured fact (source) |
|---|---|
| Local control verbs | `fleet/control.py` — an 18-verb argparse registry, an explicit tuple in `build_parser()`; effect delivered by `os.kill(pid, signum)` against the loop pid in `.fleet/sister.heartbeat.json`, by a flag file under `.fleet/`, by this host's crontab, or by `tmux attach` |
| Steering transport | `fleet/channel.py` — a local file mailbox (`.fleet/{inbox,sent,outbox,done}`, `.fleet/brain/*`) plus `.fleet/slog.jsonl`; `cmd_order` "refuses an operator sender, so this is the only way in" |
| HTTP surface | `portal/server/httpd.py` binds `serve(app, host="127.0.0.1", port=8787)`; every fleet-relevant family refuses a wrong method with 405 and the message `<family> surface is GET only` (`_route_fleet`, `_route_portal`, `_route_finops`, `_route_live_feed`, `_route_ops`, `_route_bridge`) |
| The only identity we have | `portal/server/sso.py` + `portal/server/app.py::_require_session` — a verified RS256 `os-session-token` (purpose-checked, JWKS-verified offline, failing closed on every error); RBAC is never taken from the token |
| The seam | `integrations/paperclip/api/surface.py` — "A method that needs arguments (a mutation) is skipped: this is the *read* surface" |
| Upstream's control verbs | §3 — every one is a route **on upstream's own server** (`server/src/routes/agents.ts`: `pause`, `resume`, `clear-error`, `approve`, `terminate`, `delete`, `keys`, `wakeup`, `heartbeat/invoke`, `heartbeat-runs/:runId/cancel`, `config-revisions/:revisionId/rollback`, `runtime-state/reset-session`) |

### Decision space (options weighed)

| Question | Options | Chosen |
|---|---|---|
| **Channel home** | the existing console HTTP app · a new control-plane service · the session-fleet mailbox | **the existing console HTTP app (`portal/server/app.py`), as one flag-gated-OFF route family** |
| **Direction** | operator-inbound + peer-outbound · peer-inbound (a webhook from upstream) · a peer-hosted plane | **operator-inbound + peer-outbound** |
| **Caller identity** | a new control token or key pair · a machine identity (uid / service account) · the existing console session | **the existing console session (`os-session-token` via `_require_session`)** |
| **Authorisation** | a new control permission vocabulary · the existing `identity/rbac` two-gate engine | **reuse `identity/rbac`** |
| **Authority** | any caller may act on a signal · signals file tickets and operator-requested verbs write fleet state | **signals file tickets; only operator-requested, identified, audited verbs write fleet state** |
| **Audit** | a new control ledger · the existing rails | **the existing rails (`telemetry/ledger/` + `.fleet/slog.jsonl`)** |

## Decision

### D1 — Where the control channel lives, and which way it flows

**One channel, and it is ours. The control channel is a new route family on the
existing console HTTP application — the same `portal/server/app.py` that already
owns the session, the flag reader and the 405 refusals — and commands flow
*inbound to this fleet, from an identified operator*. Nothing flows inbound from
upstream. There is no second server, no second daemon, and no remote writer on
the session-fleet mailbox.**

**D1.1 — The home.** The family is added to `portal/server/app.py`, flag-gated
OFF (`surfaces.remote_control` in `infra/feature-flags/registry.yaml`, whose
`default_policy` is already `off`), checked **before** AuthN so an unpromoted
surface is *invisible*, not merely unauthorised — the pattern
[`../LIVE-DATA-BRIDGE.md`](../LIVE-DATA-BRIDGE.md) already uses. It is not a new
service, not a new listener and not a second app: one HTTP application is the
same one-home rule `ADR-0016` applied to modules, and it is the reason a new
"control service" is refused below.

**D1.2 — The direction, frozen as a table.**

| Direction | Allowed | Transport | Why |
|---|---|---|---|
| operator → this fleet (a control verb) | **yes** | the control route family, authenticated by the console session (D2) | the only direction that can carry an operator-requested, identified command |
| this fleet → upstream (issue push) | **yes, unchanged** | `integrations/paperclip/client.py` `create_issue` over `HttpTransport` | the seam's existing single outbound write (§5 proof 3) |
| this fleet → upstream (a control verb) | yes, as a **mapping only** | RC-6 over `ADR-0013`'s adopted CLI | a mapping describes a peer verb; it never grants authority |
| upstream → this fleet | **no** | — | refused: see D1.3 |
| the monitoring plane → this fleet (a control verb) | **no** | — | refused: ADR-0022 refusal 4, consumed in D3 |

**D1.3 — Refused: an inbound-from-upstream model.** This record's own condition
for accepting that model was the ability to **name the upstream endpoint that
would carry it**. I cannot, and the measurement says none can be named:

- §3 inventories upstream's control surface from its own route files, and every
  verb is a route **upstream serves** — `POST /agents/:id/pause`, `/resume`,
  `/clear-error`, `/approve`, `/terminate`, `DELETE /agents/:id`,
  `POST /agents/:id/keys`, `/wakeup`, `/heartbeat/invoke`,
  `POST /heartbeat-runs/:runId/cancel`, all in `server/src/routes/agents.ts`. A
  route upstream *registers* is a route upstream *answers*; it is not a channel
  through which upstream *calls us*. Upstream ships no outbound-to-peer control
  transport, and §5 proof 4 records that our own seam has none either — its only
  transports are the outbound `HttpTransport` and the test `FixtureTransport`.
- The model would therefore require **upstream to grow an endpoint that calls
  this fleet**. That is work on another repository's files, which NG4 forbids us
  to author ([`../CROSS-REPO-EXECUTION-BOUNDARY.md`](../CROSS-REPO-EXECUTION-BOUNDARY.md)
  §1: a finding about another repo is a *direction issue on that repo's board*,
  never an edit, a settings change, or a pull request authored from here), and
  which would place the **peer on the writing end of our fleet state** — the
  inverse of D3's "operator-requested".
- It would also make the control path unverifiable offline: a peer-initiated
  channel cannot be exercised by the fixture transport the gate already uses.

**D1.4 — The control channel is not the session-fleet transport.**
[`ADR-0011`](ADR-0011-session-fleet-transport.md) is authoritative for
brain → sister → subagent steering between sessions, and it stands unamended;
this record neither extends nor re-opens it. The control channel is a *different
concept* — operator → fleet — with a different trust rule, and it must not become
a remote writer into `.fleet/{inbox,sent,outbox,done}`. Concretely:
`fleet/channel.py`'s `cmd_order` refusal ("refuses an operator sender, so this is
the only way in") stays **exactly as written**. The control path is a new path; it
does not widen the mailbox, and it does not acquire an operator sender.

**D1.5 — Reachability is declared, never assumed.** The console binds
`127.0.0.1`, so a remote operator reaches it only through a declared, flag-gated
exposure. Declaring that exposure is RC-3's (and it stays OFF per GR-5); this
record fixes only that the reachable surface is *this app's* control family and
never a parallel listener.

### D2 — The caller-identity model

**The caller is an operator principal established by the existing console
session, and the verb is authorised by the existing RBAC engine. There is no
second identity, no second login, no second token, and no machine caller.**

**D2.1 — Who the caller is.** A request to the control family carries the console
session cookie `os-session-token` (`portal/server/sso.py`). The principal is
established by `portal/server/app.py::_require_session`, adopted verbatim: the
token is verified offline against the mirrored auth-gate JWKS (RS256,
`kid`-indexed, expiry-checked, `purpose: os-session-token`), and *anything else
fails closed with 401*. Identity is the token subject; the role comes from the
local `ROOT_ADMIN_EMAILS` allowlist plus the org directory — **never from the
token's own `role` claim**. The console has no login of its own; the shared
auth gate is the single front door, and this decision keeps it single.

**D2.2 — What replaces today's absent caller identity.** §2.6 measures that the
caller on the control path today *is the local shell*, and that there is nothing
to authenticate. The replacement is the verified session principal and **nothing
else**:

- A control action with no verified principal is refused **401 before the verb is
  looked up**, and writes nothing.
- There is **no fallback** to the local shell, to `os.environ`, to the process
  uid, or to a default principal. "The local user asked for it" is not an
  identity.
- A *machine* caller (a cron, a watchdog, a monitoring export, a script) is a
  **refused case, not a bypass**: machine-initiated control is a *signal*, which
  D3 resolves to a ticket. There is deliberately no service-account path in this
  decision.

**D2.3 — How the verb is authorised.** Authorisation reuses
[`identity/rbac`](../../identity/rbac/guard.py) — the two gates, in the only safe
order the module states: the **scope gate** (`resolve_scope`) first, then the
**permission gate** (`authorize`), with a denial an unconditional stop mapped to
403 carrying the `Decision` fields, and no retry in another scope. The control
path adds no second authorisation engine and no second permission vocabulary:

- The **resource** segment is fixed **here**: it is `fleet`, because the thing
  being controlled is the fleet and nothing else.
- The **action** segment is the verb's own declared `required_capability`, owned
  by RC-2's single closed declaration (#553). RC-2 declares it; the control API
  evaluates it through `identity.rbac.guard`. A verb whose declared capability
  the principal does not hold is refused **403** — never silently downgraded,
  never re-tried in another scope.

**D2.4 — Authority is rechecked per call.** `identity/rbac/guard.py` already
fixes this for sessions: `guard_session` re-resolves live bindings from the store
on **every** call and is deliberately uncached, so a revocation takes effect
immediately. The control path consumes that rule unchanged — a session
established before a revocation must not carry the removed right, and no control
verb may cache an authorisation decision across requests.

**D2.5 — The identity is recorded, not inferred.** The audit record for an applied
command (D3) names the principal, and the effect is attributed to it. The local
shell stops being an implicit authority.

### D3 — The authority split (the standing rule)

**Standing rule, frozen here: monitoring verbs file tickets; control verbs are
operator-requested and audited. The control path may write fleet state only on an
operator-requested, identified, audited verb.**

The rule has three properties and all three are required **simultaneously**:

1. **Operator-requested** — the command originates from a human act, over D1's
   operator → fleet direction. No signal, threshold, watchdog, cron, monitoring
   export or scheduled task may invoke a control verb. A threshold crossed
   produces a **ticket**; the ticket is the single join node
   ([`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md)).
2. **Identified** — D2's verified principal, carried on the call. Attribution is
   never inferred from the host.
3. **Audited** — exactly one audit record per applied command, appended to the
   **existing** rails: the hash-chained `telemetry/ledger/` and the
   `.fleet/slog.jsonl` stream. No new ledger (ADR-0012's one-authority rule).

This is ADR-0022's refusal 4, consumed and not restated: *"A signal drives a
ticket, never a silent action. The single join node is the ticket (ADR-0014). The
direction is one-way and permanent: signal → ticket → human. Nothing in the
monitoring path writes fleet state — no `fleet/**` file, no `.fleet/**` record, no
claim, no branch, no worktree is mutated by an export."*
[`../OBSERVABILITY.md`](../OBSERVABILITY.md) restates it for the monitoring plane.
This record adds the complementary half ADR-0022 leaves implicit: the control
path *may* write fleet state — but only under the three properties above.

**What a monitoring verb may do:** read a signal, evaluate a threshold, file a
ticket. **What it may never do:** pause, hold, stop, kill, override, or otherwise
write fleet state.

**Falsifiable shape RC-3/RC-4 must demonstrate:** a control request without a
verified session returns `401` and writes nothing; a verb whose declared
capability is absent returns `403` and writes nothing; the monitoring path (the
exposition and publish modules) still writes no fleet state; and a command whose
local lever cannot be reached returns `503` and writes nothing — never a silent
no-op, and never a success it cannot evidence.

### D4 — The refused alternatives, and the consequence each forces

**D4.1 — Embedding a web console in the portal. Refused.** It violates
[`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md)'s binding refusal
— *"build a second dashboard"*; the monitoring plane is the **machine** surface
and the web single pane of glass is the **human** surface, and
`docs/FLEET-DASHBOARD-GAP-ANALYSIS.md` records that the remote human surface is
sourced from paperclip.ing, not built here. *The consequence it forces:* every
control verb would need a rendered view, so the surface grows a frame, a filter
bar and a timeline; the CLI-only, text/JSON property dies; and a gate would have
to police view growth that ADR-0022 already forbids. It also puts a human surface
and the machine surface back in one artifact — the exact failure EPIC #494 exists
to prevent.

**D4.2 — An inbound webhook from upstream. Refused.** No such upstream endpoint
exists to name: §3 shows every upstream control verb is a route *upstream serves*
(`server/src/routes/agents.ts`), and §5 proof 4 shows our seam has no inbound
parser at all. *The consequence it forces:* the channel would invert the
authority — the peer would hold the write into our fleet state, contradicting
"operator-requested" — and delivering it means adding an endpoint inside
`paperclipai/paperclip`, which NG4 forbids this repo from authoring. It would also
be unverifiable offline, because a peer-initiated call cannot be exercised by the
fixture transport the gate already uses.

**D4.3 — A peer-hosted control plane. Refused.** It violates NG4 and the
projection-only seam that ADR-0012/0013/0016 fix: this repo owns its own fleet
state, and a second authority for it is exactly the "two authoritative engines"
ADR-0012 forbids. *The consequence it forces:* our claim ledger, run state and
branch/worktree facts become readable **and writable** from another repository;
the seam's measured one-way property (§5) is lost, so `openapi.json` — currently
provably a read surface — would describe control; and
`scripts/check-paperclip-canonical-module.sh` would have to grow an escape hatch
for a second boundary module, which ADR-0016 deliberately removed.

**D4.4 — A remote writer on the session-fleet mailbox, or a second control
daemon. Refused.** It would make `.fleet/{inbox,sent,outbox,done}` remotely
writable, which is precisely what ADR-0011's local-only transport choice and
`cmd_order`'s operator-sender refusal exist to prevent. *The consequence it
forces:* one verb vocabulary acquires two trust rules (the mailbox's and the
control API's), and a downgrade of the mailbox's refusal becomes
indistinguishable from a legitimate control action — an unaudited path into fleet
state.

### D5 — The relationship to the existing decisions

This record **consumes** the following and re-opens none of them:

| Decision | How this record relates | What it does **not** re-open |
|---|---|---|
| [`ADR-0010`](ADR-0010-canonical-copy-ownership.md) | one canonical home; others reference | the canonical-copy rule |
| [`ADR-0011`](ADR-0011-session-fleet-transport.md) | D1.4 — the session-fleet transport stands; the control channel is a *different* concept with a *different* trust rule | the file-mailbox transport, the A2A graduation target, `cmd_order`'s operator-sender refusal |
| [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) | *map the policy, do not couple the runtime*; consumed as the reason D4.3 is refused | the Hermes/Paperclip ownership boundary |
| [`ADR-0013`](ADR-0013-paperclip-ing-integration.md) | adopt-over-embed-or-fork is the reason D4.1 is refused and the reason D1.2 permits a *mapping* to upstream over HTTP | the integration mode choice |
| [`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md) | D3 — the ticket is the single join node a signal drives | the ticket contract v2 |
| [`ADR-0015`](ADR-0015-routing-seam-single-authority.md) | untouched | routing's single authority |
| [`ADR-0016`](ADR-0016-paperclip-boundary-single-module.md) | D1.1's one-home reasoning, and D5's import rule: any upstream call **imports** `integrations/paperclip/client.py` and never re-implements a transport | the boundary module's home and the `adapters → seam` dependency direction |
| [`ADR-0022`](ADR-0022-telemetry-exposition-authority-split.md) | D3 consumes refusal 4; D4.1 consumes the no-second-dashboard refusal; D3 reuses its existing rails rather than minting a ledger | the machine/human surface split and the no-second-dashboard rule |

It likewise does **not** re-decide: what a verb *does* (`fleet/control.py` stays
authoritative for its 18 verbs' local semantics);
the claim ledger's authority (`governance/dispatch/`); the FinOps tiers
(`gateway/finops/`); or the boundary adapter's shape. This record fixes the
channel, the caller, the split and the refusals — nothing else.

## The standing rules of gap-analysis §6, with the consequence each forces

Each rule is cited from the artifact that owns it, so the constraint is checkable
rather than remembered:

| §6 rule | Consequence this record fixes |
|---|---|
| **1. NG4 — never do another repo's work** (`../CROSS-REPO-EXECUTION-BOUNDARY.md` §1; the report-is-read-only rule §3) | D4.2/D4.3: control commands **this** fleet only. Any upstream change the mapping (#557) discovers — a missing endpoint, a coarse refusal — is a **direction issue on `paperclipai/paperclip`'s board**: filed and reported, never patched from here. |
| **2. No second dashboard** (ADR-0022) | D4.1: the control surface is a **CLI and an API**, text and structured JSON only — no frame, no filter bar, no timeline, no `dashboards/` artifact. Any new *view* of fleet state obeys ADR-0022's split, which this record does not touch. |
| **3. A signal drives a ticket, never a silent action** (ADR-0022 refusal 4) | D3: two *different kinds* of verb that may never be blurred — a **monitoring** verb may only file a ticket; a **control** verb is a deliberate, human-requested, identified, audited act and is the only thing permitted to write fleet state. "The health check noticed X, so it paused the fleet" is refused by construction. |
| **4. One authority per concept; one home per module** (ADR-0012/0013/0016, ADR-0015) | D1.1 (one app, one family), D2.3 (one authorisation engine, RC-2's one vocabulary declaration), D5 (any upstream call **imports** the seam). No second server, no second identity, no second permission vocabulary, no second ledger. |
| **5. Flag-gated OFF, no Actions, no secrets, no ad-hoc apply** (GR-5/GR-6/GR-15) | D1.1: `surfaces.remote_control` ships OFF and is checked **before** AuthN, so the family is invisible rather than merely refused; endpoints and tokens come from the environment or a secret manager; automation is code-native (a `make` target and cron), never a workflow file; infra is never applied ad hoc. |

## Consequences

- **Positive:** the three blocked lanes get one frozen model instead of three
  guesses. The caller becomes a real, verified principal (replacing "the local
  shell" on the control path, with no second identity and no second login). The
  authority split is stated as a rule both the monitoring half and the control
  half can be tested against. The audit lands on rails that already exist and are
  already tamper-evident. And the refusals are pre-priced: the three most
  attractive shortcuts are named with the cost each would force.
- **Negative:** the control path inherits the console session's operational
  requirements — a configured JWKS mirror and the `ROOT_ADMIN_EMAILS` allowlist —
  so a fleet host with no auth-gate configuration can issue no control verb at
  all. That is accepted deliberately: an unauthenticated control path is the
  thing being removed. Reachability also stops being free: the console binds
  loopback, so a remote operator needs a declared, flag-gated exposure, which
  RC-3 owns and which ships OFF. And no machine caller may drive control, so
  "pause the fleet on a threshold" is not available in any form — it becomes a
  ticket, permanently.
- **Neutral:** nothing ships on. `fleet/control.py`'s 18 local verbs keep their
  semantics and remain the levers a control verb delegates to; `fleet/channel.py`
  keeps its mailbox and its refusal; `integrations/paperclip/**` keeps its single
  home and its one outbound write. What changes is that the fleet gains a
  declared, identified, audited front door instead of an implicit local one.
- **Follow-ups.** #553 declares the closed verb vocabulary along with each verb's
  `required_capability` and `effect_class` (D2.3). #554 lands the route family in
  the existing app, flag-gated OFF and checked before AuthN (D1.1). #555 provides
  the one-effect/one-record rule, appends to the existing rails, and makes an
  unreachable lever a `503` that writes nothing (D3). #556 builds the client half
  against #554's API, importing `integrations/paperclip/client.py` for any
  upstream call (D5). #557 maps our verbs onto upstream's routes as a **mapping,
  never an authority** (D1.2). #558 adds the control mismatch rows to the seam
  doc. #559 wires the new checks into the gate of record. If this decision is
  ever reversed, that is a **new** ADR, not an edit of this one.

## Evidence

Read in this lane's worktree at `origin/master` `3af12cb` (all paths, except
where marked upstream, are this repo's):

- Evidence base: `docs/REMOTE-CONTROL-GAP-ANALYSIS.md` §1.1, §2.1, §2.2, §2.4,
  §2.6, §3.2, §3.3, §5 (proofs 1–4), §6, §8.
- Local control and transport: `fleet/control.py`, `fleet/channel.py`,
  `fleet/CONTRACT.md`.
- HTTP surface and identity: `portal/server/httpd.py`, `portal/server/app.py`
  (`_require_session`, the per-family 405 refusals), `portal/server/sso.py`,
  `identity/rbac/guard.py`, `identity/rbac/README.md`,
  `infra/feature-flags/registry.yaml`.
- Seam: `integrations/paperclip/api/surface.py`,
  `integrations/paperclip/client.py`, `scripts/check-paperclip-canonical-module.sh`.
- Decision bounds: `docs/CROSS-REPO-EXECUTION-BOUNDARY.md`,
  `docs/OBSERVABILITY.md`, `docs/LIVE-DATA-BRIDGE.md`,
  `docs/FLEET-DASHBOARD-GAP-ANALYSIS.md`, `docs/EXECUTION-PLAN.md`,
  `docs/decision-records/ADR-0010`–`ADR-0016`, `ADR-0022`.
- Upstream (read-only; cited in backticks and never as links):
  `paperclipai/paperclip` `server/src/routes/agents.ts`,
  `cli/src/commands/client/agent.ts`, `packages/paperclip-runner/SEMANTIC_ACTIONS.md`.
- Numbering: `docs/decision-records/` file listing; `git ls-tree origin/master
  docs/decision-records/`; `grep -rn "ADR-0024"` / `ADR-0025`; the board (no
  issue or PR names either number).
