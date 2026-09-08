# Governed consumer repo — gate of record (issue #41)
#
# A tenant copies this template into its own repository to embed governed
# agents born-compliant: the agent pack is declared in
# .agent-orchestrator/pack.json, the governed instruction layers are
# auto-synced (make sync) and their parity is enforced by make verify.
#
#   make verify   born-compliance gate (pack manifest, sync config,
#                 instruction-layer parity — a real gate, can genuinely fail)
#   make sync     refresh the auto-synced instruction layers from the platform
#                 (--check offline by default; --fetch for a live refresh)
SHELL := /bin/bash

.PHONY: help verify sync
.DEFAULT_GOAL := help

## help — list targets
help:
	@echo "Governed consumer repo (agent-orchestrator)"
	@echo ""
	@echo "Targets:"
	@echo "  help    List targets"
	@echo "  verify  Born-compliance gate (pack + sync config + instruction parity)"
	@echo "  sync    Refresh auto-synced instruction layers (make sync ARGS=--fetch)"

## verify — born-compliance gate of record
verify:
	@bash .agent-orchestrator/scripts/verify.sh

## sync — refresh auto-synced instruction layers (offline check by default)
sync:
	@bash .agent-orchestrator/scripts/sync.sh $(ARGS)
