#!/usr/bin/env bash
# Gate orchestrator + attestation for `make verify` / `make gate` (GR-12).
#
# Runs every check in order, tees the full transcript to .verify/verify.log,
# writes .verify/attestation.json (timestamp, host, git sha, branch,
# per-check results, overall exit code) and exits with the REAL aggregate exit
# code. A check that cannot fail is a formality and is rejected
# (no-false-green doctrine). No network and no containers are required.
#
# Tri-state, honest (GR-12): each check's exit code is 0 = PASS, 1 = NOT-OK (a
# real defect -- the run goes red), 2 = CANNOT-ASSESS. A check that says it
# genuinely could not assess is recorded as SKIP -- never a pass and never a
# failure -- and is named in the summary and in the attestation, so a skip can
# never hide. Only a definite NOT-OK (or an unexpected code) fails the run.
#
# The check set includes the declared `fleet` pytest suite (`pytest-fleet`) and
# the declared capstone `e2e` suite (`e2e`).
# The gate of record must exercise the tests it claims to cover: a red fleet
# suite sat on master undetected because this gate ran no pytest at all — only
# `make gate` / `make tests` did (gate gap, issue #331). The capstone E2E suite
# had the same gap: it was declared in scripts/pytest-suites.txt, but only `make
# gate` / `make tests` ran it, so the EPIC-00 Definition-of-Done proof could be
# skipped outright (issue #525). The remaining declared suites are still
# exercised only by `make gate`; `governance/lessons` is red for an unrelated
# board-hygiene defect (#312).
#
# Usage: scripts/verify.sh [verify|gate]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

mode="${1:-verify}"

verify_dir="$root/.verify"
mkdir -p "$verify_dir"
log="$verify_dir/verify.log"
results_tsv="$verify_dir/.results.tsv"
: > "$log"
: > "$results_tsv"

# name|command — every command is an honest gate (real exit code, can fail).
checks=(
  'shell-syntax|bash scripts/check-shell-syntax.sh'
  'python-syntax|bash scripts/check-python-syntax.sh'
  'yaml-lint|python3 scripts/check-yaml.py'
  'json-lint|bash scripts/check-json.sh'
  'docs-lint|bash scripts/check-docs.sh'
  # gate-coverage (issue #526, RCA of EPIC #524): the gate registry was not
  # self-checking. A new `scripts/check-*.sh` that nobody registers is inert,
  # and a suite declared in scripts/pytest-suites.txt that no gate names is only
  # reached by the manifest sweep -- so coverage was opt-in and nothing
  # complained. This check names BOTH classes of ungated artifact and fails on
  # any that is not in a live, reasoned baseline entry
  # (scripts/gate-coverage-baseline.txt); the baseline is checked in both
  # directions, so a stale entry (its artifact is wired now, or gone) fails too
  # and the list cannot rot into a fiction. It is registered here deliberately:
  # this array is explicit, so the detector is itself inert until it is named.
  'gate-coverage|bash scripts/check-gate-coverage.sh'
  'chronological-dispatch|bash scripts/check-chronological-dispatch.sh'
  'issue-claims|bash scripts/check-issue-claims.sh'
  'fleet-channel|bash scripts/check-fleet-channel.sh'
  'fleet-contract|bash scripts/check-fleet-contract.sh'
  'fleet-runbook|bash scripts/check-fleet-runbook.sh'
  'session-isolation|bash scripts/check-session-isolation.sh'
  'github-lifecycle|bash scripts/check-github-lifecycle.sh'
  'reconcile|bash scripts/check-reconcile.sh'
  'lease-policy|bash scripts/check-lease-policy.sh'
  'fleet-state|bash scripts/check-fleet-state.sh'
  'paperclip-gap-analysis|bash scripts/check-paperclip-gap-analysis.sh'
  'brain-profile|bash scripts/check-brain-profile.sh'
  'knowledge-index|bash scripts/check-knowledge-index.sh'
  'cross-reference|bash scripts/check-cross-reference.sh'
  'conformance|bash scripts/check-conformance.sh'
  'surface-class|bash scripts/check-surface-class.sh'
  'remediation|bash scripts/check-remediation.sh'
  'board-gate|bash scripts/check-board-gate.sh'
  'cross-repo-boundary|bash scripts/check-cross-repo-boundary.sh'
  # audit-read-model (issue #347): the tamper-evident trail served as a
  # read-only, deterministic, filterable read model; verify-chain is OK on an
  # intact chain and refuses a modified / reordered / removed record, and the
  # check mutates a temp copy of the chain, so it cannot pass vacuously.
  'audit-read-model|bash scripts/check-audit-read-model.sh'
  'gateway-catalog-parity|bash scripts/check-gateway-catalog-parity.sh'
  'guardrail-controls|bash scripts/check-guardrail-controls.sh'
  # agent-identity-parity (issue #346): the shared agent-identity schema's
  # closed vocabularies must equal agent-profile.schema.json + catalog.yaml and
  # every seed must validate as a projected identity; the check mutates its own
  # input, so it cannot pass vacuously.
  'agent-identity-parity|bash scripts/check-agent-identity-parity.sh'
  'paperclip-adapter|bash scripts/check-paperclip-integration-adapter.sh'
  'paperclip-canonical-module|bash scripts/check-paperclip-canonical-module.sh'
  # paperclip-auth (issue #412, ADR-0013): the cross-boundary auth seam in
  # integrations/paperclip/auth/ must mint/verify agent identity from the
  # registry, map a human onto the board session path, bridge the run id, and
  # refuse every negative control by name (incl. 403-not-404); the check runs
  # the boundary suite and provokes each refusal, so it cannot pass vacuously.
  'paperclip-auth|bash scripts/check-paperclip-auth.sh'
  # EPIC-410 paperclip parity adapters (issue #420, the wiring lane): the
  # adapter gates below are registered here deliberately — a check that only
  # runs when someone remembers to run it is a formality, not a gate. Each
  # carries its own self-mutating negative control (a scratch-copy mutant the
  # gate must refuse BY NAME, so it cannot pass vacuously) and each subject
  # adapter's pytest suite is declared in scripts/pytest-suites.txt so `make
  # gate` runs it in isolation.
  'paperclip-approvals|bash scripts/check-paperclip-approvals.sh'
  'paperclip-deploy|bash scripts/check-paperclip-deploy.sh'
  'paperclip-heartbeat|bash scripts/check-paperclip-heartbeat.sh'
  'paperclip-openapi|bash scripts/check-paperclip-openapi.sh'
  'paperclip-secrets|bash scripts/check-paperclip-secrets.sh'
  'paperclip-skills|bash scripts/check-paperclip-skills.sh'
  # paperclip-diagrams (issue #465, completing #420): the diagrams blueprint
  # projection adapter landed before #420's wiring branch was cut and was still
  # not registered when the wiring lane ran, so it was missed twice. It has the
  # same self-mutating negative-control shape; its tests live inside the already
  # declared integrations/paperclip suite (integrations/paperclip/tests).
  'paperclip-diagrams|bash scripts/check-paperclip-diagrams.sh'
  # paperclip-routines (issue #418, completing #420): the routines adapter
  # landed AFTER #420's branch was cut, so the wiring lane could not register
  # it then. It is the same shape as its siblings; its suite is declared in
  # scripts/pytest-suites.txt (integrations/paperclip/adapters/routines).
  'paperclip-routines|bash scripts/check-paperclip-routines.sh'
  'issue-template|bash scripts/check-issue-template.sh'
  'finops-chooser|bash scripts/check-finops-chooser.sh'
  'lessons|bash scripts/check-lessons.sh'
  'paperclip-integration|bash scripts/check-paperclip-integration.sh'
  'ticket-projection|bash scripts/check-ticket-projection.sh'
  # pmo-rollup (issue #403): the PMO views are derived queries over the ticket
  # graph, never a store — no ledger, no surviving cache, no second source of
  # status; the gate provokes an owner-less risk, a RAID set that disagrees with
  # the graph and a rollup from a stale cache, and proves nothing is written.
  'pmo-rollup|bash scripts/check-pmo-rollup.sh'
  'secrets|bash scripts/check-secrets.sh'
  'feature-flags|python3 scripts/check-feature-flags.py'
  'cloudbuild|bash scripts/check-cloudbuild.sh'
  'terraform|bash scripts/check-terraform.sh'
  # EPIC #144 (per-repo agent fleet) governance surfaces — each is tri-state
  # (0 OK / 1 NOT-OK / 2 CANNOT-ASSESS) and proves its own negative control.
  'cto-overlay|bash scripts/check-cto-overlay.sh'
  'authority|bash scripts/check-authority.sh'
  'rollup|bash scripts/check-rollup.sh'
  'fleet-template|bash scripts/check-fleet-template.sh'
  'sme-routing|bash scripts/check-sme-routing.sh'
  'registry-parity|bash scripts/check-registry-parity.sh'
  # EPIC #422 (cross-repo integration gaps) — each tri-state, each proving its
  # own negative control.
  'module-admission|bash scripts/check-module-admission.sh'
  'routing-seam|bash scripts/check-routing-seam.sh'
  'cross-repo-sync|bash scripts/check-cross-repo-sync.sh'
  'cross-repo-lessons|bash scripts/check-cross-repo-lessons.sh'
  'paperclip-budget|bash scripts/check-paperclip-budget.sh'
  'metering-parity|bash scripts/check-metering-parity.sh'
  # EPIC #461 (the diagrams chain): diagrams-declaration (#464) proves the
  # architecture.yaml / gdc-manifest.yaml seeds conform to the vendored CMR
  # contract. (Its sibling, the #465 ADR-0017 projection gate
  # `paperclip-diagrams`, is already wired above with its #420 siblings.) This
  # check is tri-state: it is CANNOT-ASSESS (rc 2, visibly SKIPped) until
  # `git submodule update --init vendor/CMR` has run -- the normal state of a
  # fresh worktree -- while a genuinely wrong declaration (rc 1) still fails.
  'diagrams-declaration|bash scripts/check-diagrams-declaration.sh'
  # EPIC #472 (the codeidx consumption chain): the three gates that keep the
  # consumed indexer surface from silently rotting. `codeidx-surface` (#475)
  # proves the root .mcp.json carries the indexer server entry in the vendored
  # seed's shape AND that the root gdc-manifest.yaml still declares the
  # `code-indexing.mcp` pin; `codeidx-backend` (#476) proves the real indexer
  # backend is flag-gated OFF, labelled per path, and that an unreachable indexer
  # degrades explicitly instead of inventing results; `context-pack-consumption`
  # (#477) proves assemble_prefix consumes a pre-fetched codeidx pack as opaque
  # bytes from the published contract and leaves the absent-pack bytes exactly as
  # they were. `codeidx-surface` derives its expectation from the vendored seed,
  # so like its #461 sibling above it is CANNOT-ASSESS (rc 2, visibly SKIPped)
  # until `git submodule update --init vendor/CMR` has run -- the normal state of
  # a fresh worktree -- while a missing/drifted .mcp.json or a dropped pin (rc 1)
  # still fails.
  'codeidx-surface|bash scripts/check-codeidx-surface.sh'
  'codeidx-backend|bash scripts/check-codeidx-backend.sh'
  'context-pack-consumption|bash scripts/check-context-pack-consumption.sh'
  # e2e (issue #525): the capstone end-to-end suite. e2e/README.md calls this
  # subtree the gate of the whole product build — it proves the EPIC-00
  # Definition of Done (signup -> org -> personas -> agents -> routed model
  # calls -> audit + usage billing, across all six providers, with 10 real
  # negative controls in e2e/negative_controls.py). It was declared in
  # scripts/pytest-suites.txt but ran only under `make gate` / `make tests`,
  # which nothing enforces, so the DoD proof could be skipped and a broken DoD
  # could reach master. It is wired as an ordinary PASS/FAIL check — a real
  # failure is rc 1, never a SKIP — because the suite is offline, deterministic
  # and green, so the CANNOT-ASSESS third state does not apply.
  # PYTHONDONTWRITEBYTECODE + `-p no:cacheprovider` keep the gate clear of the
  # __pycache__ state that can make a later run read a stale module instead of
  # the tree under test.
  'e2e|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q e2e/tests'
  # EPIC #494 (the monitoring program; issue #499 is the wiring lane): the
  # declaration gate (#496, ADR-0022) proves BOTH halves of the monitoring
  # declaration are real -- `module.json` carries exactly one flat
  # `{ "id": "prometheus", "type": "monitoring" }` integration (the shape
  # ADR-0022 D3 froze, with no invented pin key) AND `docs/OBSERVABILITY.md`
  # names the producer/consumer boundary (producer SSOT, Prometheus-plane
  # owner, capability tie-back, OTLP/HTTP push, the signal-to-ticket rule, the
  # DIFFERENT human-surface gap and the exposition lane). The check stages a
  # deliberately damaged scratch copy of each declared file and REQUIRES it to
  # be refused by name, so it cannot pass vacuously. It is registered here
  # deliberately: this array is explicit, so a new scripts/check-*.sh is never
  # auto-discovered and an unwired gate is a formality, not a gate.
  'monitoring-declaration|bash scripts/check-monitoring-declaration.sh'
  # EPIC #462 (diagrams) / EPIC #473 (codeidx) -- the capability registers: the
  # four enforcement checks delivered by #468/#469 and #479/#480, registered here
  # because this array is explicit and nothing is auto-discovered, so an unwired
  # check enforces nothing (GR-12). `diagrams-capability-register` (#469) and
  # `codeidx-capability-register` (#480) enforce each register's own row grammar
  # (header, cell count, status/owner vocabularies, ref shape) and provoke their
  # negative controls; `diagrams-capability-tracker` (#468) and
  # `codeidx-capability-tracker` (#479) reconcile every row's `ref` against the
  # RECORDED board snapshot, so a row that drifted from the board is surfaced by
  # name instead of rotting. Unlike their `diagrams-declaration` (#464) and
  # `codeidx-surface` (#475) siblings above, these four are offline and
  # deterministic -- no network, no `vendor/CMR` submodule and no vendored seed
  # -- so they RUN for real (rc 0) in a fresh worktree and are never recorded as
  # CANNOT-ASSESS.
  'diagrams-capability-register|bash scripts/check-diagrams-capability-register.sh'
  'diagrams-capability-tracker|bash scripts/track-diagrams-capabilities.sh'
  'codeidx-capability-register|bash scripts/check-codeidx-capability-register.sh'
  'codeidx-capability-tracker|bash scripts/track-codeidx-capabilities.sh'
  # EPIC #500 (M30 — the enterprise chat surface in the Single Pane of Glass):
  # the six conversational-surface gates delivered by the sibling chat lanes.
  # They are registered here because this array is explicit and nothing is
  # auto-discovered, so a `scripts/check-*.sh` that is not named here is inert —
  # it runs only if someone remembers to run it, which is the formality GR-12
  # forbids. All six are offline and deterministic (no network, no `vendor/CMR`
  # submodule, no vendored seed), so unlike the `diagrams-declaration` /
  # `codeidx-surface` gates above they RUN for real (rc 0) in a fresh worktree
  # and are never recorded as CANNOT-ASSESS. Each also provokes its own negative
  # controls, so a green result cannot come from a gate that exercises nothing.
  # NB: the seventh gate named by issue #502, `check-chat-surface.sh`, was NOT
  # registered when this lane was written because it did not exist yet — it is
  # owned by #503, which has since landed (PR #564). It is registered directly
  # below by issue #568, which wired it and repaired the red gate of record its
  # landing caused. Naming that gap was honest; a stub would have been an
  # invented check, which is worse than a named gap.
  'chat-tools|bash scripts/check-chat-tools.sh'            # #504 grounding lane
  'chat-identity|bash scripts/check-chat-identity.sh'      # #505 identity lane
  'chat-finops|bash scripts/check-chat-finops.sh'          # #506 FinOps lane
  'chat-guardrails|bash scripts/check-chat-guardrails.sh'  # #507 guardrails lane
  'chat-ux|bash scripts/check-chat-ux.sh'                  # #508 UX lane
  'chat-eval|bash scripts/check-chat-eval.sh'              # #509 eval lane
  # EPIC #500 (the enterprise chat surface), issue #568: the seventh chat gate.
  # #503 (PR #564) shipped `scripts/check-chat-surface.sh` -- the OpenAI-/
  # Ollama-compatible contract and its flag gate -- but nothing invoked it, so
  # the `gate-coverage` detector (#526) failed it BY NAME and the gate of record
  # went red on master: an artifact no gate runs is a formality (GR-12). Its six
  # siblings (`chat-eval`, `chat-finops`, `chat-guardrails`, `chat-identity`,
  # `chat-tools`, `chat-ux`) are wired by the #502 lane; this one landed after
  # that lane's PR was opened, which is why it needed its own fix. Offline and
  # deterministic -- no network, no `vendor/CMR` seed -- so it RUNS for real.
  'chat-surface|bash scripts/check-chat-surface.sh'
  # The declared suite manifest (scripts/pytest-suites.txt) is run in full and in
  # isolation by `make gate` / `make tests`; this gate runs the `fleet` suite the
  # same way run-pytest-suites.sh does, so a red fleet test cannot reach master
  # again. pytest exits non-zero on a collection error or on "no tests collected"
  # (rc 5), so the check has no false-green path.
  'pytest-fleet|env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q fleet/tests'
)

# --- duplicate-registration guard (issue #499) -------------------------------
# `checks=()` is an explicit list that every wiring lane appends to, so two
# lanes can register the SAME name (measured on this board: a re-added
# `cross-reference` entry). A duplicate is a gate defect, not a harmless no-op:
# it re-runs a check and hides that two lanes claim the same surface. The
# duplicates are computed and named BEFORE anything runs, recorded in the
# attestation as `duplicates` ({} when the list is clean) and fail the run, so a
# wedged check list can never attest green.
duplicates_tsv="$verify_dir/.duplicates.tsv"
: > "$duplicates_tsv"
for entry in "${checks[@]}"; do
  printf '%s\n' "${entry%%|*}"
done | LC_ALL=C sort | uniq -c | awk '$1 > 1 {print $2"\t"$1}' > "$duplicates_tsv"

overall=0
if [ -s "$duplicates_tsv" ]; then
  while IFS=$'\t' read -r dup_name dup_count; do
    printf '  FAIL  check name %s is registered %s times (one writer per wave on this list)\n' \
      "$dup_name" "$dup_count" >&2
  done < "$duplicates_tsv"
  echo "verify: duplicate check name(s) registered -- the check list is wedged" >&2
  overall=1
fi

for entry in "${checks[@]}"; do
  name="${entry%%|*}"
  cmd="${entry#*|}"
  printf '\n== %s ==\n' "$name" | tee -a "$log"
  bash -c "$cmd" 2>&1 | tee -a "$log"
  rc="${PIPESTATUS[0]}"
  printf '%s\t%s\n' "$name" "$rc" >> "$results_tsv"
  # Honest tri-state (GR-12 / guardrails/honesty). 0 = PASS; 1 = NOT-OK, a real
  # defect, and the run fails; 2 = CANNOT-ASSESS -> SKIP. A check that says it
  # genuinely could not assess (e.g. the pinned vendor/CMR submodule is absent in
  # a fresh worktree, so the vendored contract is unreadable) must not paint the
  # whole gate red for every lane AND must not be counted as a pass: it is
  # recorded as SKIP and named in the summary and the attestation, so a skip can
  # never hide. Any other rc (unexpected, timeout 124, killed 137) fails.
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 2 ]; then
    overall=1
  fi
done

# --- attestation ------------------------------------------------------------
export ATTEST_DIR="$verify_dir"
export ATTEST_TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
export ATTEST_SHA="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
export ATTEST_BRANCH="$(git branch --show-current 2>/dev/null || echo unknown)"
export ATTEST_HOST="$(hostname 2>/dev/null || echo unknown)"
export ATTEST_RESULT="$overall"
export ATTEST_MODE="$mode"
export ATTEST_RESULTS_TSV="$results_tsv"
export ATTEST_DUPLICATES_TSV="$duplicates_tsv"
python3 - <<'PY'
import json, os

attest_dir = os.environ["ATTEST_DIR"]
results_tsv = os.environ["ATTEST_RESULTS_TSV"]
duplicates_tsv = os.environ["ATTEST_DUPLICATES_TSV"]

checks = []
with open(results_tsv, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        name, rc = line.split("\t", 1)
        status = "PASS" if rc == "0" else ("SKIP" if rc == "2" else "FAIL")
        checks.append({"name": name, "rc": int(rc), "status": status})

skipped = [c["name"] for c in checks if c["status"] == "SKIP"]

# Duplicate check names: a name registered twice is a gate defect (two lanes
# wrote the same surface), so it is recorded here verbatim -- {} when clean.
duplicates: dict[str, int] = {}
with open(duplicates_tsv, encoding="utf-8") as fh:
    for line in fh:
        line = line.rstrip("\n")
        if not line:
            continue
        dup_name, dup_count = line.split("\t", 1)
        duplicates[dup_name] = int(dup_count)

overall = int(os.environ["ATTEST_RESULT"])
attestation = {
    "gate": "verify",
    "mode": os.environ["ATTEST_MODE"],
    "result": "PASS" if overall == 0 else "FAIL",
    "exit_code": overall,
    "timestamp": os.environ["ATTEST_TS"],
    "host": os.environ["ATTEST_HOST"],
    "git_sha": os.environ["ATTEST_SHA"],
    "branch": os.environ["ATTEST_BRANCH"],
    "check_count": len(checks),
    "skipped": len(skipped),
    "skipped_checks": skipped,
    "duplicates": duplicates,
    "checks": checks,
}
path = os.path.join(attest_dir, "attestation.json")
with open(path, "w", encoding="utf-8") as fh:
    json.dump(attestation, fh, indent=2)
    fh.write("\n")
PY

# --- summary ----------------------------------------------------------------
# A SKIP is counted and named here so it can never hide (a skip is not a pass).
passed=0
failed=0
skipped=0
skipped_names=""
total=0
for entry in "${checks[@]}"; do
  name="${entry%%|*}"
  total=$((total + 1))
  rc="$(awk -F'\t' -v n="$name" '$1==n {print $2}' "$results_tsv" | head -1)"
  case "${rc:-1}" in
    0) passed=$((passed + 1)) ;;
    2) skipped=$((skipped + 1)); skipped_names="${skipped_names}${skipped_names:+, }${name}" ;;
    *) failed=$((failed + 1)) ;;
  esac
done

skip_note=""
if [ "$skipped" -gt 0 ]; then
  skip_note=", $skipped skipped: $skipped_names"
fi

if [ -s "$duplicates_tsv" ]; then
  echo "verify: duplicate check name(s): $(awk -F'\t' '{printf "%s%s", sep, $1; sep=", "}' "$duplicates_tsv")" >&2
fi

echo ""
if [ "$overall" -eq 0 ]; then
  echo "verify: PASS ($passed of $total checks$skip_note)"
  echo "attestation: $verify_dir/attestation.json"
  if [ "$mode" = "gate" ]; then
    echo "GATE: PASS"
  fi
else
  echo "verify: FAIL ($failed of $total checks failed$skip_note)" >&2
  echo "attestation: $verify_dir/attestation.json" >&2
  if [ "$mode" = "gate" ]; then
    echo "GATE: FAIL" >&2
  fi
fi

exit "$overall"
