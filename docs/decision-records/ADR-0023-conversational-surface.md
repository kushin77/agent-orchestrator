---
id: ADR-0023
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0023: The conversational surface — gateway-authoritative chat with a swappable client

## Status

`accepted` — ratified on the PR for issue #501, a child of EPIC #500 (the
conversational surface in the Single Pane of Glass). It is written **before any
implementation lane starts**: the EPIC's options paper is a proposal until this
record freezes the mode, the single authority, the grounding boundary, the
identity rule and the flag posture that the `gateway/chat/**`, identity,
telemetry, guardrails, portal and registry lanes then code against. It
supersedes no earlier decision; the ownership boundary it operates within is
[`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) (unamended), the integration
mode it applies is [`ADR-0013`](ADR-0013-paperclip-ing-integration.md)
(unamended), and the one-writer map it never crosses is
[`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md) (unamended).

**Numbering note (issue #501 filed `ADR-0023`; re-derived at lane start).** The
issue deliberately did not freeze a number, and the sibling decision lanes were
right not to: `#463` self-corrected to `ADR-0017` and `#474` to `ADR-0018`
because the ceiling moved under them. Re-derived here against `origin/master` at
`2c24f3b`, with four commands:

1. `git ls-files docs/decision-records/` — the highest record **in the tree** is
   `ADR-0018-codeidx-consumption-and-index-authority.md` (0001–0018 land, and
   0002–0009 are reservations, see [`README.md`](README.md)).
2. `git ls-remote --heads origin` — 52 heads, **none** carrying an ADR number of
   0019 or above (the only `adr`-matching ref is `refs/heads/issue-220-mech`).
3. `gh pr list --state all --limit 100 --json number,headRefName,title` — the
   highest ADR any PR anywhere carries is **`ADR-0018`** (PR #490), and no PR in
   the list adds a file under `docs/decision-records/`.
4. `gh api repos/kushin77/agent-orchestrator/issues/495` →
   `state=open title=Decision: the telemetry-exposition authority split + the no-second-dashboard rule (ADR-0022)`.
   A whole-board sweep (`gh issue list --state open --limit 1000`) confirms
   `#495` is the **only** issue that names a number above 0018 as its own, with
   `#497`/`#498` citing `ADR-0022` as the transport they render into.

The highest **claimed** number is therefore `ADR-0022` (claimed, unlanded — no
`ADR-0019`..`ADR-0029` file exists on `origin/master`), and the next free number
above it is **`ADR-0023`** — the issue's own candidate, confirmed rather than
assumed.

**Registration.** The record is discovered where ADRs are discovered: the
knowledge indexer walks a declarative source list whose ADR entry owns the glob
`docs/decision-records/ADR-*.md` (`governance/knowledge/sources.py`,
`SourceSpec(KIND_ADR, "docs/decision-records/ADR-*.md", _OWNER_ARCH)`), so a new
record indexes with **no second list to edit** — the acceptance criterion the
issue set. The human index table in [`README.md`](README.md) is a reading aid
maintained by the docs lane and is outside this lane's file surface.

## Context

### The question

The owner's intent, verbatim: *"how we can have the openwebui experience inside
our frontend portal that is now mostly paperclip — i want to be able to chat
with my environment just like its a regular ai chat bot but its fully aware of
my enterprise e2e — since we use ollama and already have the shared-services
openwebui already running give me options to integrate this intent into our
single pane of glass using elite saas methods and functionality"*.

Restated as an architecture question: **what is the conversational surface of
this control plane, and who owns a conversational turn?** The answer has to
satisfy both halves of the intent at once — a chat experience good enough that
people use it as a normal chatbot, and an enterprise awareness that is
*structural* rather than aspirational: every turn routed, capped, budgeted,
guarded, metered and audited by the components that already own those jobs here.

### What is measured today (fact · path)

| Fact | Evidence on `origin/master` @ `2c24f3b` |
|---|---|
| **No conversational surface exists, and the client is not named in this tree** | `git grep -iE 'openwebui\|open-webui' origin/master` → **0 hits**. Nothing here answers `POST /v1/chat/completions` or `POST /api/chat`. |
| **The model path is already single and already deferred to one mount point** | [`gateway/proxy/README.md`](../../gateway/proxy/README.md) — the route→dispatch→log funnel; *"The real HTTP REST surface (`POST /v1/agents/:agentId/tasks` + streaming) is mounted by the phase-7 control-plane REST issue; this lane ships the dispatch core + a thin handler + an offline CLI."* |
| **Ollama is a first-class provider, not a bolt-on** | [`gateway/providers/ollama.py`](../../gateway/providers/ollama.py) — keyless, `POST {base}/api/chat`, "the graceful-degradation target for every cloud provider". |
| **The provider contract and tier vocabulary are frozen** | [`gateway/providers/contract.py`](../../gateway/providers/contract.py) — `{content, model_used, usage, latency_ms}`, `TIERS = ("LOW", "MED", "HIGH", "MAX")`. |
| **Routing, tier choice, caps, budgets, guardrails, metering, ledger all exist — each with one owner** | [`gateway/sme-routing/`](../../gateway/sme-routing/) (the routing authority, [`ADR-0015`](ADR-0015-routing-seam-single-authority.md)), [`gateway/finops/`](../../gateway/finops/), [`gateway/limits/`](../../gateway/limits/), [`telemetry/budgets/`](../../telemetry/budgets/) (incl. `killswitch.py`), [`guardrails/dlp/`](../../guardrails/dlp/), [`telemetry/metering/`](../../telemetry/metering/), [`telemetry/ledger/`](../../telemetry/ledger/). |
| **The grounding authorities already exist and already have a write protocol** | `ao.bridge/v1` families ([`../LIVE-DATA-BRIDGE.md`](../LIVE-DATA-BRIDGE.md)), the MCP tool catalogue `gateway/mcp/tools.py` (7 declared tools), [`governance/dispatch/`](../../governance/dispatch/), [`telemetry/budgets/`](../../telemetry/budgets/), the knowledge index ([`governance/knowledge/`](../../governance/knowledge/)). Destructive control-plane ops already answer `202 approval_required` ([`identity/cpapi/openapi.yaml`](../../identity/cpapi/openapi.yaml)). |
| **Conversation state has a home** | [`engine/memory/`](../../engine/memory/) — the `SESSION` scope is tenant/agent/session-scoped with a hard isolation error on a boundary crossing, plus [`prompt_cache.py`](../../engine/memory/prompt_cache.py). |
| **Identity is already ours and already offline-verified** | [`identity/sso/tokens.py`](../../identity/sso/tokens.py) mints/verifies the auth-gate RS256 `os-session-token` (`purpose: os-session-token`); [`portal/server/sso.py`](../../portal/server/sso.py) verifies it against the gate's JWKS mirror and issues nothing of its own. |
| **Every surface ships invisible until promoted** | [`infra/feature-flags/registry.yaml`](../../infra/feature-flags/registry.yaml) `services:` + `surfaces:` (all `default: off`), parity-enforced against [`infra/terraform/variables.tf`](../../infra/terraform/variables.tf) by [`scripts/check-feature-flags.py`](../../scripts/check-feature-flags.py). |

> **Amended 2026-09-22:** the `default: off` / flag-gated-OFF posture referenced
> in this table and below was reversed by policy-gr5-enabled-by-default
> (2026-09-21, AO-GR-6); see
> docs/GOLDEN-RULES.md#ao-gr-6--flag-gated-off-by-default.

### The client being adopted — and its honest caveats

Upstream **OpenWebUI** is a real, already-running product in the shared-services
platform, **outside this tree**: nothing here vendors it, mounts it or names it.
It is adopted as a *client*, which means two facts about it are named rather than
papered over:

- **It carries its own identity model** — a local user table plus a trusted
  email header injected by a proxy. That is a second identity model, and a
  second conversation store, wherever it is allowed to be authoritative.
- **Its model connection is pointed at an Ollama endpoint directly.** That path
  bypasses routing, tier choice, caps, budgets, DLP, metering and the ledger —
  it is precisely the path this control plane exists to own.

Neither fact disqualifies the client. Both facts disqualify **letting it
authoritative**, which is what makes the mode (below) the whole decision.

### The decision space

Five options were live in the EPIC's options paper; the table below is the
tradeoff that decided it.

| | **A.** Mount OpenWebUI, pointed at the Ollama endpoint (status quo wiring) | **B.** Rebuild the chat UI in the portal, OpenWebUI as a model gateway only | **C.** Gateway-proxied, portal-embedded | **D.** Portal-native chat on MCP only | **E.** **Gateway authority + OpenWebUI as a swappable client** (adopted) |
|---|---|---|---|---|---|
| **Where the surface lives** | vendor shell module → `os.purebliss.app` | `portal/static/` + `portal/server/` | `gateway/` serving endpoint; OpenWebUI framed in the shell | portal views + `gateway/mcp` tools | `gateway/chat/**` serving endpoint; OpenWebUI mounted as a client |
| **Identity owner** | OpenWebUI + proxy (a second identity model) | ours | ours, *but the framed client still logs in unless bound* | ours | **ours** — a scoped chat credential in the claim vocabulary `gateway/mcp/authn.py` already verifies |
| **Conversation-state owner** | OpenWebUI's volume (second store) | ours (new store) | ours | ours | **ours** — `engine/memory` SESSION scope |
| **Upgrade path** | upstream image; our tree untouched | ours forever | upstream client + our endpoint | ours forever | upstream client **and** our flagged endpoint — **both** upgrade cheaply |
| **What it forecloses** | enterprise awareness (no routing, budget, DLP, ledger); tenant isolation | [`ADR-0013`](ADR-0013-paperclip-ing-integration.md)'s "do not fork, do not rebuild" posture — a permanent maintenance surface | a swappable client (couples us to OpenWebUI's framing) | a mature chat UX and streaming; a thin grounding story | **nothing structural** — only the compatibility contract is a commitment |
| **Honest verdict** | cheapest to start, **fails the intent** (an ungrounded Ollama chatbot) | builds a product we would then maintain forever | viable, but embeds the client | viable later, not now | **the only option that meets the intent without a second authoritative engine** |

## Decision

The conversational surface is **gateway-authoritative**: this control plane owns
the turn, and *any* chat client is a swappable consumer of it. Six commitments
follow, and each is a decision plus its reason plus the path it rests on.

### 1. Mode — adopt across a process boundary; not embed, not fork

**Decision.** Adopt the already-running upstream OpenWebUI **across a process
boundary**, over a **versioned, OpenAI-/Ollama-compatible contract** served by
this control plane (`POST /v1/chat/completions` and `POST /api/chat`, SSE
streaming, dispatched through the existing funnel). We do **not embed**
OpenWebUI into `portal/` and we do **not fork** it.

**Reason.** Embedding duplicates the tenant/RBAC/session model — two identity
systems, two session stores, a UI we do not own rendering on a runtime we do not
own — and pays that cost forever. Forking pays a **monotonically growing** rebase
against a moving upstream, i.e. the cost grows exactly when the upstream moves
fastest. Adoption pays a **bounded, one-time** cost (stand the contract up, bind
a credential, agree the version) after which an upstream release lands entirely
on the far side of the boundary without touching this tree.
[`ADR-0013`](ADR-0013-paperclip-ing-integration.md) weighed exactly these three
modes for the operator surface and rejected embed and fork **for exactly these
costs**; the shape of the problem is the same here, so the ruling carries over
rather than being re-litigated.

**Citation.** [`ADR-0013`](ADR-0013-paperclip-ing-integration.md) (embed vs
fork vs adopt, and the boundary-unbounded / fork-monotonic / adoption-bounded
cost argument); [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) (the
ownership boundary this lives inside).

**Compatibility is the seam, and it is what makes the client swappable.** The
commitment is the *contract*, not the client: OpenAI- and Ollama-compatible
endpoints are the one wire shape both upstream OpenWebUI and any future
portal-native UI can speak, so replacing the client is an implementation change
rather than a re-architecture. The provider side of that contract is already
frozen here — [`gateway/providers/contract.py`](../../gateway/providers/contract.py)
(`chat(messages, schema, opts) → {content, model_used, usage, latency_ms}`,
`LOW/MED/HIGH/MAX`) and [`gateway/providers/ollama.py`](../../gateway/providers/ollama.py)
(`POST {base}/api/chat`) — and the serving endpoint mounts the existing
dispatch core rather than re-implementing it
([`gateway/proxy/`](../../gateway/proxy/), whose HTTP REST surface is explicitly
the phase-7 mount point).

### 2. Single authority — `gateway/` remains the one model path

**Decision.** Every conversational turn is **one dispatch through `gateway/`**,
and no second component becomes authoritative for anything the gateway owns.
Routing stays [`gateway/sme-routing/`](../../gateway/sme-routing/) (a policy
source we *map*, never a runtime we couple — [`ADR-0015`](ADR-0015-routing-seam-single-authority.md));
tier choice stays [`gateway/finops/`](../../gateway/finops/); context/token caps
stay [`gateway/limits/`](../../gateway/limits/); budgets and the kill switch
stay [`telemetry/budgets/`](../../telemetry/budgets/); DLP and
prompt-injection defense stay [`guardrails/`](../../guardrails/); metering and
the ledger stay [`telemetry/`](../../telemetry/) (`metering/`, `ledger/`).

**Reason.** The chat surface is the first place where it is *tempting* to let a
second engine exist, because a chat product wants its own model dial, its own
per-conversation limits and its own spend counter. Each of those would be a
second authoritative engine for a surface this platform already owns, which is
the one thing [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) forbids — *map
the policy, do not couple the runtime*. A conversational turn is therefore not a
new kind of thing: it is a dispatch with a friendlier envelope, and it inherits
routing, caps, budgets, DLP, metering and ledger **because it did not invent a
parallel path to the model**.

**Citation.** [`ADR-0012`](ADR-0012-hermes-paperclip-boundary.md) (no two
authoritative engines; map the policy, do not couple the runtime);
[`ADR-0015`](ADR-0015-routing-seam-single-authority.md) (the routing seam is a
policy source); `gateway/sme-routing/`, `gateway/finops/`, `gateway/limits/`,
`telemetry/budgets/`, `guardrails/`, `telemetry/metering/`, `telemetry/ledger/`.

### 3. The authority boundary for grounding — read, or propose as an approval; never write

**Decision.** A turn may **read** the existing authorities: the `ao.bridge/v1`
families ([`../LIVE-DATA-BRIDGE.md`](../LIVE-DATA-BRIDGE.md)), the
[`gateway/mcp/`](../../gateway/mcp/) tool catalogue (extended with read-only
tool families for tickets, budgets, the ledger, agents and the knowledge index),
[`governance/dispatch/`](../../governance/dispatch/),
[`telemetry/budgets/`](../../telemetry/budgets/) and the knowledge index
([`governance/knowledge/`](../../governance/knowledge/)). It may **propose**
writes **only as approvals**, on the existing `202 approval_required` protocol
([`identity/cpapi/openapi.yaml`](../../identity/cpapi/openapi.yaml)). It is
**never** the writer of `status`, `blocked_by` or `facets.budget`.

**Reason.** Grounding is the whole point of the surface, and grounding has
exactly two honest forms: read the authority, or ask the authority's owner to
write. A third form — the chat surface writing the ticket state it just
reasoned about — is how a conversational feature silently becomes a second
writer of the fleet's most contested fields. The one-writer map is already
frozen; the conversational surface is a **reader with a proposal channel**, and
its proposals travel the same approval path a human operator's do.

**Citation.** [`ADR-0014`](ADR-0014-ticket-single-join-node-contract-v2.md)
(the ticket as single join node and the one-writer map for `status`,
`blocked_by`, `facets.budget`); [`../contracts/paperclip/ticket.schema.json`](../contracts/paperclip/ticket.schema.json)
(the normative v2 shape those fields belong to); `ao.bridge/v1` in
[`../LIVE-DATA-BRIDGE.md`](../LIVE-DATA-BRIDGE.md); `gateway/mcp/tools.py`,
`governance/dispatch/`, `telemetry/budgets/`, `governance/knowledge/`.

### 4. Identity — the auth gate is the front door; the client's user table is advisory

**Decision.** The auth-gate **`os-session-token`** is the front door
([`identity/sso/tokens.py`](../../identity/sso/tokens.py),
[`portal/server/sso.py`](../../portal/server/sso.py)). Upstream OpenWebUI's own
user table is **advisory, never authoritative**: it may render a display name,
it may not decide a tenant, a role or what a tool may touch. A **scoped chat
credential is minted per `(tenant, agent, conversation)`** by `identity/`, in the
claim vocabulary the gateway already verifies — `iss` / `sub` / `aud` / `iat` /
`exp` / `jti` plus the scoped `tenantId` / `agentId` / `role` / `allowedTools`
([`gateway/mcp/authn.py`](../../gateway/mcp/authn.py)) — and where the client's
identity disagrees with ours, **ours wins**.

**Reason.** Two identity models meeting at a boundary is fine; two
*authoritative* identity models is a tenancy incident waiting for a URL. Keeping
exactly one authority and binding the per-conversation credential through the
vocabulary that is already verified makes the client's login a rendering detail
and keeps tenant isolation structural (AO-GR-15) rather than enforced by
convention at the seam.

**Citation.** [`identity/sso/tokens.py`](../../identity/sso/tokens.py) (the
RS256 `os-session-token`, `purpose: os-session-token`);
[`identity/rbac/`](../../identity/rbac/) and
[`portal/server/authz.py`](../../portal/server/authz.py) (scope-then-permission
gates); [`gateway/mcp/authn.py`](../../gateway/mcp/authn.py) (the verified claim
vocabulary).

### 5. Flag posture — `surfaces.chat` ships OFF, and the flag is checked before AuthN

**Decision.** The surface ships **invisible**: a `surfaces.chat` entry in
[`infra/feature-flags/registry.yaml`](../../infra/feature-flags/registry.yaml)
with `default: off`, a **matching dedicated `enable_chat`** in
[`infra/terraform/variables.tf`](../../infra/terraform/variables.tf) (so the
surface can be promoted — and killed — without promoting the whole gateway),
and the 1:1 parity that
[`scripts/check-feature-flags.py`](../../scripts/check-feature-flags.py)
enforces mechanically. The flag is checked **before AuthN**: an unpromoted
surface is *invisible*, not merely unauthorised — the `live_bridge` precedent
([`../LIVE-DATA-BRIDGE.md`](../LIVE-DATA-BRIDGE.md), *"the flag is checked
before AuthN"*).

**Reason.** AO-GR-6 and the fleet's GR-5 make flag-gated-OFF the *default*, not
an afterthought: a new surface that ships on is a defect regardless of how
carefully it is built. A dedicated `enable_chat` is chosen over reusing
`enable_gateway` so that the chat surface has its **own** promotion and its own
kill switch — promoting chat must not imply promoting every gateway endpoint,
and killing chat must not require killing the gateway. This is the
`web`/`paperclip` shape: a flag of its own for a surface of its own.

**Parity detail (measured, not assumed).** `scripts/check-feature-flags.py`
does not merely *check* that an `enable_*` exists; it enforces **set equality**
between the terraform `enable_*` flag names and the registry's `services:` keys
(step 5: *"terraform flags without registry entries"* / *"registry services
without terraform enable_ flags"* are both failures). A dedicated `enable_chat`
variable therefore requires a **paired `services.chat` entry** alongside the
`surfaces.chat` entry, or the gate goes red for a reason that has nothing to do
with chat. The implementation lane is expected to add **both** registry entries
plus the variable; this record states it so the lane does not discover it from a
red gate.

**Citation.** [`docs/GOLDEN-RULES.md`](../GOLDEN-RULES.md) AO-GR-6 —
*"Flag-gated OFF by default"*; the fleet's GR-5 rule in local form;
[`scripts/check-feature-flags.py`](../../scripts/check-feature-flags.py) (the
enforcer); [`infra/feature-flags/registry.yaml`](../../infra/feature-flags/registry.yaml)
(the existing `surfaces:` entries, `live_bridge` among them).

### 6. Rejected alternatives, with the reason each was rejected

**Option A — mount OpenWebUI pointed at an Ollama endpoint (the status-quo
wiring).** Cheapest to start and it *feels* like the intent, because it is a real
chat product. Rejected: it has **no enterprise awareness at all** (no routing,
no tier choice, no caps, no budget, no DLP, no metering, no ledger, no
grounding), it introduces a **second identity model** and a **second
conversation store**, and it fails tenant isolation structurally. It is an
ungrounded chatbot wearing our domain name; the intent is the opposite of that.

**Option B — rebuild the chat UI in the portal, keeping OpenWebUI as a model
gateway only.** Enterprise-aware, ours end to end. Rejected because it pays a
**recurring** cost to buy a **one-time** boundary: we would own a chat UX,
conversation store, streaming and every future chat feature forever, against a
bounded integration cost that buys all of it upstream. It re-creates the
fork trap in a friendlier form, and it contradicts the posture
[`ADR-0013`](ADR-0013-paperclip-ing-integration.md) already ratified ("do not
fork, do not rebuild").

**Option D — portal-native chat on MCP only, no OpenWebUI.** Rejected for the
same rebuild cost as B, plus a **thinner grounding story**: MCP alone is a tool
plane, so the surface would ship without the conversation UX that makes the
intent worth building, while still paying for a UI we would maintain. It remains
a *later* option, not a rejected end state — and because the commitment is the
compatibility contract, it is reachable without re-deciding anything here.

**Option C — gateway-proxied and portal-embedded.** **Adopted as the contract,
not as a coupling.** C is exactly the serving half of this decision
(`gateway/chat/**` serving an OpenAI-/Ollama-compatible contract, dispatched
through the existing funnel) and it is *how* the seam is spelled. What is
explicitly **not** adopted is the coupling in C's framing clause: framing or
embedding a specific client is a client decision, not an architecture one, and
it is the client that must stay swappable. The client mount (iframe / tab /
native, and which shell module carries it) belongs to the vendor shell's own
registry and its owners — requested by direction issue, not decided here.

## Consequences

- **Positive:** the intent is met without a second authoritative engine. The
  enterprise-awareness half is not built at all — it is *inherited*, because a
  turn is a dispatch through [`gateway/`](../../gateway/) and therefore routed,
  capped, budgeted, DLP-checked, metered and ledgered by the components that
  already do those jobs. The experience half is not built either — upstream owns
  it, so the chat UX improves on someone else's roadmap. Upgrades on both sides
  cost nothing in this tree: the upstream client upgrades on its side of the
  boundary and our endpoint upgrades behind its flag. Tenant isolation stays
  structural, identity stays single-sourced on `os-session-token`, and grounding
  has exactly two honest forms (read, or propose an approval).
- **Negative:** a **process boundary** is a new operational surface — a second
  service to run, health-check, version and pin, plus a cross-boundary identity
  binding to get right once. Two client-side facts must be reconciled rather
  than ignored: the live client currently runs **outside** its own IaC flag
  (live state ahead of declared state) and, as deployed, its image tag is
  unpinned — a promotion must pin it. The compatibility contract is a
  **commitment**: once OpenAI-/Ollama-compatible endpoints exist they are a
  public shape, so changing them is a versioned change, not a rename. The MCP
  tool catalogue is the grounding surface and part of it is a declared backend,
  so a turn grounded there must return honest `NO_DATA` (AO-GR-19) rather than a
  fabricated answer.
- **Neutral:** nothing is renamed, no transport changes, no data migrates, and
  no gateway adapter is touched by this record — it edits no code and stands up
  no process. The conversational surface is a **new consumer** of surfaces that
  already exist, so it can land in parallel lanes
  (`gateway/chat/**`, identity, telemetry, guardrails, registry) without
  re-sequencing the pillars.

**Follow-ups (named).**

1. The **serving surface** — `gateway/chat/**` mounted on the existing dispatch
   core, with the OpenAI-compatible and Ollama-compatible endpoints and SSE
   streaming — is the lane this record's mode is for.
2. The **grounding read tools** must be added to
   [`gateway/mcp/`](../../gateway/mcp/) as **read-only** families; the
   declared-fake index backend must be replaced by the real codeidx/KB surface
   or report `NO_DATA`, never a synthesised answer.
3. The **scoped chat credential** (per `(tenant, agent, conversation)`) is an
   `identity/` deliverable; [`gateway/mcp/authn.py`](../../gateway/mcp/authn.py)'s
   verified vocabulary is the target shape.
4. **Flag posture** requires three coordinated edits, not one: `services.chat`
   and `surfaces.chat` in
   [`infra/feature-flags/registry.yaml`](../../infra/feature-flags/registry.yaml),
   plus `enable_chat` in
   [`infra/terraform/variables.tf`](../../infra/terraform/variables.tf) — the
   parity check in [`scripts/check-feature-flags.py`](../../scripts/check-feature-flags.py)
   refuses two of the three alone.
5. The **client mount** is a direction request to the vendor shell's owners, not
   an edit here.
6. **Declared-vs-live reconciliation** for the already-running client (its IaC
   flag is OFF while the deployment is live) and image pinning are named
   operational follow-ups, owned by whoever touches the client.

**Reversibility.** The decision is **reversible at bounded cost**, and it is
reversible *in the direction that matters*: because the commitment is the
compatibility contract rather than a coupling, replacing upstream OpenWebUI with
a portal-native UI (option D) replaces an implementation of a client, not a
contract — the serving endpoint, the identity binding, the grounding rules and
the flag posture are all unchanged by that swap. The one part that would be
expensive to reverse is the contract itself, which is why it is the *only* part
this record treats as frozen. That is the same reversibility shape
[`ADR-0013`](ADR-0013-paperclip-ing-integration.md) used for the operator
surface and [`ADR-0015`](ADR-0015-routing-seam-single-authority.md) for the
routing seam: bind the contract, leave the runtime swappable.
