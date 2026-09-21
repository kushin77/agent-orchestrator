# RCA: Policy centralization gap review (2026-09-21)

## Acronym resolution (evidence-based, not assumed)

| Term | Resolution | Evidence |
|---|---|---|
| GDC | Tag-authority dimension used in issue conformance labels (`gdc:enterprise`), NOT Google Distributed Cloud | `governance/conformance/policy.yaml:116` declares `gdc: enterprise`; no other `gdc:` hit in docs/GOLDEN-RULES.md, docs/TAGGING.md, AGENTS.md |
| CMR | `vendor/CMR` — the pinned submodule (per CLAUDE.md: "vendor/ (pinned submodule)") | `ls vendor/` → `CMR` |
| CMR's own "GDC policy" | **Not found.** `grep -rln "gdc" vendor/CMR/` and `grep -rln "GDC\|golden.rule" vendor/CMR --include=*.md` returned zero hits | vendor/CMR has no gdc/GDC/golden-rule-named policy file. Owner's premise ("our CMR already has a GDC policy") does not match current vendor/CMR contents — either it was removed, never landed, or the owner means a different module. Flagging as unresolved rather than guessing. |
| GCS | Google Cloud Storage buckets | `infra/terraform/main.tf` is the only hit for `google_storage_bucket` under `infra/` |
| Cloudflare | DNS/WAF/edge ingress — real domain, not absent | `infra/cloudflare/{ingress.py,provision.py,ao-ssh-access.sh}`, `infra/feature-flags/registry.yaml`, plus doc scatter across `docs/EDGE-CUTOVER.md`, `docs/AGENTCONSOLE-*.md`, `docs/OPERATOR-ACCESS.md` |
| Local | Local-dev policy = AO-GR-17 (local-code-first) | `AGENTS.md:100`, `docs/GOLDEN-RULES.md:366`; enforced via `.pre-commit-config.yaml` + `.claude/settings.json` |
| Git | Branch protection (GitHub API), CODEOWNERS, AO-GR rules (AGENTS.md prose), squash-trailer script logic | see table below |

## Per-domain table: source / enforcement / control-plane visibility

| Domain | Policy source(s) | Enforcement point | Control-plane visibility |
|---|---|---|---|
| Git (branch protection, merge) | GitHub branch-protection API (live config, not IaC-declared) + `docs/GOLDEN-RULES.md` AO-GR-3/8/9/11 prose + `.github/CODEOWNERS` + `scripts/check-squash-message.sh` (hardcoded trailer regex) | pre-push hooks, CI gate, GitHub API | **scattered** — 4+ independent sources, no single read path; branch protection itself lives only in GitHub, not in this repo at all |
| GDC (tag authority) | `governance/conformance/policy.yaml` | `governance/conformance/` gate at issue-file time | **present** — single declared source, single consumer |
| GCS | `infra/terraform/main.tf` (IaC-declared, per AO-GR-5) | Terraform apply (never ad hoc, AO-GR-5) | **scattered-but-IaC** — declared once, but no governance/ mirror or control-plane surface reads bucket policy state |
| Cloudflare | `infra/cloudflare/*.py` scripts + scattered doc mentions (5 docs, no single spec) | `infra/cloudflare/provision.py` / `ingress.py` at deploy time | **scattered** — no single policy doc; edge policy is implicit in script logic |
| Local-dev (AO-GR-17) | `AGENTS.md` prose + `docs/GOLDEN-RULES.md` + `.pre-commit-config.yaml` + `.claude/settings.json` | pre-commit hooks | **scattered** — 2 declarative sources (doc + hook config), no registry entry |
| Isolation (AO-GR-2x, A2A leases) | `governance/isolation/`, `governance/policy/lease.py` | dispatch-time lease check | **present** — has its own `governance/policy/` module already (lease-focused only) |
| Secrets (AO-GR-6/7) | `docs/GOLDEN-RULES.md` + `governance/*` secret scan in `make verify` | CI gate | **scattered** — no dedicated governance/secrets module found |
| SemVer (AO-GR-8) | `docs/GOLDEN-RULES.md` prose only | none automated found | **absent** as enforced control-plane surface |
| Model-tier vocabulary | Re-declared in 5+ modules per open issue #1494 | n/a | **known scattered gap**, already tracked (do not duplicate) |

## Relationship to #1756 (settings aggregator)

#1756 (`portal/server/settings.py`, open, Parent #1667) aggregates **descriptive/observed** config: feature-flags.yaml, fleet-jobs.json, tier-policy.json, skip-budget.json, RBAC presets — read-only snapshot for the Settings view, explicit "no per-domain ad hoc readers" mandate.

Policy centralization (this review) is **prescriptive/enforced** state: branch protection, GDC tag rules, Cloudflare ingress rules, GCS bucket policy — things that gate or block actions, not values a UI displays. **Verified against #1756's specified row schema (module not yet built)** — separate concern, not an extension of #1756: #1756's row schema (`domain, key, value, source_file, editable:false`) has no field for enforcement point or violation consequence, which a policy registry needs. Recommend a sibling module (`governance/policy/registry.py` or similar) that #1756's settings view can later read from as one more domain, not the other way around.

## Related closed issues (checked, not duplicates)

Search hits for "policy registry" included #26 (22 Policy-as-code + gate engine, CLOSED) and #7 (03 Golden rules + policy spine, CLOSED). Both are EPIC-00 phase issues already closed; #26 built the rule-evaluation/gate engine, #7 codified docs/GOLDEN-RULES.md itself. Neither built a cross-domain aggregation/registry surface — #1763 is the registry those engines' policy state would be exposed through, not a re-do of either.

## Gap list

- #1763 — P0: no policy-registry aggregation verb in control-plane/ (structural gap, same shape as #1756 was for settings)
- #1764 — P0: Paperclip has no policy-delivery path from this repo's declarations (re-derives or is blind)
- #1765 — P1: Git policy fragmented across 4+ sources incl. GitHub-only branch protection (not IaC-declared)
- #1766 — P1: Cloudflare/edge policy has no single spec, implicit in script logic across 5+ docs
- #1767 — P2: GCS/Terraform-declared policy has no governance/ mirror for control-plane visibility
- #1768 — P2: local-dev policy (AO-GR-17) not registered as a control-plane-visible domain
- #1769 — P2: CMR/vendor GDC-equivalent policy claim unresolved — vendor/CMR has no gdc/policy artifact matching owner's description; needs source-of-truth reconciliation

## Roadmap (dependency order)

1. #1763 (policy-registry aggregation verb) — structural prerequisite; everything else registers into it
2. #1764 (Paperclip delivery path) -- depends on #1763 existing as the thing to deliver from
3. #1765 (Git) and #1769 (CMR/GDC reconciliation) -- can run parallel to each other once #1763 lands
4. #1766 (Cloudflare), #1767 (GCS), #1768 (local-dev) -- mechanical registrations into #1763, file-disjoint lanes, parallelizable

Lanes are file-disjoint: #1765 touches `.github/`, `scripts/`; #1766 touches `infra/cloudflare/`; #1767 touches `infra/terraform/` docs mirror; #1768 touches `docs/GOLDEN-RULES.md` cross-ref only; #1763/#1764 own `control-plane/` and `integrations/paperclip/` respectively.
