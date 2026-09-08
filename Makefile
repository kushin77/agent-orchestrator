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
        shell-syntax yaml-lint json-lint docs-lint \
        secrets feature-flags cloudbuild terraform tf-fmt tf-validate \
        shellcheck gitleaks pre-commit

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
lint: shell-syntax yaml-lint json-lint docs-lint
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
