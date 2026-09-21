#!/usr/bin/env bash
# check-pytest-suites.sh — run the declared pytest suites that no OTHER gate names (#1497).
#
# THE DEFECT THIS EXISTS FOR
#   `scripts/pytest-suites.txt` declares the repository's per-suite corpus: 99
#   suites. `scripts/check-gate-coverage.sh` measures, honestly, which of them a
#   gate actually NAMES as a pytest target — and 51 of the 99 were named by
#   nobody. They ran only because `scripts/run-pytest-suites.sh` sweeps the
#   manifest, and that sweep is reached by `make gate`, NOT by the gate of
#   record — so 51 declared suites ran in no `make verify` at all. The coverage
#   detector recorded that as 51 `swept-only` rows in
#   `scripts/gate-coverage-baseline.txt`, each deferred to an epic that had
#   already CLOSED (measured 2026-09-19: #524 -- 46 rows, #502 -- 3, #445, #447,
#   all closed; the deferral lane the baseline's own header named, #559, closed
#   2026-09-14). A row deferred to a closed issue is a permanent excuse, and the
#   detector could not say so because its closed-tracker rule covered only
#   `script` rows. Both halves are retired by #1497: the rule now covers every
#   live row, and 42 of those 51 rows are GONE because their suites run here —
#   naming a suite makes its row STALE, which is a red gate. #1497 retired 39 of
#   them on 2026-09-19; #1509 retired the last THREE on 2026-09-20, each once its
#   venue cause had been measured in that venue and repaired rather than excused
#   (see "WHAT IS NOT IN THIS LIST", below). The nine rows that remain are listed
#   below, each owned by an OPEN issue; this check does not claim them, and
#   `check-gate-coverage.sh` still reports them.
#
# WHAT IS MEASURED
#   Every suite named below is run HERE, each in ISOLATION (its own pytest
#   process), exactly as `scripts/run-pytest-suites.sh` runs the manifest:
#     * a suite is named LITERALLY on the pytest line that runs it, never
#       through a loop variable. `scripts/check-gate-coverage.sh` reads a suite
#       as covered when a gate file (or a check it invokes) NAMES it as a pytest
#       target, so a `"$suite/tests"` loop would leave every suite looking
#       ungated while this check ran it — the declaration and the run would
#       disagree, which is the exact class of defect this lane exists to close.
#       The literal repetition is the mechanism, not an accident of style;
#     * the suites cannot share one pytest invocation: sibling suites collide on
#       conftest `sys.path` bootstrap and duplicate test-module basenames
#       (`test_budget.py`, `test_events.py`, ...), so a combined run errors
#       during collection;
#     * each suite's transcript lands in `.verify/pytest-suites/<suite>.log`
#       (git-ignored, so the gate cannot dirty the tree it judges) and a failure
#       names its log.
#
#   `PYTHONDONTWRITEBYTECODE=1` + `-p no:cacheprovider` keep the run clear of the
#   `__pycache__` state that can make a later run read a stale module instead of
#   the tree under test.
#
#   A suite that exceeds `SUITE_TIMEOUT` (default 300s) is a FAILURE, never a
#   skip: the tri-state reserves CANNOT-ASSESS for a venue the check cannot
#   judge, and "no verdict" recorded as a small believable skip is how a blind
#   check gets quoted as part of a green board. A timeout is named as the
#   finding it is.
#
# WHY THE SUITE LIST IS AN ASSERTION
#   `LISTED` is the number of suites named below, and the check FAILS unless
#   exactly that many ran. A truncated or half-edited list therefore cannot pass
#   quietly, and a suite REMOVED from the list is caught in the other direction
#   by `scripts/check-gate-coverage.sh`, which refuses a declared suite that no
#   gate names. The two halves keep the list equal to "declared AND unreached
#   elsewhere" without either of them reading the other's state.
#
# WHAT IS NOT IN THIS LIST, AND WHY
#   NONE. #1737 wired the last excluded suite (portal) in below; every
#   declared suite named by no other gate now runs here.
#
#   #1501 (CLOSED) measured NINE suites failing, hanging, or unable to run in a
#   lane venue on origin/master, each ALONE in a worktree, its own pytest
#   process, nothing else running. FOUR were suite-test defects
#   (module-identity / stale-clock / stale-expectation bugs, never product
#   bugs), fixed and named above by #1501 itself: governance/conformance
#   (1 failed, now 128 passed), governance/ticket (1 failed, now 69 passed),
#   governance/cto-overlay (4 failed, now 70 passed), governance/authority
#   (2 failed, now 137 passed). FOUR MORE were fixed and named above by #1725:
#   each one's real root cause was a `git worktree add` checkout leaving
#   `vendor/CMR` as an empty gitlink placeholder — `.is_dir()` on it reads
#   true, so a guard written against that check never fired — plus, for
#   `scripts`, two tests that judge this shared box's own live state rather
#   than the code under test. See `scripts/gate-coverage-baseline.txt` for the
#   per-suite before/after counts and the exact fix in each:
#     scripts                           18 failed, 32 passed -> 46 passed,
#                                        20 skipped (AO_GATE_VENUE gating)
#     governance/knowledge              4 failed, 65 passed -> 63 passed,
#                                        6 skipped (validator-path skip guard)
#     governance/modules                3 errors -> 119 passed, 5 skipped
#                                        (catalog-dir skipif)
#     integrations/paperclip/reporting  7 failed, 18 passed, 57 errors ->
#                                        18 passed, 64 skipped (catalog-dir
#                                        skip guard)
#   ONE MORE WAS FIXED AND NAMED ABOVE BY #1737: `portal` measured NOT hanging
#   (579 passed, 8 failed, 164s with pytest-timeout available); the 8 failures
#   were three `infra/feature-flags/registry.yaml` `surfaces:` entries
#   (fleet_projection, remote_control, operator_terminal) shipping
#   `default: on` in violation of GR-5. Flipped to `off`; portal is now
#   579 -> 587 passed, 0 failed.
#
#   THREE MORE WERE EXCLUDED, AND ARE NAMED AGAIN (#1509). They are the rows
#   #1497 baselined because they failed in the CI venue (Cloud Build python:3.14,
#   build 6845d8ad) while passing alone in a lane worktree. Each cause was then
#   MEASURED IN THE VENUE ITSELF — `python:3.14` at `/workspace`, uid 0,
#   LANG=C.UTF-8, deps where `infra/cloudbuild/requirements-verify.txt` puts them —
#   and repaired wherever a repair was the honest fix:
#     guardrails/honesty     one arm could not establish its own PRE-CONDITION.
#         test_unreadable_file_is_never_clean makes a mode-000 file and asserts the
#         analyzer surfaces it — but uid 0 READS a mode-000 file, so the file was
#         never unreadable and the arm reported NOT-OK about a state it never
#         created. It now skips BY NAME there — "running as root; permissions do
#         not block reads", the remedy telemetry/ledger's
#         test_unreadable_file_is_cannot_assess already carries for the same
#         pre-condition — and still measures in every non-root run, i.e. every lane
#         and every developer.
#     governance/reconcile   test_the_shipped_document_re_measures_its_own_rows
#         asserted this checkout IS the repository instance the shipped quarantine
#         document names (/home/akushnir/agent-orchestrator/.git). A foreign
#         instance is a DESIGNED state, not a defect: `check_real_tree` reports
#         every entry of a document that is not in force as inapplicable, honours
#         nothing, and treats none of it as fatal (#1321). The arm therefore failed
#         in every venue but the one machine the rows were measured on, and a
#         pristine clone — exactly what the CI venue checks out — can never be that
#         machine. It now asserts the OUT-OF-FORCE contract instead, against the
#         SHIPPED document, by name: the same subject, satisfiable in any venue.
#     governance/lifecycle   no repair: the suite is green in the venue itself (258
#         passed, 1 skipped, its own named arm). Its five apparent CI failures were
#         the MEASURING HARNESS's, not the suite's — a child interpreter that
#         overwrites PYTHONPATH and so lost PyYAML; #1509 carries the first run and
#         the corrected one on the record.
#
#   Naming one of them here would red the gate of record for every lane — the
#   one outcome worse than the honest deferral, because a gate that is red on
#   master is ignored. A baseline row naming an OPEN issue is owned instead, and
#   the row is refused the moment that issue closes.
#
# EXIT CONTRACT (guardrails/honesty tri-state, consumed not redefined)
#   0  OK              every named suite ran and passed
#   1  NOT-OK          a suite failed, timed out, or its `tests/` directory is
#                      missing; or fewer/more suites ran than are named
#   2  CANNOT-ASSESS   python3 is unavailable, or this is not a git work tree
#
# Offline, deterministic, no network, no containers.
#
# Usage: bash scripts/check-pytest-suites.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-pytest-suites: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "check-pytest-suites: CANNOT-ASSESS — not inside a git work tree, so the suites would be read from a peer lane's scratch copy" >&2
  exit 2
fi

suite_timeout="${SUITE_TIMEOUT:-300}"
log_dir=".verify/pytest-suites"
mkdir -p "$log_dir"
if [ ! -d "$log_dir" ]; then
  echo "check-pytest-suites: CANNOT-ASSESS — cannot create the log directory $log_dir" >&2
  exit 2
fi

LISTED=51
ran=0
failed=0

suite_log() { # <suite> -> the transcript path for that suite
  printf '%s/%s.log' "$log_dir" "$(printf '%s' "$1" | tr '/' '_')"
}

judge() { # <suite> <rc> — the pytest line above it is the one that produced <rc>
  local suite="$1" rc="$2"
  ran=$((ran + 1))
  if [ "$rc" -eq 0 ]; then
    printf '  PASS  %s\n' "$suite"
    return 0
  fi
  failed=$((failed + 1))
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    printf '  FAIL  %s (no verdict: exceeded SUITE_TIMEOUT=%ss — %s)\n' \
      "$suite" "$suite_timeout" "$(suite_log "$suite")" >&2
  else
    printf '  FAIL  %s (pytest exit %s — %s)\n' \
      "$suite" "$rc" "$(suite_log "$suite")" >&2
  fi
  return 0
}

echo "== declared suites no other gate names (each run in isolation) =="

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q guardrails/dlp/tests > "$(suite_log guardrails/dlp)" 2>&1
judge guardrails/dlp $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q guardrails/policy/tests > "$(suite_log guardrails/policy)" 2>&1
judge guardrails/policy $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q guardrails/controls/tests > "$(suite_log guardrails/controls)" 2>&1
judge guardrails/controls $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q guardrails/isolation/tests > "$(suite_log guardrails/isolation)" 2>&1
judge guardrails/isolation $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q guardrails/sandbox/tests > "$(suite_log guardrails/sandbox)" 2>&1
judge guardrails/sandbox $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q identity/onboarding/tests > "$(suite_log identity/onboarding)" 2>&1
judge identity/onboarding $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q identity/cpapi/tests > "$(suite_log identity/cpapi)" 2>&1
judge identity/cpapi $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q identity/edges/tests > "$(suite_log identity/edges)" 2>&1
judge identity/edges $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q identity/entitlements/tests > "$(suite_log identity/entitlements)" 2>&1
judge identity/entitlements $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q identity/sso/tests > "$(suite_log identity/sso)" 2>&1
judge identity/sso $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q telemetry/ledger/tests > "$(suite_log telemetry/ledger)" 2>&1
judge telemetry/ledger $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q telemetry/audit/tests > "$(suite_log telemetry/audit)" 2>&1
judge telemetry/audit $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q telemetry/observability/tests > "$(suite_log telemetry/observability)" 2>&1
judge telemetry/observability $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q telemetry/budgets/tests > "$(suite_log telemetry/budgets)" 2>&1
judge telemetry/budgets $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q control-plane/instructions/tests > "$(suite_log control-plane/instructions)" 2>&1
judge control-plane/instructions $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q control-plane/sdk/python/tests > "$(suite_log control-plane/sdk/python)" 2>&1
judge control-plane/sdk/python $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/merge/tests > "$(suite_log governance/merge)" 2>&1
judge governance/merge $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/dispatch/tests > "$(suite_log governance/dispatch)" 2>&1
judge governance/dispatch $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/finops/tests > "$(suite_log governance/finops)" 2>&1
judge governance/finops $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/sync/tests > "$(suite_log governance/sync)" 2>&1
judge governance/sync $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q infra/rollout/tests > "$(suite_log infra/rollout)" 2>&1
judge infra/rollout $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/lessons/tests > "$(suite_log governance/lessons)" 2>&1
judge governance/lessons $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/lessons-sync/tests > "$(suite_log governance/lessons-sync)" 2>&1
judge governance/lessons-sync $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/remediation/tests > "$(suite_log governance/remediation)" 2>&1
judge governance/remediation $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/board/tests > "$(suite_log governance/board)" 2>&1
judge governance/board $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/isolation/tests > "$(suite_log governance/isolation)" 2>&1
judge governance/isolation $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/pmo/tests > "$(suite_log governance/pmo)" 2>&1
judge governance/pmo $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/rollup/tests > "$(suite_log governance/rollup)" 2>&1
judge governance/rollup $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q control-plane/fleet-template/tests > "$(suite_log control-plane/fleet-template)" 2>&1
judge control-plane/fleet-template $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q gateway/sme-routing/tests > "$(suite_log gateway/sme-routing)" 2>&1
judge gateway/sme-routing $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/paperclip/tests > "$(suite_log integrations/paperclip)" 2>&1
judge integrations/paperclip $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/paperclip/adapters/approvals/tests > "$(suite_log integrations/paperclip/adapters/approvals)" 2>&1
judge integrations/paperclip/adapters/approvals $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/paperclip/adapters/routines/tests > "$(suite_log integrations/paperclip/adapters/routines)" 2>&1
judge integrations/paperclip/adapters/routines $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/paperclip/adapters/secrets/tests > "$(suite_log integrations/paperclip/adapters/secrets)" 2>&1
judge integrations/paperclip/adapters/secrets $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/paperclip/adapters/skills/tests > "$(suite_log integrations/paperclip/adapters/skills)" 2>&1
judge integrations/paperclip/adapters/skills $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q registry/parity/tests > "$(suite_log registry/parity)" 2>&1
judge registry/parity $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q guardrails/chat/tests > "$(suite_log guardrails/chat)" 2>&1
judge guardrails/chat $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q registry/chat/tests > "$(suite_log registry/chat)" 2>&1
judge registry/chat $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q telemetry/chat/tests > "$(suite_log telemetry/chat)" 2>&1
judge telemetry/chat $?

# --- the three suites whose CI-venue red #1509 resolved, and which are named here
#     again BECAUSE each cause was repaired rather than excused -------------------
timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q guardrails/honesty/tests > "$(suite_log guardrails/honesty)" 2>&1
judge guardrails/honesty $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/lifecycle/tests > "$(suite_log governance/lifecycle)" 2>&1
judge governance/lifecycle $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/reconcile/tests > "$(suite_log governance/reconcile)" 2>&1
judge governance/reconcile $?

# The following four were repaired and wired in by #1501: each failure was a
# test-suite defect (module-identity / stale-clock / stale-expectation), not
# a product defect, and each is fixed at its root cause rather than skipped.
timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/conformance/tests > "$(suite_log governance/conformance)" 2>&1
judge governance/conformance $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/ticket/tests > "$(suite_log governance/ticket)" 2>&1
judge governance/ticket $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/cto-overlay/tests > "$(suite_log governance/cto-overlay)" 2>&1
judge governance/cto-overlay $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/authority/tests > "$(suite_log governance/authority)" 2>&1
judge governance/authority $?

# The following four were repaired and wired in by #1725: each was a
# vendor/CMR precondition (or a live-venue precondition, for `scripts`)
# misread as unavailable by a stale `.is_dir()`/no-skip check, not a product
# defect — see scripts/gate-coverage-baseline.txt for the root cause of each.
timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q scripts/tests > "$(suite_log scripts)" 2>&1
judge scripts $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/knowledge/tests > "$(suite_log governance/knowledge)" 2>&1
judge governance/knowledge $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q governance/modules/tests > "$(suite_log governance/modules)" 2>&1
judge governance/modules $?

timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q integrations/paperclip/reporting/tests > "$(suite_log integrations/paperclip/reporting)" 2>&1
judge integrations/paperclip/reporting $?

# Repaired and wired in by #1737: the 8 failures were three portal-surface
# feature-flag entries in infra/feature-flags/registry.yaml (fleet_projection,
# remote_control, operator_terminal) shipping `default: on` in violation of
# GR-5; not a suite defect. See scripts/gate-coverage-baseline.txt.
timeout "$suite_timeout" env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q portal/tests > "$(suite_log portal)" 2>&1
judge portal $?

if [ "$ran" -ne "$LISTED" ]; then
  printf 'check-pytest-suites: NOT-OK — %d suite(s) ran but %d are named in this check; the list and the run disagree\n' \
    "$ran" "$LISTED" >&2
  exit 1
fi

if [ "$failed" -ne 0 ]; then
  printf 'check-pytest-suites: NOT-OK — %d of %d declared suites named by no other gate failed\n' \
    "$failed" "$LISTED" >&2
  exit 1
fi

printf 'check-pytest-suites: OK — %d of %d declared suites no other gate names ran and passed\n' \
  "$ran" "$LISTED"
exit 0
