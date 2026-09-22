# Human-in-the-loop gap analysis and elimination roadmap (2026-09-21)

Every point in this session where work stopped for an owner decision, grouped
by why it stopped, with the mechanical/IaC change that would remove the
human step — and, where a real human value judgment is unavoidable, the
narrowest one-time decision that converts it into a standing, auto-applied
rule.

This document analyzes and proposes options. It does not itself change any
security posture, flag default, or live infrastructure state — every item
below still requires its own separate, reviewed change to take effect.

## Class 1 — live infrastructure mutation outside git

| Gap | Why it stopped | Mechanical fix |
|---|---|---|
| Arming `infra/cloudbuild/apply-trigger.yaml` | `gcloud builds triggers update --no-disabled` is declared "out-of-band, never a commit" by this repo's own doctrine — the live trigger's enabled bit is deliberately kept outside Terraform/git as a human veto point. | Bring trigger enablement into Terraform itself as a `google_cloudbuild_trigger` resource whose `disabled` field is git-declared, so the only path to arm it is a reviewed PR plus the same apply pipeline it gates. Add a plan-time safety check (refuse apply if the plan contains any `destroy` of a resource tagged `critical`) so an automated arm is provably safe before it ever runs unattended. |
| CloudBuild triggers (#1465) live-enabled while declared disabled | Same class: live GCP trigger state and git declaration can drift, and reconciling that drift today means a human runs `gcloud builds triggers update`. | Same fix as above — once trigger state lives in Terraform, `terraform plan` itself becomes the drift detector and `apply` the reconciler; no separate gcloud step ever needed again. |
| `terraform apply` against `purebliss-ghl` | `project_id` defaults to a literal placeholder (`example-control-plane`) specifically so a bare apply can never target the real project by accident. | Replace the placeholder default with a `data "google_client_config"` lookup (or an environment-sourced value validated against an allow-list of one real project id) so a correctly-authenticated CI identity always resolves the real project without a human typing it, while an unauthenticated/wrong-identity run still fails closed. |

## Class 2 — bulk/destructive action on a concurrently-active shared box

| Gap | Why it stopped | Mechanical fix |
|---|---|---|
| Orphan reclaim (55 worktrees / 123 branches / 63 issue-lanes over budget) | The reclaimer's remote-visibility check (`preserved_remotely`) was reading a stale local checkout and had already deleted real pushed work once (#1887) before this analysis was written. | Fixed same day (#1893): `preserved_remotely` now queries the remote live via `git ls-remote`, closing the exact race that made the classifier's concurrency concern justified. Remaining step: add a per-sweep lock so concurrent sessions can't race the same branch, plus an audit log so bulk reclaim can run unattended with after-the-fact review instead of before-the-fact approval. |

## Class 3 — needs a real value judgment (cost, product direction)

These cannot become purely mechanical without removing the judgment itself —
but each can be reduced to one standing decision that then auto-applies
forever, instead of a recurring interrupt.

| Gap | One-time decision that eliminates the recurring ask |
|---|---|
| `check-finops-kpi` ceiling (doc bytes over budget) | Replace the fixed byte ceiling with a self-adjusting budget: ceiling = trailing 30-day measured size + a pre-agreed headroom percentage, recomputed automatically each measurement. Removes the "bump the number" interrupt permanently; only alerts if growth accelerates past the trend. |
| Hermes hosting (#1560/#1561): self-host vs. Nous cloud | Declare one routing rule once (e.g. "self-host below N requests/day, cloud above") in `governance/dispatch/tier-policy.json`, and let `route.py` (landed 2026-09-21) auto-select per the measured volume. The only human input ever needed again is picking N. |
| Standards consolidation (#1890/#1891/#1892): which of 3-4 existing implementations becomes canonical | Declare a standing tie-break rule once (e.g. "the implementation in the repo tagged `hub` in CMR's topology wins; others become thin wrappers calling it") and let a mechanical pass apply it. Removes the case-by-case dedup decision for every future duplicate the indexer finds. |

## Class 4 — live process / daemon management

| Gap | Mechanical fix |
|---|---|
| Live fleet loop running stale code (#1790), checkout-behind remedy never invoked (#1795) | The self-heal mechanism already exists (`#780`'s bootstrap remedy) — the gap is only that nothing schedules it. Wire it into the existing fleet cron/watchdog schedule (`config/fleet-jobs.json`) so a stale checkout self-corrects on its own cadence; no one needs to notice and restart it by hand again. |

## Class 5 — tool-boundary, not doctrine

| Gap | Why it stopped | Path to close |
|---|---|---|
| `vendor/CMR` submodule pin bump (#1557) | The sandbox classifier refuses editing `vendor/` content directly as untrusted code integration — correctly, since a submodule pin bump pulls in code this repo hasn't reviewed line-by-line. | Not a repo gap — a supply-chain control working as intended. The mechanical closure is upstream: once the CMR-side PRs land (kushin77/CMR #1019/#1020/#1022/#1023/#1024/#1025), the pin bump becomes a diff of already-externally-reviewed, already-merged commits, which is the standard, safe shape for a dependency bump and can be automated with a bot that only bumps pins pointing at commits already merged to the upstream default branch. |

## What's left needing a person specifically

Only the live-infra items in Class 1 (arming the apply trigger, the real
project id) and the Class 3 one-time decisions (finops ceiling headroom
percentage, Hermes routing threshold N, standards tie-break rule) remain a
genuine decision point. Everything else in this table already has its
mechanical fix landed or roadmapped above with no further human step
required.
