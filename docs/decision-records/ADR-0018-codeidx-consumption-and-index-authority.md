---
id: ADR-0018
status: accepted
date: 2026-09-14
deciders: [owner]
req: []
supersedes: []
---

# ADR-0018: The two-index authority split + the no-re-derivation rule

## Status

`accepted` — ratified on the PR for issue #474 (child of EPIC #472). It fixes,
**before any code**, which index answers which question across this repo's three
index-shaped surfaces, and freezes the rule that stops a published contract from
being re-implemented a second time. It supersedes no earlier decision; it makes
explicit the distinction [`ADR-0010`](ADR-0010-canonical-copy-ownership.md)
already drew for *copies* (one canonical home, others reference) and extends it
to *contracts* (consume the published shape, never mirror it).

**Numbering note (issue #474 said `ADR-0017`).** The issue was drafted when the
highest ADR on `origin/master` was ADR-0015 and the diagrams lane was expected to
take ADR-0016. By the time this lane opened (base `497432d`) the diagrams lane had
landed as **ADR-0017** and the paperclip-boundary lane as **ADR-0016** — both are
present in the [`README.md`](README.md) index — so `ADR-0017` was already taken.
The issue's own Notes fix the tie-break — *"coordinate by the README table"* — so
this record lands as the next free number, **ADR-0018**, and its index row follows
it. The issue's illustrative filename is satisfied in substance by
`ADR-0018-codeidx-consumption-and-index-authority.md`; two files sharing the
number 0017 would have been the defect this note avoids.

## Context

This repo carries **three** index-shaped surfaces and, until this record, wrote
down nowhere what their relationship is. That absence — not any mistake — is what
let a published contract be harvested and then re-implemented.

1. **`governance/knowledge/` — our institutional knowledge catalogue (ours).**
   [`README.md`](../../governance/knowledge/README.md) calls it *"the authoritative
   catalogue of this repository's institutional knowledge: its golden rules,
   governance, architecture, ADRs, policy, pattern/template definitions and live
   issue metadata — each with the provenance needed to trace it back to a
   revision."* Its manifest [`catalog.json`](../../governance/knowledge/catalog.json)
   enumerates the closed kind set — `golden-rules`, `governance`, `architecture`,
   `adr`, `policy`, `pattern-template`, `issue-metadata` (and `lessons`/`rca`
   from the CMR hub, reported `unavailable` while the submodule is absent) — and
   records that *"Nothing here is hand-maintained … so there is exactly one place
   to add a source and no second knowledge store to drift out of sync."*
2. **`gateway/mcp/kb.py` — a declared, in-memory symbol graph (fake).** Its
   module docstring states: *"The compiler-accurate indexing tool shapes
   (definitions / references / search / query / freshness) are re-used from the
   code-indexing MCP catalog (`.research/fleet/code-indexing/codeidx/mcp_server.py`)
   … but this gateway does NOT implement a real indexer: each tenant's code/KB is
   represented as a declared, in-memory graph … that the declared tools query."*
   Every hit envelope carries `FIDELITY_NOTE` = *"Results are compiler-derived
   declarations in a per-tenant fake index: `fidelity` is `semantic` for indexed
   definitions/references. No code is read; only the tenant's own index graph is
   queried."*
3. **`kushin77/code-indexing` — the real, compiler-accurate org-wide symbol
   index (theirs).** Listed in [`../CANNIBALIZATION.md`](../CANNIBALIZATION.md) §1
   as *"Compiler-accurate indexer + MCP tool catalog (Python/SQLite)"* and §2.8 as
   the *"Reference MCP tool catalog — canonical, AST-scoped agent tools."* It is
   **not consumed** by anything here.

That gap is why `kushin77/code-indexing#129` and `#130` happened: a tool shape was
harvested (correctly, with provenance) and then **re-implemented**, because
nothing recorded the difference between *adapting a pattern* and *depending on a
service*. The second instance is already in the tree:
[`prompt_cache.py`](../../engine/memory/prompt_cache.py) documents a block
discipline *"lifted from `codeidx/docs/prompt-cache.md` (see provenance in
`README.md`)"* and a token approximation that is *"the same deterministic local
approximation the harvested codeidx prompt-cache spec uses"* — re-use that is
legitimate for a **spec/pattern**, and must stay distinguishable from re-use of a
**contract**.

**Decision space.** For the gateway's posture three answers are live: retire the
declared graph, keep it as the offline fixture, or keep it as the production
fallback. For authority: merge the surfaces, or split them. For re-use: license
re-implementation by provenance, or forbid it for published contracts. For
freshness: gate on the declared signal now, or refuse to until the vendor
publishes currency.

## Decision

Four commitments, each decided and each naming the alternative it rejects.

### 1. The authority split — and the statement that neither index decides anything

| Surface | Owns | Home |
|---|---|---|
| `governance/knowledge/` | **institutional facts** — what we rule, govern, decided, or templated | **ours** (this repo) |
| `kushin77/code-indexing` | **code facts** — what symbols exist, where, compiler-accurately | **theirs** (the vendor repo) |

The catalogue is *ours* because its kinds are institutional
(`catalog.json`: `golden-rules`, `governance`, `architecture`, `adr`, `policy`,
`pattern-template`, `issue-metadata` — documents and decisions that only this
repo can author). The symbol index is *theirs* because only a compiler-accurate
indexer can answer a code fact, and that indexer lives in `kushin77/code-indexing`.

**Neither index decides anything.** An index *answers a question*; it holds no
authority to *act*. Authority to decide lives in `docs/decision-records/` (this
ADR set) and the declared contracts — never in an index, ours or theirs. A
catalogue entry that looks like a rule is a *citation of* a rule the governance
docs own; a symbol hit is *evidence about* code, never a mandate to change it, and
a code fact that implies an action becomes a ticket or an ADR, never a silent
edit.

**Rejected — one merged index.** Collapsing institutional and code facts into a
single surface was rejected explicitly: the two have different owners, different
provenance models (`version`/hash/line-count for ours, `commit`/`indexedAt` for
theirs), different freshness guarantees, and different licences. A merged answer
would be un-attributable — a consumer could no longer say whether a fact is a
decision we ratified or a symbol the compiler counted — which is precisely the
confusion that produced #129/#130.

### 2. The gateway's posture — kept as the **offline fixture**, never a production fallback

The declared in-memory graph in [`kb.py`](../../gateway/mcp/kb.py) is **kept as
the offline fixture** that exercises the declared indexing tool shapes
(`definitions` / `references` / `search` / `query` / `freshness`) in tests, gates
and the CLI demo. It is legitimate **only while both** conditions hold:

- **contract-faithful** — it answers with the same tool shapes and result
  envelopes as the published `codeidx` catalog (the reason `kb.py` re-uses those
  shapes at all); and
- **labelled** — every result carries the fidelity label so a caller can never
  mistake it for a real index (`kb.py`'s `FIDELITY_NOTE`, surfaced "on every hit
  envelope (mirrors codeidx)").

**What a consumer sees in each case, and how it tells which one it got:**

- **(a) retired** — the declared tools would have no offline backend; the fixture
  paths in the test suite could not run and the tool-shape contract would go
  unexercised offline. (Not chosen.)
- **(b) offline fixture — CHOSEN.** In offline / test / demo mode the tools answer
  from the declared graph; every hit envelope carries `FIDELITY_NOTE` and
  `freshness()` returns the *declared* `status` / `commit` / `indexedAt`. A caller
  tells it got the fixture by reading the fidelity label and by recognising that
  the freshness fields are **declared configuration, not a measured index** — no
  code was read and no repository was committed to.
- **(c) production fallback — REJECTED.** Where no real index exists, the same
  answers would be returned on a live path, and a caller could not tell a fake
  "symbol not found" from a real one except by the label — a fake answer standing
  in for a measured one is the false-green failure this fleet forbids
  ([`../GOLDEN-RULES.md`](../GOLDEN-RULES.md), the no-false-green doctrine).

**Rejected — (c) keep it as the fallback.** Tempting (the vendor's own pickup of
an in-memory graph suggests the shape is useful), but a fallback is defined by
being *indistinguishable to the caller except for the label*, and an index result
that is *structurally* trustworthy enough to be a fallback is trustworthy enough
to mislead. A consumer that cannot tell fixture from fact cannot gate on either.
The fixture is therefore scoped to offline/test/demo; the moment a real backend is
consumed, it is the only backend on a live path and the fixture backs no
production answer.

### 3. The no-re-derivation rule (frozen, citable)

> **A shape that exists in a published contract is _consumed, never mirrored._**
> Provenance in [`../CANNIBALIZATION.md`](../CANNIBALIZATION.md) — the GR-10
> `harvested_from: <repo>/<path>` marker — records **where an idea came from**;
> it is **never** a licence to re-implement a published contract.

The rule draws the line the missing record blurred:

- **A pattern or spec** (a `PATTERN` / `REFERENCE` verdict in
  [`../CANNIBALIZATION.md`](../CANNIBALIZATION.md)) **may** be adapted and
  re-implemented here — that is what the harvest doctrine is for. `prompt_cache.py`
  re-implementing the codeidx prompt-cache *discipline* is legitimate, and its
  docstring records the provenance.
- **A published contract** (an interface, tool catalog, or service surface a
  producer publishes for consumers) **must be consumed** at its source, never
  re-implemented behind a local copy of the shape. `kb.py` re-implementing the
  `codeidx` tool catalog is the case this rule stops: the tool shapes are the
  published contract, so we either consume the `kushin77/code-indexing` service or
  we do not offer those tools — we do not maintain a private mirror of its shape.

**Rejected — "provenance licences re-implementation."** Treating a recorded
`harvested_from` line as permission to re-implement was rejected explicitly:
provenance is a *citation*, not an *authority*. It answers "where did this come
from", not "who owns it now". Reading it as a licence is exactly how #129/#130
recurred — a correct citation left a published contract un-consumed and locally
mirrored. This ADR is the citation the rule can be pointed at, so the distinction
is decidable next time rather than re-litigated.

### 4. Freshness — advisory until the vendor publishes a currency signal

A consumer that cannot tell whether the index is **current for *this* repo**
cannot gate on it. That is a real gap and it is **directional to the vendor**
(`kushin77/code-indexing#162`, filed by EPIC #472's chain); this ADR states our
behaviour **until it lands**:

- **We do not gate on index freshness.** `freshness()` here returns a *declared*
  `status` / `commit` / `indexedAt` (`kb.py`), so it proves nothing about
  currency; treating `status: ok` as "current" would be a declaration masquerading
  as a measurement.
- **Index output is advisory evidence, never a gate input.** Any decision that
  depends on a code fact is re-checkable against the checkout first
  (local-code-first, [`../GOLDEN-RULES.md`](../GOLDEN-RULES.md) GR-17); the index
  may *suggest*, only the checkout *confirms*.
- **A currency signal becomes a gate input on the day the vendor publishes one** —
  a per-repo freshness answer that is *measured*, not declared. Until then,
  freshness is a direction issue, not a check.

**Rejected — gate on the declared `freshness()` now.** Rejected explicitly: the
declared envelope is config, not measurement, so gating on it would pass on stale
data and read as green — the same false-green class as decision 2's fallback.

## Consequences

- **Positive:** the three surfaces have one stated relationship, so a shape can no
  longer be harvested and re-implemented unnoticed. The distinction between
  *adapting a pattern* and *depending on a service* is now citable rather than
  reconstructed. The gateway keeps the offline fixture it needs to exercise the
  tool-shape contract without ever pretending to be an index on a live path.
- **Negative:** consuming a published contract means consuming a service we do not
  yet have — the `kushin77/code-indexing` interface is not wired here, so the
  declared indexing tools remain offline-only until that consumption lands (a
  separate lane). A shape published by the vendor can no longer be tracked with a
  private mirror, so a vendor-side gap becomes a direction issue to their board,
  never a local edit.
- **Neutral:** no code changes. This record writes **no file** under
  `governance/knowledge/`, `gateway/mcp/` or `engine/memory/`; each surface's own
  lane owns its code, and any change there is a separate issue. It does not weaken
  [`../CANNIBALIZATION.md`](../CANNIBALIZATION.md)'s existing provenance entries or
  the record of issue #120 — it explains why that closed EPIC's namesake
  acceptance criterion ("consume the harvested indexer") did not hold: the shapes
  were correctly harvested and correctly cited, but nothing had recorded that a
  *contract* — not a pattern — was being mirrored, so re-implementation reading as
  success was the predictable outcome of an unstated rule. This ADR states it.
- **Follow-ups:** (1) a consumption lane wires the real `kushin77/code-indexing`
  interface (directional to `#162` for the freshness signal); (2) `kb.py`'s non-fixture
  role, if any, is decided by that lane against decision 2; (3)
  [`../CANNIBALIZATION.md`](../CANNIBALIZATION.md) carries a pointer to this record
  beside its harvest entries.

## Alternatives considered

| Option | Outcome | Verdict |
|---|---|---|
| Merge the three surfaces into one index | un-attributable answers; owners, licences and freshness models collide | **rejected** (decision 1) |
| Retire the declared in-memory graph (a) | the declared tool-shape contract goes unexercised offline | **rejected** (decision 2) |
| Keep the graph as a production fallback (c) | a fake answer stands in for a measured one on a live path | **rejected** (decision 2, load-bearing) |
| Let provenance license re-implementation | correct citation, un-consumed contract, #129/#130 recur | **rejected** (decision 3, load-bearing) |
| Gate on the declared freshness signal now | declared config read as measurement; passes on stale data | **rejected** (decision 4) |
