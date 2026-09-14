# Provenance — vendored `--os-` design-token twin (GR-10)

The lane's `seeds/tokens.json` is a byte-twin of the upstream design-token set,
and the repo-root `tokens.json` is that byte-twin unchanged (proven by
`tests/test_render.py` and by `scripts/check-shared-frontend-onboarding.sh`).

| Fact | Value |
| --- | --- |
| `harvested_from` | `kushin77/shared-frontend@shared/design-tokens/tokens.json` |
| Rev | `ace748f4` |
| Tag | `v0.2.0` |
| sha256 | `679ffaf89f6476832d663f3879aa4f90ebdce64021d0f20e0b11ffe0703fe188` |
| License | MIT (Copyright (c) 2026 kushin77) — retained verbatim in the file |
| Hub seed | `vendor/CMR/templates/frontend/shared/design-tokens/tokens.json` |
| Ledger | `canibalization/registry.csv` row `TB01` / issue `CMR-204` |
| Authority | `vendor/CMR/catalog/mandatory.tsv` row 3 (`shared-frontend`) |

## Rules

- **Re-sync by reviewed PR (NG2), never by hand.** A hand-edited token set is a
  drift, and the gate refuses it by name: the committed `tokens.json` must keep
  the pinned sha256 or the onboarding gate goes NOT-OK naming both digests.
- **Re-pin in two places together.** `render.py`'s `PINNED` constant and
  `template.yaml`'s `provenance:` block carry the SAME facts; the gate refuses a
  divergence by name, so the pin cannot be half-updated.
- **The twin is tenant-invariant.** Its bytes are the shared `--os-` namespace,
  grounded in the upstream source file and commit named in its own group headers;
  the tenant/org/domain parameters therefore bind into `gdc-manifest.yaml`
  (`repo:` + the `x-onboarding` block) rather than into the token set. That is
  why the twin's render is a byte-identity assertion, not a substitution.

## Upstream content note

The upstream `shared/design-tokens/README.md` documents the full source table,
the `--os-` namespace rule and the twin-file rule. The hub seed directory carries
the two data files plus an attribution stub; this lane carries the JSON twin and
this provenance record.
