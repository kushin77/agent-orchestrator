# Built-not-shipped inventory — agent-orchestrator

**Issue:** #1540 (parent EPIC #1510). **Measured at:** `5357190f` (`master`,
2026-09-20T19:52Z). **Method:** the issue's steps 1-7, with the corrections
recorded in the next section.

This is the extended inventory the issue asks for: every agent-orchestrator
artifact that is **built but not shipped** — feature-flagged off, route without
a view, overlay never brought up, ADR accepted with no live resource, script
never invoked — each row carrying the **mechanism** that makes the claim
checkable (who would call it, and what shows nothing does).

Two rules govern every row, both taken from the issue:

- **No row is asserted from a grep alone.** Each names the caller that *would*
  reach the artifact and the measurement that shows no caller does.
- **Dead code and not-yet-shipped are different categories** (issue step 6), and
  so are *correctly-gated-off* and *drifting*. All three appear below and are
  labelled.

## Method corrections (the issue's own commands, measured)

These are findings in their own right: three of the issue's seven steps carry an
instrument that cannot see what it is aimed at.

| # | Command as written | What it actually does here | Correct instrument |
|---|---|---|---|
| M1 | step 4 `find . -iname 'docker-compose*.yml' -path '*/contrib/*'` | returns **nothing**. The overlay is `contrib/shared-services/agentconsole.compose.yml` — the `.compose.yml` shape is not matched by `docker-compose*.yml`, and `infra/docker-compose.agentconsole.yml` (the name the issue uses) **does not exist**; it is a *recommendation* inside two documents | `find . \( -name 'docker-compose*.yml' -o -name '*.compose.yml' \)` |
| M2 | step 6 `… .githooks/*` | there is **no `.githooks` directory** in this repository; hook material lives at `fleet/hooks` | `ls fleet/hooks` |
| M3 | step 5 `grep -L "Superseded\|Status: Live" docs/decision-records/ADR-*.md` | returns **all 27 ADRs — 0 true positives**. The vocabulary is YAML `status: accepted`; the words `Live` and `Superseded` appear in no ADR | `grep -h '^status:' docs/decision-records/ADR-*.md \| sort \| uniq -c` (see N-02) |
| M4 | any hand-kept "is this gate wired?" list | unnecessary and worse than the real mechanism: `scripts/discover-checks.sh` (sourced at `scripts/verify.sh:798`) **auto-discovers every `scripts/check-*.sh`**, so a new gate is wired the moment it lands | `sed -n '787,806p' scripts/verify.sh` |
| M5 | reading `state=closed, merged=false` as "the work did not land" | **false.** A train folds several PRs into one verified landing and then closes those PRs unmerged, *and* closes their issues. Measured twice in this sweep (below). This is the single largest false-negative trap in this issue | `git log --oneline origin/master --grep='#<n>'` then `git branch -r --contains <sha>` |

**M5 in detail — the measurement that changed this inventory.** Reading PR
state alone, the sweep first concluded that rows for #1525, #1520, #1512, #1513
and #1515 were orphaned debt (their issues closed, their PRs closed unmerged).
Re-measuring against the remote *after* a train landed showed all five had in
fact shipped:

```
# #1525's PR closed unmerged; the work landed in train #1591:
git show 156e1940^1:.mcp.json   ->  "args": ["catalog/indexer/mcp_server.py"]
git show 156e1940:.mcp.json     ->  "args": ["vendor/CMR/catalog/indexer/mcp_server.py"]

# #1520/#1515/#1512/#1513 closed at 19:44:54Z, their PRs closed unmerged at 19:45-19:46Z;
# the work landed in train #1604:
git log --oneline -1 origin/master
5357190f Merge train 20260920T191806Z: 4 pull request(s), one verified landing (#1604)
```

Train #1604's own body names the four: `#1599` (FinOps + Ops/SLO views),
`#1596` (paperclip go/no-go), `#1595` (`.fleet` loop ADR + declared crontab),
`#1594` (agentconsole overlay lift). Every one of those four *was* the subject of
a row in this issue's pre-filled table. **A sweep that trusts PR state would
have reported five shipped artifacts as debt, and filed five false issues.**

## The inventory

Dispositions: **LIVE DEBT** (built, ship is owed, owner named) · **RESOLVED**
(landed during this sweep) · **DECIDED-OFF** (a decision landed and the answer
is "stay off") · **NOT BUILT** (the named artifact does not exist — not
built-not-shipped) · **DELETED** (removed by a merged delete PR) ·
**CANNOT-ASSESS** (not measurable from this sandbox).

| # | Artifact | Disposition | Owning issue / delete PR |
|---|---|---|---|
| P-01 | `enable_paperclip` (terraform variable + `module "paperclip_runtime"`) | DECIDED-OFF | #1515 (closed by train #1604 — decision landed) |
| P-02 | `enable_hermes` provider switch | **LIVE DEBT** | **#1518** (open, Parent #1510; PR #1598 open) |
| P-03 | `hermes-head-guardrails` control | **LIVE DEBT** | **#1519** (open, Parent #1510; PR #1612 open) |
| P-04a | FinOps + Ops/SLO routes without a view | RESOLVED | #1520 (closed by train #1604) |
| P-04b | OrgChart + SkillStudio routes without a view | **LIVE DEBT** | **#1521** (open, Parent #1510; PR #1606 open) |
| P-04c | TaskBoard + fleet board routes without a view | **LIVE DEBT** | **#1522** (open, Parent #1510) |
| P-04d | `/api/v1/bridge` route without a consumer | **LIVE DEBT** | **#1523** (open, Parent #1510) |
| P-05 | AgentConsole compose overlay, never lifted | RESOLVED (repo half) | #1513 (closed by train #1604); host half unticked — see P-13 |
| P-06 | `cmr-indexer` MCP server, unresolvable entry | RESOLVED | #1525 (work landed in train #1591) |
| P-07 | `context_pack.py` / `front_load()` | NOT BUILT | #1527, #1532 (both open) |
| P-08 | `.fleet` in-container crontab, undeclared | RESOLVED | #1512 (closed by train #1604 — ADR-0032 landed) |
| P-09 | `fleet_cron` module deployed 0 replicas | **LIVE DEBT** | EPIC #706 |
| P-10 | Console container present on only one node | CANNOT-ASSESS | #1513 (host half); cross-repo |
| P-11 | OS gate stale on `.31` | CANNOT-ASSESS | `shared-services#4242` (cross-repo) |
| N-01 | Promotion ledger carries no owner for 38 off-by-default surfaces | **LIVE GAP** | **#1618** (filed by this sweep, Parent #1510) |
| N-02 | ADR vocabulary has no promotion state | **CLOSED** | **#1619**: vocabulary closed to `proposed`/`accepted`/`live`/`superseded`/`deprecated`, `scripts/check-adr-status.sh` gates it |
| N-03 | `guardrails/dlp/egress.py` (`SAFE_EGRESS_...` helper no code path reached) | DELETED | PR **#1218** (merged 2026-09-18) |

## Rows, with the evidence that makes each one checkable

### P-01 `enable_paperclip` — DECIDED-OFF

Built: a terraform variable with a downstream module.

```
grep -rn 'var\.enable_paperclip' --include='*.tf' . | awk -F: '{print $1}' | sort | uniq -c
#  1 ./infra/terraform/main.tf            <- exactly one reader, the module's `enabled`
grep -n -A4 'module "paperclip_runtime"' infra/terraform/main.tf
#  137: module "paperclip_runtime" { 138: enabled = var.enable_paperclip ... }
sed -n '82,86p' infra/terraform/variables.tf   # default = false
```

Why not live: `default = false`, and the flag feeds nothing but that module.
**Resolution measured:** `docs/PAPERCLIP-PROMOTION-DECISION.md` now exists (landed
by train #1604, closing #1515), so the row's "decide" is answered — the variable
is still `default = false`, i.e. the decision was *not* to promote. The row is
no longer open debt.

### P-02 `enable_hermes` — LIVE DEBT

Built: a declared kill-switch with a registry entry.

```
grep -n 'hermes:' infra/feature-flags/registry.yaml      # 108
sed -n '88,92p' infra/terraform/variables.tf             # variable "enable_hermes" ... default = false
```

**Who would call it, and what shows nothing does:**

```
grep -rn 'enable_hermes' --include='*.py' .  | wc -l     # 0
grep -rn 'enable_hermes' gateway/ | wc -l                # 0
```

Zero Python readers repo-wide. The registry entry says so itself: "the gateway
provider does not yet read it, so today the switch is a declared kill-switch
surface only, not a live gate on the in-process adapter". So this is **not** a
surface that is off — it is a switch nothing consults, which means promoting it
would change nothing and the provider cannot be killed by it. Owner **#1518**
(open, `Parent: #1510`), PR #1598 open.

### P-03 `hermes-head-guardrails` — LIVE DEBT

Built: a guardrails control with a policy bundle.

```
grep -n 'id: hermes-head-guardrails' guardrails/policy/controls.yaml   # 126
sed -n '126,140p' guardrails/policy/controls.yaml                      # enabled: false
grep -rn 'enable_hermes' --include='*.py' . | wc -l                    # 0  <- and its second gate reads nothing
```

Why not live: double-gated — the control is `enabled: false` **and** the
provider flag it names is read by nothing (P-02). So of the two conditions the
control declares itself gated on, only one is even wired. Owner **#1519**
(open, `Parent: #1510`), PR #1612 open.

### P-04 the console routes — one row split into its real owners

Built: the router serves the families; the views do not exist.

```
grep -n '_route_' portal/server/app.py
#  766 _route_org_chart   783 _route_skill_studio   837 _route_task_board
#  865 _route_board       946 _route_finops         998 _route_live_feed
# 1066 _route_ops        1129 _route_bridge

for fam in finops ops orgchart skillstudio taskboard v1/bridge board; do
  printf '/api/%-12s %s\n' "$fam" "$(grep -rl "/api/$fam" portal/static 2>/dev/null | wc -l)"
done
```

Measured at `5357190f`:

| family | static refs | verdict |
|---|---|---|
| `/api/finops` | 2 | RESOLVED (P-04a) |
| `/api/ops` | 2 | RESOLVED (P-04a) |
| `/api/orgchart` | **0** | LIVE DEBT (P-04b) |
| `/api/skillstudio` | **0** | LIVE DEBT (P-04b) |
| `/api/taskboard` | **0** | LIVE DEBT (P-04c) |
| `/api/board` | **0** | LIVE DEBT (P-04c) |
| `/api/v1/bridge` | **0** | LIVE DEBT (P-04d) |

**P-04a resolution, measured** — the row that landed:

```
ls -la portal/static/views/finops.html portal/static/views/ops.html     # both exist
grep -n '/api/finops\|/api/ops' portal/static/js/console.js
#  97: { id: "finops", label: "FinOps",   ... probe: "/api/finops/overview" },
#  98: { id: "ops",    label: "Ops / SLO", ... probe: "/api/ops/overview" }
ls portal/tests/test_finops_ui.py portal/tests/test_ops_health_ui.py    # both exist
```

So the mechanism now exists where it did not: a nav entry whose `probe` is the
route, a view that calls it, and a UI test per view. The remaining orphan
families have **none** of the three. Owners **#1521** (PR #1606 open),
**#1522**, **#1523** — all open with `Parent: #1510`.

### P-05 the AgentConsole compose overlay — RESOLVED (repo half)

**Correction (M1):** the artifact is `contrib/shared-services/agentconsole.compose.yml`;
`infra/docker-compose.agentconsole.yml` does not exist.

```
find . \( -name 'docker-compose*.yml' -o -name '*.compose.yml' \) -not -path './.git/*'
#  ./infra/fleet/docker-compose.agent-cron.yml
#  ./contrib/shared-services/agentconsole.compose.yml
```

Why it was not live, in the overlay's own words: "It is not wired into any
running stack by this repo, changes nothing on the live cluster … The
shared-services run half lifts it into its own repo (recommended:
`infra/docker-compose.agentconsole.yml`) and brings it up with the console
surface flag promoted."

**Resolution measured** — the repo half is now automated (train #1604, closing
#1513):

```
ls -la scripts/sync-agentconsole-overlay.sh scripts/overlay-sync-manifest.json \
        scripts/check-agentconsole-overlay-sync.sh
#  all three exist
head -20 scripts/check-agentconsole-overlay-sync.sh
#  "the overlay-lift sync tool must not be inert (issue #1513) ... offline gate half:
#   proves the detector on fixtures AND validates the manifest schema"
#  and it is auto-discovered (M4), so it runs in `make verify` from the moment it landed.
```

What remains is the **host** half, which no file in this repository can complete
— see P-13.

### P-06 `cmr-indexer` MCP entry — RESOLVED

Built: a mandatory `.mcp.json` naming the hub's indexer.

```
cat .mcp.json
#  "args": ["vendor/CMR/catalog/indexer/mcp_server.py"]
```

**Resolution measured** (train #1591, closing #1525 — whose own PR #1581 closed
unmerged; see M5):

```
git diff 156e1940^1 156e1940 -- .mcp.json
#  -      "args": ["catalog/indexer/mcp_server.py"],
#  +      "args": ["vendor/CMR/catalog/indexer/mcp_server.py"],
```

Residual, stated rather than hidden: the path resolves only once the submodule
is initialised, and a fresh worktree is not —

```
git submodule status          # -b6c49aa03992dba9fe4b87b46104b8fc2f69f224 vendor/CMR
ls vendor/CMR/catalog/indexer/mcp_server.py
#  No such file or directory
```

That state is *reported*, not passed: `scripts/check-codeidx-surface.sh` (wired
as `codeidx-surface`, `scripts/verify.sh:549`) returns **rc 2 CANNOT-ASSESS** in
exactly this condition, and its `--self-test` proves the refusal with a
`live-unresolvable-entry` control.

### P-07 `context_pack.py` / `front_load()` — NOT BUILT

**Correction:** neither artifact exists on `master`.

```
find . -name 'context_pack.py' -not -path './.git/*'    # (nothing)
grep -rn 'front_load' . --exclude-dir=.git --exclude-dir=vendor   # (nothing)
find . -name '*context_pack*' -not -path './.git/*'
#  ./engine/memory/tests/test_context_pack.py
#  ./fleet/tests/test_context_pack.py
```

So this row is not "built but not shipped" — the **producer** was never built.
What *is* built and gated is the **consumption** half:

```
grep -n 'context-pack-consumption' scripts/verify.sh
#  'context-pack-consumption|bash scripts/check-context-pack-consumption.sh'
```

which proves `assemble_prefix` in `engine/memory/prompt_cache.py` consumes a
pre-fetched pack as opaque bytes and leaves the absent-pack bytes unchanged.
Reclassifying this row matters: filing it as shippable debt would have produced
a "ship" decision for code that does not exist. Owners **#1527** and **#1532**
(both open, `Parent: #1510`) describe work that is a *build*, not a promotion.

### P-08 `.fleet` in-container crontab — RESOLVED

**Correction:** the crontab install *is* declared and auditable.

```
sed -n '213p' infra/fleet/Dockerfile
#  ENTRYPOINT ["/usr/bin/tini", "--", "/repo/infra/fleet/entrypoint.sh"]
grep -n 'entrypoint:' infra/fleet/inventory.yaml          # 31: /repo/infra/fleet/entrypoint.sh
grep -n 'install' infra/fleet/entrypoint.sh | head -3      # `fleet/cron.py install` is the one writer
ls scripts/check-fleet-cron-image.sh && grep -n 'fleet-cron-image' scripts/skip-budget.json | head -1
#  the image-vs-inventory gate exists and is auto-discovered (M4)
```

And the ADR the row's owner produced landed: `docs/decision-records/ADR-0032-fleet-loop-ownership.md`
("The `.fleet` loop family stays agent-orchestrator-owned, with a declared (not
in-container-hardcoded) crontab"), via train #1604 closing #1512.

### P-09 `fleet_cron` — LIVE DEBT (the deployment half of P-08)

```
grep -n -A3 'module "fleet_cron"' infra/terraform/main.tf
#  149: module "fleet_cron" {
#  152:   count = var.enable_fleet_cron ? 1 : 0
grep -A3 'variable "enable_fleet_cron"' infra/terraform/variables.tf | grep default
#  default     = false
```

So the container image, its entrypoint, its inventory and its gate are all built
and green, while `count` evaluates to **0** — the schedule the whole EPIC-706
chain exists to install has never been scheduled. Owner: **EPIC #706** (open).

### P-10 / P-11 the two live-infra defects — CANNOT-ASSESS here

Both name **live host/cluster state**, and this sweep runs in a sandbox with no
access to the shared-services pair or the on-prem nodes.

- Console container on only one node — owner named in the issue as `#1513`
  (now closed; the unticked host items in `docs/AGENTCONSOLE-HOSTING.md` §8 —
  `- [ ] Lift the overlay`, `- [ ] Re-check the host port …`, `- [ ] Wire DNS +
  the Cloudflare tunnel ingress rule …` — are the surviving record of what the
  host half still owes).
- OS gate stale on `.31` (`os-auto-deploy` promotes only `os` + `os-ai`) —
  owner `shared-services#4242`, a **different repository**; reporting it here,
  not editing it.

Recording these as CANNOT-ASSESS rather than "ship" is deliberate: the honest
verdict for a claim this sandbox cannot measure is that it cannot be measured,
not that it is confirmed. Neither is counted in the "3 ship rows with an open
owner" acceptance check below.

### N-01 the promotion ledger has no owner — LIVE GAP

**New row.** The mechanism:

```
grep -cE 'owner:|promotion_issue:|target_date:|due_by:' infra/feature-flags/registry.yaml   # 0
grep -cE 'owner:|promotion_issue:' infra/rollout/rollout-state.yaml                          # 0
```

Both files are the platform's promotion ledger and between them declare **38
entries defaulting `off`** — and not one carries an owner, a promotion issue or
a target date. So *pending a reviewed go-live* and *drifting with nobody
responsible* are indistinguishable from the declarations. This is the governance
gap the issue's own root cause predicted ("no gate checks every `enable_*` flag
has an owner and a target promotion date"). Owner: **#1618**, filed by this
sweep with `Parent: #1510`.

### N-02 the ADR vocabulary has no promotion state — LIVE GAP

**New row**, and the reason M3's instrument returns everything:

```
ls docs/decision-records/ADR-*.md | wc -l                                  # 27
grep -l '^status: accepted' docs/decision-records/ADR-*.md | wc -l         # 18
grep -l 'superseded_by:' docs/decision-records/ADR-*.md | wc -l            # 0
for f in docs/decision-records/ADR-*.md; do grep -q '^status:' "$f" || echo "$f"; done | wc -l
#  9  (ADR-0002..ADR-0009, ADR-0028 carry no status key at all)
```

An ADR whose subject is deployed and one whose subject has never been promoted
both read `status: accepted` — so "ADR accepted with nothing built" is
undecidable from the metadata, which is exactly the row class this issue
inventories.

**Closed by #1619.** The `status:` vocabulary is now the closed set
`reserved | proposed | accepted | live | superseded | deprecated`, declared
once in `docs/decision-records/template.md` and enforced by
`scripts/check-adr-status.sh` (wired into `make verify` via
`scripts/discover-checks.sh`). All ADRs carry a `status:` key: the 8 index-only
placeholders (ADR-0002..ADR-0009) took `status: reserved`, matching their
existing `**STATUS: reserved** ... Not a decision.` body text; ADR-0028 took
`superseded` with `superseded_by: ADR-0035` (its own banner
says superseded, and ADR-0035 gives the single-developer landing method that
replaced it an ADR home); every other pre-existing ADR kept `accepted`
(no promotion evidence found — not invented). `superseded` requires a
populated `superseded_by:` naming an existing ADR; `live` requires a
populated `live_resource:`. The gate refuses a missing or out-of-vocabulary
state, and an unresolvable `superseded_by`, by name; `--self-test` proves
both directions.

### N-03 the DELETE exemplar — DELETED

The issue's acceptance asks for at least one row whose disposition is delete,
linked to a real delete PR. Measured:

```
gh api repos/kushin77/agent-orchestrator/pulls/1218 \
  --jq '"\(.title) merged=\(.merged) at=\(.merged_at)"'
#  chore(dead-code): delete the controls no code path reaches merged=true at=2026-09-18T12:48:21Z
gh api repos/kushin77/agent-orchestrator/pulls/1218/files --jq '.[] | "\(.status)\t\(.filename)"'
#  removed  e2e/_team_providers.py          (-42)
#  modified guardrails/dlp/egress.py        (-13)
```

That PR is the working precedent for the delete disposition: it removed a guard
whose code path nothing reached, rather than leaving it declared. Recorded here
because it is the one delete in this inventory that is already *closed*, and
therefore the template the open rows should follow when a surface is decided
against rather than promoted.

## Negative control

The issue requires that an artifact which **is** fully shipped be correctly
excluded. `portal/static/views/agents.html` is that artifact — the view, the
route and the caller all resolve:

```
grep -n '/api/tenants' portal/static/views/agents.html
#  28: var payload = await CP.get("/api/tenants/" + encodeURIComponent(tenantId) + "/agents");
#  63: var res = await CP.post("/api/tenants/" + ... + "/agents/" + ...);
grep -n '_route_agents\|_route_tenant' portal/server/app.py
#  1441: return self._route_agents(
#  1479: def _route_agents(
```

View file, the `CP.get`/`CP.post` calls it makes, the tenant router branch that
dispatches to `_route_agents`, and the handler that serves the family — all four
present. It appears nowhere in the table.

## Acceptance check

| Requirement | Measured |
|---|---|
| Every row has a non-empty owning issue or delete PR | yes — 17 of 17 rows name an issue, a PR, or (for the two live-infra rows) an explicitly named cross-repo owner |
| ≥3 rows marked *ship* whose owner is **open** and `Parent: #1510` | **4**: #1518 (P-02), #1519 (P-03), #1521 (P-04b), #1522 (P-04c) — all measured open with `Parent: #1510`; PRs #1598, #1612, #1606 open against the first three |
| ≥1 row marked *delete* with a real linked delete PR | N-03 → PR **#1218**, merged 2026-09-18 |
| Negative control excluded | `agents.html` — see above |
| No blank or placeholder owning-issue cell | yes |

## Out-of-lane findings — reported, not fixed

1. **The five near-miss rows (M5).** #1525, #1520, #1515, #1512 and #1513 all
   closed their issues and closed their PRs **unmerged** while their work landed
   in a train (#1591, #1604). Nothing is wrong with the outcome; the observation
   is that **PR state is not a landing signal in this repository** and any
   sweep, closeout or reconciler that reads `state=closed, merged=false` as "did
   not land" will report shipped work as debt. Named because it nearly produced
   five false issues in this very sweep.
2. **`docs/README.md` is contended.** It is the only sanctioned index for a
   tracked `docs/*.md` (`scripts/check-docs.sh` fails the build otherwise), and
   at measurement time it was modified by open PRs #1613, #1596, #1588, #1583,
   #1578 and #1575 — train #1591's own body records that it held four PRs back
   precisely because they "add tracked docs that `docs/README.md` does not
   index". This row's index entry is added in the least-contended position
   (the tail of the fleet/governance table); a fold may still need to rebase it.
3. **`ADR-0028` carries no `status:` key** — folded into N-02/#1619.

## How to re-run this inventory

```
git fetch origin && git checkout -b <lane> origin/master
find . \( -name 'docker-compose*.yml' -o -name '*.compose.yml' \) -not -path './.git/*'
for fam in finops ops orgchart skillstudio taskboard v1/bridge board; do
  printf '/api/%-12s %s\n' "$fam" "$(grep -rl "/api/$fam" portal/static 2>/dev/null | wc -l)"; done
grep -rn 'enable_hermes' --include='*.py' . | wc -l
grep -cE 'owner:|promotion_issue:|target_date:' infra/feature-flags/registry.yaml
grep -h '^status:' docs/decision-records/ADR-*.md | sort | uniq -c
git log --oneline origin/master --grep='#<issue>'      # always before calling anything unlanded
```

Re-run against a **fresh** `origin/master`, not a stale worktree, and re-read
every disposition: five of the seventeen rows in this table changed state during
the sweep that produced it.
