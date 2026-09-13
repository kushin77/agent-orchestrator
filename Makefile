# agent-orchestrator — gate of record
#
# `make verify` is the honest composite gate (GR-12), orchestrated by
# scripts/verify.sh with attestation. Every check produces a REAL exit code
# and can genuinely fail: shell syntax errors, unparseable YAML/JSON, broken
# doc links, missing foundation files, unfinished markers, leaked secrets,
# feature flags that ship ON, cloudbuild triggers that ship enabled, or
# terraform fmt/validate failures all fail the gate. A gate that cannot fail
# is a formality and is rejected (fleet no-false-green doctrine).
#
# The gate writes .verify/verify.log + .verify/attestation.json (the verify
# record / attestation). Run `make verify` before every PR and every merge;
# paste its output as evidence (GR-12). No network and no containers required.

SHELL := /bin/bash

.PHONY: help verify lint gate merge-gate qa-loop tests \
        shell-syntax yaml-lint json-lint docs-lint chronological-dispatch \
        issue-claims issue-template fleet-channel knowledge-index knowledge-index-build \
        conformance secrets feature-flags cloudbuild terraform tf-fmt \
        tf-validate shellcheck gitleaks pre-commit

.DEFAULT_GOAL := help

## help — list targets with one-line descriptions
help:
	@echo "agent-orchestrator — gate of record"
	@echo ""
	@echo "Usage: make <target>"
	@echo ""
	@echo "Targets:"
	@echo "  help          List targets with one-line descriptions"
	@echo "  verify        Gate of record (with attestation); run before every PR/merge"
	@echo "  lint          Shell + YAML + JSON + docs (no secret scan)"
	@echo "  gate          Full QA gate: verify + policy-schema + guard"
	@echo "                negative-controls + per-suite tests + drift +"
	@echo "                merge-gate wiring (tri-state, attestation)"
	@echo "  merge-gate    Pre-merge contract: clean tree + verify + full"
	@echo "                per-suite tests + controls + policy-schema"
	@echo "  qa-loop       fix -> verify -> re-check until the gate is green"
	@echo "  tests         Run every declared pytest suite in isolation"
	@echo "  shellcheck    Run shellcheck on scripts/ (skipped if not installed)"
	@echo "  gitleaks      Run gitleaks with .gitleaks.toml (skipped if absent)"
	@echo "  pre-commit    Run pre-commit on all files (skipped if absent)"
	@echo ""
	@echo "Fine-grained checks (used by verify):"
	@echo "  shell-syntax  bash -n on every *.sh outside vendor/"
	@echo "  yaml-lint     Parse every .yml/.yaml outside vendor/ (PyYAML)"
	@echo "  json-lint     Validate every *.json outside vendor/"
	@echo "  docs-lint     Foundation files + md links + whitespace + markers"
	@echo "  chronological-dispatch  Governance docs declare dependency-ordered"
	@echo "                issue selection (GR-20, cannot drift to kanban picking)"
	@echo "  issue-claims  Claim-time order enforcement (#157): ledger replayed"
	@echo "                against the board snapshot + self-control mutants"
	@echo "  issue-template  Issue-brief contract (#165): the required fields +"
	@echo "                canonical FinOps vocab; a dropped field is refused"
	@echo "  fleet-channel Steering channel (M26 #162): message contract + the"
	@echo "                DSv4FNone standing directive, all mutants refused"
	@echo "  knowledge-index  Institutional knowledge index (#139): provenance,"
	@echo "                coverage and secret policy over the indexed assets"
	@echo "  conformance   CMR class/pattern/template enforcement (#140): every"
	@echo "                milestoned issue classified; mandates checked on the diff"
	@echo "  secrets       Mechanical secret scan (always on)"
	@echo "  feature-flags Feature-flag registry: every surface defaults OFF"
	@echo "  cloudbuild    infra/cloudbuild YAML parses; triggers ship disabled"
	@echo "  terraform     infra/terraform fmt + offline validate (SKIP if absent)"
	@echo "  tf-fmt        terraform fmt -check only"
	@echo "  tf-validate   offline terraform validate only"

## verify — gate of record (orchestrated by scripts/verify.sh, with attestation)
verify:
	@bash scripts/verify.sh verify

## lint — shell + YAML + JSON + docs (no secret scan)
lint: shell-syntax yaml-lint json-lint docs-lint chronological-dispatch issue-claims issue-template fleet-channel knowledge-index
	@echo ""
	@echo "lint: OK"

## gate — full QA gate (issue #29): verify composite + policy-schema + guard
## negative-controls + per-suite isolated tests + drift + merge-gate wiring.
## Honest tri-state aggregation (issue #28) + .verify/gate-attestation.json.
gate:
	@bash scripts/gate.sh gate

## merge-gate — pre-merge contract (issue #29): refuses on a dirty tree, runs
## verify + drift + every declared pytest suite in isolation + guard
## negative-controls + policy-schema, and writes .verify/merge-attestation.json.
merge-gate:
	@bash scripts/merge-gate.sh run

## qa-loop — continuous fix -> verify -> re-check until the gate is green
qa-loop:
	@bash scripts/qa-loop.sh

## tests — run every declared pytest suite in isolation (per-suite, issue #29)
tests:
	@bash scripts/run-pytest-suites.sh

## shell-syntax — bash -n on every *.sh outside vendor/
shell-syntax:
	@bash scripts/check-shell-syntax.sh

## yaml-lint — parse every .yml/.yaml outside vendor/ with PyYAML
yaml-lint:
	@python3 scripts/check-yaml.py

## json-lint — validate every *.json outside vendor/
json-lint:
	@bash scripts/check-json.sh

## docs-lint — foundation files, markdown links, whitespace, unfinished markers
docs-lint:
	@bash scripts/check-docs.sh

## chronological-dispatch — governance docs must declare dependency-ordered
## issue selection (GR-20); a doc-only rule is advisory, so this gate fails it
chronological-dispatch:
	@bash scripts/check-chronological-dispatch.sh

## issue-claims — claim-time order enforcement (issue #157): the ledger is
## replayed against the committed board snapshot and every violation fails; the
## audit always runs its own mutants, so it cannot pass vacuously
issue-claims:
	@bash scripts/check-issue-claims.sh

## issue-template — the fleet issue-brief contract (issue #165): every required
## field present and the FinOps vocabularies canonical; a dropped field fails
issue-template:
	@bash scripts/check-issue-template.sh

## fleet-channel — steering channel (M26, issue #162): the message contract and
## the DSv4FNone standing directive; every mutant message must be refused
fleet-channel:
	@bash scripts/check-fleet-channel.sh

## knowledge-index — the institutional knowledge index must be valid (issue #139):
## every item's provenance complete, secret policy clean, mandatory kinds covered
knowledge-index:
	@bash scripts/check-knowledge-index.sh

## knowledge-index-build — regenerate the catalogue + report on demand (the ops
## runner invokes the same command on its schedule)
knowledge-index-build:
	@python3 governance/knowledge/cli.py build

## conformance — CMR class/pattern/template enforcement (issue #140): every
## milestoned issue must be classified, and the class it declares must hold
conformance:
	@bash scripts/check-conformance.sh

## conformance-change-set — check the current diff against the mandates
## (GR-15 code-native automation; new infrastructure ships flag-gated OFF)
conformance-change-set:
	@python3 governance/conformance/cli.py change-set

## secrets — mechanical secret scan (always on, no external tool dependency)
secrets:
	@bash scripts/check-secrets.sh

## feature-flags — feature-flag registry: every surface defaults OFF (issue #6)
feature-flags:
	@python3 scripts/check-feature-flags.py

## cloudbuild — infra/cloudbuild YAML parses; every trigger ships disabled (issue #6)
cloudbuild:
	@bash scripts/check-cloudbuild.sh

## terraform — infra/terraform fmt + offline validate (issue #6; SKIP if absent)
terraform:
	@bash scripts/check-terraform.sh all

## tf-fmt — terraform fmt -check -recursive on infra/terraform
## (visible SKIP if the terraform binary is not installed)
tf-fmt:
	@bash scripts/check-terraform.sh fmt

## tf-validate — offline terraform validate of infra/terraform
## (visible SKIP if terraform is absent or no local provider cache exists)
tf-validate:
	@bash scripts/check-terraform.sh validate

## shellcheck — optional lint (not part of verify; skipped if not installed)
shellcheck:
	@if command -v shellcheck >/dev/null 2>&1; then \
		shellcheck scripts/*.sh; \
	else \
		echo "shellcheck: not installed (skipped)"; \
	fi

## gitleaks — optional secrets scan with .gitleaks.toml (not part of verify)
gitleaks:
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks detect --no-git --source . -c .gitleaks.toml --redact; \
	else \
		echo "gitleaks: not installed (skipped)"; \
	fi

## pre-commit — optional, runs all pre-commit hooks (not part of verify)
pre-commit:
	@if command -v pre-commit >/dev/null 2>&1; then \
		pre-commit run --all-files; \
	else \
		echo "pre-commit: not installed (skipped)"; \
	fi
