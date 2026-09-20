# Board triage — 2026-09-20

Issue #1574 (parent #1510). Machine sidecar: `governance/pmo/clusters.json`
(schema: `governance/pmo/clusters.schema.json`).

Scope: `kushin77/{agent-orchestrator, shared-services, shared-frontend,
shared-governance, purebliss, CMR, leaderboard, capital-underwriting}`, open
issues only, pulled via `gh issue list --state open --limit 500 --json
number,title,labels,assignees,createdAt,updatedAt,body,milestone`. All eight
repos exist and are not archived (confirmed via `gh repo list kushin77`).
660 open issues scanned. `capital-underwriting` retirement (memory note,
2026-09-18) has not removed its open issues, so it stays in scope.

Clustering method: a Python pass (kept out of this doc) tags each issue
against ten families by title/label/body keyword match (rca, lessons,
friction, offshore, governance-gate, template-backfill, iac-drift, outage,
docs, dependabot), groups matches by repo, and keeps a group as a cluster
only when it has 3+ issues (task's own bar for "a real win"). Everything
else — no family match, or a match too small to batch — goes to
`unclustered` with a reason. This is a mechanical first pass; it undercounts
true similarity (title-stem/body-text matching only, no semantic read), so
the 456-issue unclustered count should be read as "not mechanically
clusterable by keyword," not "unrelated."

## 1. Inventory

| repo | open | oldest # | stale >14d | unassigned | no-epic |
|---|---|---|---|---|---|
| agent-orchestrator | 72 | #132 | 0 | 18 | 19 |
| shared-services | 92 | #4088 | 0 | 76 | 78 |
| shared-frontend | 13 | #312 | 0 | 8 | 9 |
| shared-governance | 9 | #616 | 0 | 9 | 9 |
| purebliss | 10 | #109 | 10 | 10 | 10 |
| CMR | 73 | #202 | 0 | 73 | 73 |
| leaderboard | 265 | #1591 | 265 | 265 | 251 |
| capital-underwriting | 126 | #116 | 118 | 125 | 125 |

`no-epic` = body's first line is not `Parent: #N`. `unassigned` = no GitHub
assignee. SLA-breach column omitted: none of the eight repos' checked-out
config declared a discoverable `sla-enforcement`-style file at the time of
this pass, so P0/P1-age SLA breach could not be computed from local config;
flagged as a gap rather than fabricated.

## 2. Clusters (22 total, all wave 1/2, all L0, all batchable)

Every cluster in this pass is single-family, single-repo (paths and repo
conventions differ enough across repos that a cross-repo cluster would need
an "except for" clause per repo, which the task's own splitting rule
disallows). Full list with issue numbers is in `clusters.json`; top 10 by
size:

| cluster | family | repo | size | sme | tier | wave |
|---|---|---|---|---|---|---|
| c-docs-05 | docs | leaderboard | 28 | pmo-sme | L0 | 2 |
| c-rca-07 | rca | agent-orchestrator | 27 | platform-sme | L0 | 1 |
| c-docs-06 | docs | capital-underwriting | 22 | pmo-sme | L0 | 2 |
| c-outage-12 | outage | shared-services | 20 | platform-sme | L0 | 1 |
| c-outage-14 | outage | leaderboard | 15 | platform-sme | L0 | 1 |
| c-outage-15 | outage | capital-underwriting | 13 | platform-sme | L0 | 1 |
| c-docs-04 | docs | CMR | 11 | pmo-sme | L0 | 2 |
| c-docs-02 | docs | shared-services | 10 | pmo-sme | L0 | 2 |
| c-rca-08 | rca | shared-services | 6 | platform-sme | L0 | 1 |
| c-outage-13 | outage | CMR | 5 | platform-sme | L0 | 1 |

SME routing taken from `docs/SME-ROUTING.md`; tier default L0 per
`docs/MODEL-PROFILES.md`'s ladder — none of these clusters needed escalation
(mechanical recipes, no multi-constraint judgment calls).

456 issues fell to `unclustered` (69% of scanned issues) — each carries its
own `reason` field in `clusters.json` (`"no family keyword match"` or
`"family match <3 issues in repo"`). This is the honest output of a
keyword-only first pass; a second pass with an SME reading bodies would
likely raise the clustered fraction, and is recommended as follow-up before
the next triage date.

## 3. Priority order

1. **Outage family first** (c-outage-12/13/14/15, wave 1) — issues whose
   title/label/body matched `outage|incident|p0|sev1`. Evidence per cluster
   is the match count in `clusters.json`'s `evidence` field.
2. **RCA family next** (c-rca-07/08, wave 1) — same rank tier as outage per
   the priority-key used in the clustering pass (`outage`, `rca`,
   `governance-gate`, `iac-drift` all rank 0/1/2; no governance-gate or
   iac-drift cluster met the 3-issue bar this pass, so none appear).
3. **Everything else** (docs, dependabot, etc., wave 2) ranked by cluster
   size, descending — larger clusters are the bigger batching win per the
   task's own framing.

No SLA-breach evidence was computable (see §1), so that tier of the
requested ordering could not be populated; it is listed as a gap, not
silently dropped.

## 4. Hygiene actions (proposals only — nothing closed or edited)

- **Duplicates** (7 candidate pairs/groups, identical normalized title stem
  within the same repo, both open): see `hygiene.duplicates` in
  `clusters.json` for exact issue numbers and repo. Recommend an SME
  eyeball-confirm before closing either side.
- **Orphan children** (19 issues): body declares `Parent: #N` but #N is not
  open in the same repo — either the parent is closed or the number doesn't
  exist in that repo. See `hygiene.orphan_children`. Recommend confirming
  parent state before treating these as orphaned (a closed parent with open
  children is a legitimate "epic done, cleanup remains" state, not
  necessarily a hygiene bug).
- **Empty epics** (epics with zero open children): not computable from an
  open-issues-only pull (would need closed-issue diffing against parent
  refs across repos); left as an empty list with the gap noted rather than
  fabricated.
- **Template gaps**: shared-services issues with zero labels are flagged as
  a proxy for "missing template fields" per #4247/#4248's template shape;
  see `hygiene.template_gaps`. A full field-by-field diff against the
  template was out of scope for this pass's budget.

## 5. Paperclip / Hermes

Per the owner directive (paperclip → hermes downward), every cluster in
`clusters.json` is a paperclip ticket kind = `batch-triage-cluster`, with
hermes as the default downstream executor for every batchable cluster
(all 22 clusters here are `batchable: true`). No cluster in this pass needed
routing outside that default.

## 6. Labels

`cluster:<id>` (one per cluster, distinct color per family) and
`pmo:triaged` were created per-repo (idempotent — 422-already-exists treated
as success) and applied to every issue listed under a cluster in
`clusters.json`. See PR / issue for the applied-count and any repos where
the write was denied.

## 7. Known gaps in this pass

- SLA-breach detection and empty-epic detection could not be populated from
  an open-issues-only, no-config-discovered pull; both are called out above
  rather than guessed.
- The 69% unclustered rate reflects a keyword-only first pass, not a claim
  that most of the board is unrelated work.
