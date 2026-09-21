---
id: ADR-000X
status: proposed
date: YYYY-MM-DD
deciders: []
req: []
supersedes: []
---

# ADR-000X: <short decision title>

## Status

`reserved` · `proposed` · `accepted` · `live` · `superseded` · `deprecated` —
pick one,
keep it in sync with the front-matter `status:` key (checked by
`scripts/check-adr-status.sh`, part of `make verify`). A record leaves
`proposed` only via review. `accepted` means decided but not yet promoted;
`live` means the decision's subject is deployed — add a `live_resource:`
front-matter key naming the resource/service that makes it so. `superseded`
requires a populated `superseded_by:` front-matter key naming the ADR that
replaces it (issue #1619). Reserved numbers are index-only (see
[`README.md`](README.md)) and are not decided here.

## Context

What is the problem, constraint, or observation that forces a decision? Frame
the decision space — options considered, prior art, the tradeoffs that matter.
Be concrete enough that someone who was not present can reconstruct the moment.

## Decision

The decision, stated as a single, crisp commitment. What we will do — not the
options we weighed. Reference golden rules (AO-GR-N in
[`../GOLDEN-RULES.md`](../GOLDEN-RULES.md)) and superseded ADRs here.

## Consequences

- **Positive:** what adopting this decision gains.
- **Negative:** what it costs or forecloses.
- **Neutral:** things that change but are neither good nor bad.

List concrete follow-ups (docs to update, reservations to fill, work to
schedule). If this decision is later reversed, that is a **new** ADR.
