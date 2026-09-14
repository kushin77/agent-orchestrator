# SME routing + capability / route / tier FinOps contract

**Issue #149** (child of EPIC #144). This document is the reference for the
declared routing surface in `gateway/sme-routing/`: which SME handles which
domain, which worker fleet carries which agent role, how complexity and risk
select an agent chain and a model tier, what a tier's cost controls are, and
where escalation stops.

The policy is **declared data**, not code: three YAML documents under
`gateway/sme-routing/policies/`, validated against `gateway/sme-routing/schema.yaml`
(JSON Schema, draft-07) and then against a set of semantic invariants that JSON
Schema cannot express. `gateway/sme-routing/router.py` reads those documents and
hard-codes no policy value.

## 1. Where the rules live

| Piece of the contract                | Declared in                                    |
|--------------------------------------|------------------------------------------------|
| agent roles, worker fleets, chains    | `policies/capability-registry.yaml`            |
| domain -> SME -> authoritative module | `policies/capability-registry.yaml`            |
| squad (board lens) + its keywords     | `policies/capability-registry.yaml`            |
| complexity/risk -> route + chain      | `policies/route-policy.yaml`                   |
| task type -> route, fail-safe default | `policies/route-policy.yaml`                   |
| tier cost controls + escalation       | `policies/tier-policy.yaml`                    |
| complexity score -> tier              | `policies/tier-policy.yaml`                    |
| shape of all of the above             | `gateway/sme-routing/schema.yaml`              |
| referential + behavioural invariants  | `gateway/sme-routing/smeroute_config.py`       |

## 2. Which SME handles which domain

The classifier scores every declared domain by counting its keyword labels in
the task text and takes the highest score; a zero score falls to `general`. It is
the ported form of the harvested `classify_domain` / `DOMAIN_SME` mapping.

| Domain            | SME profile    | Authoritative module    | Keyword labels |
|-------------------|----------------|-------------------------|----------------|
| `general`         | `sniper-generic` | —                     | (none: the fall-through) |
| `testing`         | `test-quality`  | `vendor/testing-suite`  | 9 |
| `security-scanning` | `security`    | —                       | 15 |
| `debugging`       | `sniper-generic` | —                     | 6 |
| `infra`           | `terraform`     | `vendor/gcp-gatekeeper` | 10 |
| `frontend-vibe`   | `sniper-generic` | —                     | 8 |
| `backend-server`  | `prisma-db`     | —                       | 9 |
| `docs`            | `sniper-generic` | `vendor/mxdocs`        | 7 |

`general` is not "no answer": it is the declared verdict for work no domain
keyword selects, and `sme_default` (`sniper-generic`) is asserted by the loader
to equal `sme_domains.general.sme`, so the fall-through cannot drift from the
declaration.

An empty `module` is a declared "no authoritative module owns this domain" — a
verdict, not an omission. A non-empty module must exist in the ported authority
matrix, or the loader refuses the policy.

### Squads (board lens)

Declaration order is load-bearing: the classifier takes the first squad with a
keyword match, exactly like the harvested `case` statement. The keyword-less
squad is the declared `squad_default`.

| Squad     | Lens           | Keywords                                        |
|-----------|----------------|-------------------------------------------------|
| `security`| `lens:security` | secret, credential, token, auth, vulnerab, injection |
| `deploy`  | `lens:deploy`   | deploy, docker, compose, container, fleet       |
| `testing` | `lens:testing`  | test, qa, gate, coverage, fuzz                   |
| `staff`   | `lens:staff`    | strategy, architecture, advisor                  |
| `shell`   | `lens:shell`    | (none — the declared default arm)                |

## 3. Which fleet carries which role

| Worker fleet     | Access level | Agent roles          | Default model      |
|------------------|--------------|----------------------|--------------------|
| `text-fleet`     | `api`        | executor             | `deepseek-v4-flash` |
| `shell-fleet`    | `shell`      | executor             | `deepseek-v4-flash` |
| `code-fleet`     | `full`       | executor, planner    | `deepseek-v4-pro`   |
| `scout-fleet`    | `read`       | planner              | `deepseek-v4-flash` |
| `scribe-fleet`   | `write_docs` | planner, executor    | `deepseek-v4-flash` |
| `auditor-fleet`  | `read`       | verifier, critic     | `deepseek-v4-pro`   |

Agent roles: `planner` (decompose/estimate/choose), `executor` (edit/run/apply/
commit), `verifier` (test/validate/audit), `critic` (classify failure, propose a
retry plan, escalate to a human). Declared dispatch chains: `default_chain`
(executor, verifier), `governance_chain` (planner, executor, verifier, critic),
`doc_chain` (planner, executor), `audit_chain` (planner, verifier).

## 4. The route / tier contract

Selection is an ordered rule set; the first match wins.

| # | Signal                                                              | Route    | Tier    |
|---|---------------------------------------------------------------------|----------|---------|
| 1 | `risk=high`, or a declared risk keyword in the text                   | `strict` | `auditor` |
| 2 | a declared task type (`dispatch_defaults`)                           | declared | declared |
| 3 | a declared complexity keyword in the text                            | `deep`   | `pro`   |
| 4 | a token estimate at or above `deep_path_min_tokens` (300)            | `deep`   | `pro`   |
| 5 | a token estimate at or below `fast_path_max_tokens` (120)            | `fast`   | `flash` |
| 6 | **anything else — including an undeclared task type**                | `deep`   | `pro`   |

Risk outranks a declared cheap type: `type: doc_update` with "production
secret" in the text routes `strict`, not `fast`.

| Route    | `path_mode` | Agent chain                             | Tier      | Worker fleets |
|----------|-------------|-----------------------------------------|-----------|---------------|
| `fast`   | `fast`      | executor                                | `flash`   | text-fleet, shell-fleet |
| `deep`   | `deep`      | planner -> executor -> verifier         | `pro`     | code-fleet, scout-fleet, scribe-fleet |
| `strict` | `deep`      | planner -> executor -> verifier -> critic | `auditor` | auditor-fleet |

`strict` is the deep execution mode with the governance chain and the auditor
tier; the route **name** is what distinguishes it.

The tier is then the **highest** of three declared candidates, by tier order:
the route's tier, the tier for an explicit `complexity` score (0-30 `flash`,
31-70 `pro`, 71-100 `auditor`), and the task-type override (for example
`code-review` -> `auditor`). It is a maximum, never a minimum: routing can
escalate a tier but can never silently de-escalate one below its route.

Declared task types and their routes: `unknown_task_type` -> `deep`,
`legacy_task` -> `deep`, `doc_update` -> `fast`, `audit` -> `deep`,
`script_fix` -> `deep`, `config_change` -> `deep`, `code_review` -> `strict`.

## 5. The fail-safe: unknown work goes deep

A task the policy does not recognise **must not** travel the cheap path. The
loader enforces `dispatch_defaults.unknown_task_type == "deep"` as an invariant,
so the fail-safe is a checked declaration rather than a code default someone can
edit. `Decision.fail_safe` is set when the default was applied, so a caller can
tell a *decision* from a *default*, and the CLI prints `fail_safe: yes`.

## 6. Cost controls: timeouts, token caps, fallbacks

| Tier      | Models              | Timeout | Token cap | Fallback  |
|-----------|---------------------|---------|-----------|-----------|
| `flash`   | `deepseek-v4-flash` | 30 s    | 4 096     | `pro`     |
| `pro`     | `deepseek-v4-pro`   | 120 s   | 16 384    | `auditor` |
| `auditor` | `deepseek-v4-pro`   | 300 s   | 32 768    | `null`    |

`dispatch` enforces the chosen tier's caps:

* **within caps** -> `dispatched` on that tier;
* **cap breached, escalation not authorised** -> `refused`, and the reason names
  the breached cap and both numbers. Never a silent pass, never a downgrade to a
  narrower model;
* **cap breached, escalation authorised** -> climb the declared ladder.

The ladder is validated at load time, not trusted at runtime: every fallback must
name a declared tier, the ladder must be acyclic, must ascend strictly in the
declared tier order, must terminate at a tier whose fallback is `null`, and each
rung must buy **strictly more** tokens and time than the tier below it. A rung
that buys no more capacity is refused as decorative.

## 7. The escalation terminal: a human / advisor

When the top tier's caps are also breached there is no higher tier. The router
does **not** raise and does **not** loop: it returns the distinct
`human_advisor` outcome, whose reason is
`no higher tier than 'auditor' (its fallback is null): terminal escalation is a
human/advisor hand-off`. Every rung it tried is recorded in `Outcome.attempts`,
so the hand-off carries the evidence of what was attempted.

The terminal is bounded by construction: the ladder is acyclic and strictly
increasing, and the loop is additionally capped at the tier count.

## 8. Exit-code contract (tri-state)

The CLI reports `0 OK / 1 NOT-OK / 2 CANNOT-ASSESS`, and the line between the
failure classes is deliberate:

| Code | Meaning                                                                                        |
|------|------------------------------------------------------------------------------------------------|
| 0    | the policy validates, the task routed, or the dispatch was accepted — including `human_advisor` |
| 1    | a declared invariant is violated, or a dispatch is `refused` under `--no-escalate`              |
| 2    | the policy is missing / unparseable / schema-invalid, or the request cannot be assessed at all   |

A malformed policy is **CANNOT-ASSESS (2), never OK**. So is a request the
router cannot fully assess: an unknown task key, an out-of-range complexity
score, a non-numeric budget. `human_advisor` is rc 0 because the *engine*
behaved correctly — `status=` carries the answer for a machine reader.

## 9. Blast radius

Consumers that would be affected by a change here: model selection at the
gateway proxy, the execution engine's dispatch, the control-plane/portal
surfacing of routing decisions, and the fleet's lane dispatch. The tier
vocabulary (`flash` / `pro` / `auditor`) is the same closed set
`gateway/finops/chooser.py` pins.

The failure modes this surface exists to prevent, and which the invariants above
block:

| Drift                                  | Consequence without the invariant                        |
|----------------------------------------|----------------------------------------------------------|
| fail-safe re-pointed at `fast`          | unknown work silently runs on the cheapest model          |
| a cap raised silently                   | no cost control at the tier                               |
| a ladder rung that buys less capacity   | escalation that cannot help, mistaken for a fix           |
| a cyclic or open-ended ladder           | unbounded retry                                           |
| a route naming an undeclared agent/fleet| dispatch to a role that does not exist                    |
| a dead domain (no keywords)             | a routing rule no input can ever select                   |
| a complexity gap                        | tasks whose score maps to no tier, silently defaulted     |

This lane changes no runtime wiring: it adds `gateway/sme-routing/**`,
`scripts/check-sme-routing.sh` and this document. It does not touch
`gateway/proxy/**`, `gateway/finops/**`, `gateway/health/**`,
`gateway/providers/**`, `registry/**`, `governance/**`, `fleet/**`, the
`Makefile` or `scripts/verify.sh`.

## 10. Verification

```bash
python3 -m pytest gateway/sme-routing/tests -q     # behavioural + mutation tests
bash scripts/check-sme-routing.sh; echo "rc=$?"    # standalone tri-state gate
python3 gateway/sme-routing/cli.py demo            # the three paths, asserted
```

`scripts/check-sme-routing.sh` is standalone and is **not** wired into
`scripts/verify.sh`. It refuses to pass vacuously: it builds three mutated policy
variants in a scratch directory (re-point the fail-safe -> rc 1, break the shape
-> rc 2, neutralise the risk keywords -> the strict route must stop being
chosen) and fails if any variant still produces the shipped answer. It also
hashes the shipped policies before and after, proving they are read-only inputs.

## 11. PROVENANCE (GR-10 cannibalization ledger)

Sources were read from the local checkouts and re-implemented as the repo's
declared standard stack (Python + PyYAML). Verdicts:

| Source repo | Path | Verdict |
|-------------|------|---------|
| `kushin77/capital-underwriting` | `config/leaderboard/capability-registry.json` | **PORTED** — `agents`, `worker_fleet`, `dispatch_routing` kept verbatim (ids, owners, capabilities, tools, latency/cost tiers, fallbacks, access levels, chains, default models); re-expressed as schema-validated YAML |
| `kushin77/capital-underwriting` | `config/leaderboard/route-policy.json` | **PORTED** — `thresholds`, `routes`, `dispatch_defaults` kept verbatim, including the `strict` route's `path_mode: deep` |
| `kushin77/capital-underwriting` | `config/leaderboard/tier-policy.json` | **PORTED** — `tiers`, `complexity_to_tier`, `task_overrides` kept verbatim (timeouts, token caps, fallbacks) |
| `kushin77/capital-underwriting` | `scripts/agent/sme/sme-dispatch.sh` | **RE-IMPLEMENTED** — `DOMAIN_LABELS`, `DOMAIN_DESC`, `DOMAIN_SME` ported as declared data; the bash `classify_domain` scoring loop and the `${DOMAIN_SME[$domain]:-sniper-generic}` fallback became `Router.classify_domain` / `classify_sme`; the CLI shape (`classify`, `sme-prompt`, `valid`, `list`) became the `sme` subcommand plus loader invariants |
| `kushin77/leaderboard` | `scripts/dispatch/sme-squad-router.sh` | **RE-IMPLEMENTED** — `SQUAD_LENS` and the `_infer_squad` keyword arms ported as declared data; the bash `case` first-match and its `*) echo "shell"` fallthrough became `Router.classify_squad` and the declared `squad_default` |
| `kushin77/capital-underwriting` | `config/agent-module-authority.json` | **PORTED SUBSET** — `domain`, `module`, `owns`, `policy` only |

Lineage recorded but not re-ported: the two JSON sources name their own upstream
as `kushin77/vscode-memory` `integration/*.json` (#1476); that lineage is
preserved in each policy file's provenance comment.

### Deliberate porting decisions (all covered by tests)

* **Repository-local fields dropped.** `agent-module-authority.json`'s `tracks`,
  `command_to_reference`, `documentation`, `auto_sync`, `enforcement` and
  `agent_guidance` describe the capital-underwriting repo's own workflow; only the
  authority matrix itself is meaning-bearing here.
* **`_` and `-` folded into one key space.** The harvest spells route-policy keys
  with underscores (`doc_update`) and tier-policy overrides with hyphens
  (`doc-update`). Both are preserved verbatim; the loader normalises and refuses a
  policy whose keys collide after normalisation, so a declared override can never
  be skipped by a spelling mismatch. This is what makes the override findable, and
  a mutation test proves it.
* **Deterministic tie-break.** The harvested bash classifier iterated an unordered
  associative array, so a score tie between two domains was arbitrary. The port
  resolves a tie to the first *declared* domain, which is stable across runs.
* **Substring label matching kept, not "improved".** The harvest greps each label
  as a substring, so a short label matches inside a word (`ui` in "quick", giving
  a false `frontend-vibe`). The port keeps that behaviour and a test pins it,
  rather than inventing word-boundary semantics the source does not have.
* **Domain -> module is a derived edge.** The authority matrix's domains are not
  the SME domain names. The three links declared here (`testing` ->
  `vendor/testing-suite`, `infra` -> `vendor/gcp-gatekeeper`, `docs` ->
  `vendor/mxdocs`) follow from the matrix's own domain names; the remaining SME
  domains carry `module: ""` as a declared "no authoritative module".
* **New invariants, not new data.** The escalation ladder rules and the
  "escalation must buy more capacity" rule are additions of *validation*, not of
  policy content: the harvested numbers already satisfy them.
