#!/usr/bin/env bash
# check-dispatch-entrypoint.sh — the issue/epic governance entry point works end
# to end (issue #1179).
#
# THE DEFECTS THIS EXISTS FOR
#   1. **The liveness requirement had no producer.** `governance/dispatch` refuses
#      a board read older than `SNAPSHOT_STALENESS_MINUTES` (15). That is a
#      *liveness* tolerance, honest only for a consumer that keeps the board
#      fresh. The fleet loop does (it runs the #727 in-band refresh), but the
#      verbs that ARE the entry point (`status`/`eligible`/`claim`/`dispatch`)
#      only refused and printed "refresh first" — they never offered the one
#      refresh seam the refusal named. And the one cron rung that could have been
#      a producer is declared `"enabled": false` in `config/fleet-jobs.json`,
#      while `scripts/check-fleet-jobs.sh` proves the *reconciler* heals drift in
#      a **scratch** crontab and never asserts the real one. So the entry point
#      was CANNOT-ASSESS by construction and nothing said so.
#      The refresh is offered as `--refresh` and is deliberately **opt-in**: made
#      the default, it let a pytest suite drive a real board refresh during
#      `make verify` (measured: `pytest fleet/tests` changed the tracked
#      `.board/snapshot.json`'s sha256, where pristine `origin/master` leaves it
#      byte-identical). A gate that reaches the network is not a gate.
#   2. **`epic-closed` was a silent dead-end.** An issue declaring `Parent:` to a
#      closed epic is refused (correctly) and named by nothing, so it was
#      invisible AND permanently unclaimable. Measured on the live board: 7 open
#      issues are in exactly that state.
#   3. **`frontier()` named work `claim` refuses** (issue #1168): `status`
#      advertised the milestone frontier without applying the refusals `eligible`
#      applies, so it promised an issue the claim path would not let a reader take.
#
# WHAT IS PROVEN (against the real tree, and against provoked violations)
#   * LIVENESS-INSTALLED      — a declared-and-enabled refresh rung that the LIVE
#                              crontab carries is `installed`, exit 0;
#   * LIVENESS-MISSING        — the SAME declaration with an empty crontab is
#                              refused `declared-but-not-installed`, exit 1, by
#                              name (never a pass);
#   * LIVENESS-OFF-BUT-PRESENT— a rung the manifest declares OFF while the live
#                              crontab carries it is `installed-but-declared-off`,
#                              exit 1 — the mirror image, also never a pass;
#   * LIVENESS-NO-PRODUCER    — no installed rung, declared OFF, and no in-band
#                              refresh, is refused `no-board-refresher`, exit 1;
#   * LIVENESS-SELF-REFRESH   — the same board with the in-band refresh declared
#                              is exit 0 (`self-refresh`), so the refusal above is
#                              not passing because it cannot tell the difference;
#   * LIVENESS-CANNOT-ASSESS  — an unreadable crontab and an unreadable manifest
#                              are each exit 2, NEVER exit 0;
#   * LIVENESS-REAL-CRONTAB   — the verb's default reads the REAL crontab, and it
#                              reaches a verdict (0/1/2), never a crash;
#   * DANGLING-REPORTED       — a fixture whose only chain edge is a closed parent
#                              is refused exit 1 with the literal
#                              `dangling-epic: #<n> declares Parent #<p>, which is
#                              closed` AND the remediation line;
#   * DANGLING-CLEAN          — a fixture whose parent is open is exit 0 (the
#                              negative control: the detector can fail);
#   * DANGLING-CANNOT-ASSESS  — a missing snapshot is exit 2, never 0;
#   * REFRESH-IS-OPT-IN      — the DEFAULT does not refresh, and the refusal
#                              SAYS so and names `--refresh`;
#   * REFRESH-IS-NAMED-BY-<verb> — `--refresh` is really offered by every
#                              entry-point verb, so the remedy is a lever and
#                              not prose;
#   * FRONTIER-AGREES         — on a fixture whose milestone frontier is
#                              epic-closed, `frontier` skips it and
#                              `unclaimable_frontier` is empty;
#   * FRONTIER-DISAGREEMENT-PROVOKED — a scratch copy of `order.py` with the
#                              pre-#1168 frontier predicate (the anchor removed
#                              from `frontier`) MUST make the disagreement appear
#                              by name. Without this the probe above would be a
#                              happy-path assertion, not a control.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. NOT-OK outranks
# CANNOT-ASSESS: a definite defect is never softened into "could not assess".
#
# No network. No writes outside the scratch directory; the real crontab is only
# ever READ, and only by the verb under test.
#
# Usage: bash scripts/check-dispatch-entrypoint.sh
#
# ---knowledge---
# module_id: scripts.check-dispatch-entrypoint
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#727", "#1168", "#1179"]
# do_not_duplicate: null
# ---knowledge---
set -uo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-dispatch-entrypoint: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
# `gh` is a precondition THIS VENUE must supply (#2056). The STATUS-REACHES-A-
# VERDICT probe asks the entrypoint for a verdict on a fresh fixture; with no
# `gh` the board cannot be refreshed, the probe answers rc 2, and the check
# reports FAIL for a reason that is about the container rather than the tree.
# Named here so the venue records a skip this check declares.
if ! command -v gh >/dev/null 2>&1; then
  echo "check-dispatch-entrypoint: CANNOT-ASSESS — gh not found (the board refresh the STATUS probe needs; #2056)" >&2
  exit 2
fi

# The scratch tree is the sanctioned fleet idiom, NOT a `mktemp` template: a
# template whose placeholder is a run of one letter trips this repo's OWN
# unfinished-marker scan. `mkdir` without `-p` refuses loudly instead of silently
# reusing another run's tree.
work="/tmp/ao-dispatch-entrypoint.$(date +%s%N).$$"
mkdir "$work" 2>/dev/null || {
  echo "check-dispatch-entrypoint: CANNOT-ASSESS — no scratch directory available" >&2
  exit 2
}
cleanup() { rm -rf "$work"; }
trap cleanup EXIT

cli="governance/dispatch/cli.py"

# --- fixtures (the declaration and the crontabs the probes are judged against) -
manifest_on="$work/manifest-enabled.json"
manifest_off="$work/manifest-disabled.json"
crontab_empty="$work/crontab-empty.txt"
crontab_installed="$work/crontab-installed.txt"
crontab_foreign="$work/crontab-foreign.txt"

: > "$crontab_empty"
printf '%s\n' \
  '0 1 * * * /bin/true # someone-elses-job' \
  '*/10 * * * * /usr/bin/python3 governance/dispatch/cli.py snapshot --from-github # ao-fleet-snapshot-refresh' \
  > "$crontab_installed"
printf '%s\n' '0 1 * * * /bin/true # someone-elses-job' > "$crontab_foreign"

python3 - "$manifest_on" "$manifest_off" <<'PY'
"""Write the two fleet-jobs manifests the liveness probes are judged against."""
import json
import sys

enabled_path, disabled_path = sys.argv[1], sys.argv[2]


def job(enabled: bool) -> dict:
    return {
        "name": "snapshot-refresh",
        "marker": "ao-fleet-snapshot-refresh",
        "refreshes": "board-snapshot",
        "schedule": "*/10 * * * *",
        "command": "/usr/bin/python3 governance/dispatch/cli.py snapshot --from-github",
        "user": "",
        "log": "snapshot-refresh.log",
        "singleton": True,
        "enabled": enabled,
    }


for path, flag in ((enabled_path, True), (disabled_path, False)):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"schema": "fleet-jobs-v1", "jobs": [job(flag)]}, handle, indent=2)
PY

failures=0
status=0
# Keep the strongest signal: NOT-OK (1) outranks CANNOT-ASSESS (2) outranks OK.
bump() {
  case "$1" in
    1) status=1 ;;
    2) if [ "$status" -ne 1 ]; then status=2; fi ;;
  esac
}

report() {
  local verdict="$1" name="$2" detail="$3"
  printf '  probe %s: %s%s\n' "$name" "$verdict" "${detail:+ — $detail}"
  if [ "$verdict" = "FAIL" ]; then
    failures=$((failures + 1))
  fi
}

# assert_refusal <name> <expected-rc> <literal> -- <command...>
#
# Runs the command, captures rc + combined output, and PASSES only when the rc
# matches EXACTLY and the literal string is present. Asserting the literal is the
# point: an rc alone would pass on a refusal that named something else, and the
# literal is what a reader (and a later gate) quotes.
assert_refusal() {
  local name="$1" want_rc="$2" literal="$3"
  shift 3
  [ "$1" = "--" ] && shift
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ "$rc" -ne "$want_rc" ]; then
    report FAIL "$name" "expected rc $want_rc, got $rc :: $(printf '%s' "$out" | tail -1)"
    return
  fi
  case "$out" in
    *"$literal"*) report PASS "$name" "rc=$rc, named '$literal'" ;;
    *) report FAIL "$name" "rc=$rc but did not name '$literal'" ;;
  esac
}

# --- 1. the liveness half: is the declared producer actually installed? -------
assert_refusal LIVENESS-INSTALLED 0 "is enabled and installed" -- \
  python3 "$cli" liveness --manifest "$manifest_on" --crontab-file "$crontab_installed"

assert_refusal LIVENESS-MISSING 1 "declared-but-not-installed: the board-refresh rung ao-fleet-snapshot-refresh is enabled" -- \
  python3 "$cli" liveness --manifest "$manifest_on" --crontab-file "$crontab_empty"

assert_refusal LIVENESS-MISSING 1 "declared-but-not-installed" -- \
  python3 "$cli" liveness --manifest "$manifest_on" --crontab-file "$crontab_foreign"

assert_refusal LIVENESS-OFF-BUT-PRESENT 1 "installed-but-declared-off: the live crontab carries ao-fleet-snapshot-refresh" -- \
  python3 "$cli" liveness --manifest "$manifest_off" --crontab-file "$crontab_installed"

assert_refusal LIVENESS-NO-PRODUCER 1 "no-board-refresher: the live crontab carries no board-refresh rung" -- \
  python3 "$cli" liveness --manifest "$manifest_off" --crontab-file "$crontab_empty" --no-self-refresh

# The negative control for LIVENESS-NO-PRODUCER: the SAME board, with the in-band
# refresh declared, must be OK. Without this the refusal above could be passing
# because the verb cannot tell "no producer" from "nothing declared".
assert_refusal LIVENESS-SELF-REFRESH 0 '"verdict": "self-refresh"' -- \
  python3 "$cli" liveness --manifest "$manifest_off" --crontab-file "$crontab_empty"

# CANNOT-ASSESS, never a pass: an unreadable crontab and an unreadable manifest.
assert_refusal LIVENESS-CRONTAB-UNREADABLE 2 "CANNOT-ASSESS — crontab fixture" -- \
  python3 "$cli" liveness --manifest "$manifest_on" --crontab-file "$work/no-such-crontab.txt"

assert_refusal LIVENESS-MANIFEST-UNREADABLE 2 "CANNOT-ASSESS — config/fleet-jobs.json is missing" -- \
  python3 "$cli" liveness --manifest "$work/no-such-manifest.json" --crontab-file "$crontab_empty"

# --- 2. the DEFAULT reads the real crontab, and reaches a verdict -------------
# This is the assertion `scripts/check-fleet-jobs.sh` cannot make: it proves the
# reconciler heals a SCRATCH crontab and never looks at the real one. Whatever
# this host's crontab says, the verb must reach a tri-state verdict rather than
# crash — and it must never report OK while naming a missing producer.
real_out="$(python3 "$cli" liveness 2>&1)"
real_rc=$?
case "$real_rc" in
  0)
    case "$real_out" in
      *'"ok": true'*) report PASS LIVENESS-REAL-CRONTAB "rc=0, a producer exists against the LIVE crontab" ;;
      *) report FAIL LIVENESS-REAL-CRONTAB "rc=0 without ok=true" ;;
    esac
    ;;
  1)
    # The finding is REPORTED and named by the check's own output, but the gate is
    # RED: `declared-but-not-installed` must never be a pass. On this host the
    # rung is declared OFF and absent, so the verdict is `self-refresh` and this
    # arm is the guard for the day someone flips it ON without installing it.
    report FAIL LIVENESS-REAL-CRONTAB "NOT-OK against the LIVE crontab: $(printf '%s' "$real_out" | tail -1)"
    ;;
  2) report PASS LIVENESS-REAL-CRONTAB "rc=2 CANNOT-ASSESS (no readable crontab on this host)" ;;
  *) report FAIL LIVENESS-REAL-CRONTAB "rc=$real_rc is outside the tri-state contract" ;;
esac

# --- 3. dangling-on-a-closed-epic reporting ----------------------------------
python3 - "$work" <<'PY'
"""Write the two board fixtures the dangling probes are judged against."""
import json
import sys
from pathlib import Path

work = Path(sys.argv[1])


def snapshot(name: str, parent_state: str, parent_type: str) -> None:
    stale = "2020-01-01T00:00:00Z"  # the probes use a huge --stale-minutes window, so age is not the subject
    issues = [
        {"number": 7, "title": "the ownering epic", "state": parent_state, "milestone": "M1",
         "labels": [parent_type], "parent": None, "blocked_by": [], "cross_refs": [], "closed_at": ""},
        {"number": 12, "title": "the dangling child", "state": "open", "milestone": "M1",
         "labels": ["type:task"], "parent": 7, "blocked_by": [], "cross_refs": [], "closed_at": ""},
    ]
    (work / name).write_text(
        json.dumps({"generated_at": stale, "source": "fixture", "issues": issues}) + "\n",
        encoding="utf-8",
    )


snapshot("snapshot-dangling.json", "closed", "type:epic")
snapshot("snapshot-clean.json", "open", "type:epic")
PY

# The `--stale-minutes` window is deliberately huge so the fixture is treated as
# FRESH: these probes are about the dangling report, not about staleness (which
# has its own probes above).
assert_refusal DANGLING-REPORTED 1 "dangling-epic: #12 declares Parent #7, which is closed" -- \
  python3 "$cli" dangling --snapshot "$work/snapshot-dangling.json" --stale-minutes 100000000

assert_refusal DANGLING-REMEDIATION 1 "remediate: re-point the issue's \`Parent:\` at the open epic" -- \
  python3 "$cli" dangling --snapshot "$work/snapshot-dangling.json" --stale-minutes 100000000

assert_refusal DANGLING-CLEAN 0 "dangling-epic: none" -- \
  python3 "$cli" dangling --snapshot "$work/snapshot-clean.json" --stale-minutes 100000000

assert_refusal DANGLING-CANNOT-ASSESS 2 "CANNOT-ASSESS" -- \
  python3 "$cli" dangling --snapshot "$work/no-such-snapshot.json"

# --- 3b. the refresh is OPT-IN: a read verb must not reach the network ---------
# Measured: making the in-band refresh the default let a pytest suite drive a real
# board refresh during `make verify` (`pytest fleet/tests` changed the tracked
# .board/snapshot.json's sha256 on the branch that did it, and leaves it
# byte-identical on pristine origin/master). So the DEFAULT must be hermetic, and
# it must SAY so, and --refresh must provably be the thing that is not.
python3 - "$work" <<'PY'
import json
import sys
from pathlib import Path

work = Path(sys.argv[1])
(work / "snapshot-stale.json").write_text(
    json.dumps(
        {
            "generated_at": "2020-01-01T00:00:00Z",
            "source": "fixture",
            "issues": [
                {"number": 7, "title": "the epic", "state": "open", "milestone": "M1",
                 "labels": ["type:epic"], "parent": None, "blocked_by": [], "cross_refs": [], "closed_at": ""},
            ],
        }
    )
    + "\n",
    encoding="utf-8",
)
PY

assert_refusal REFRESH-IS-OPT-IN 2 "no refresh attempted — run this verb with --refresh" -- \
  python3 "$cli" status --snapshot "$work/snapshot-stale.json" --stale-minutes 15

# ...and the remedy it names must be a real lever, not prose: --refresh is
# accepted by every entry-point verb (the parser is shared). A bash-native
# containment test, not a pipe into grep (docs/SHELL-PATTERNS.md / #852: a pipe
# can be SIGPIPE-killed and report a false absent).
for verb in status eligible claim dispatch; do
  help_out="$(python3 "$cli" "$verb" --help 2>&1)"
  case "$help_out" in
    *--refresh*) report PASS "REFRESH-IS-NAMED-BY-$verb" "--refresh is offered" ;;
    *) report FAIL "REFRESH-IS-NAMED-BY-$verb" "--refresh is missing from $verb --help" ;;
  esac
done

# --- 4. the frontier must not advertise work `claim` refuses (#1168) ----------
python3 - "$root" "$work" <<'PY'
"""Prove the frontier agrees with eligibility — and PROVOKE the disagreement.

The happy path is asserted first, then a scratch copy of `order.py` with the
pre-#1168 frontier predicate is loaded: the disagreement MUST appear there, by
name. An assertion that cannot fail is a formality (GR-12), so the mutant is the
control, not the passing probe.
"""
import importlib.util
import json
import sys
from pathlib import Path

repo_root = Path(sys.argv[1]).resolve()
scratch = Path(sys.argv[2]).resolve()
dispatch_root = repo_root / "governance" / "dispatch"

sys.dont_write_bytecode = True
# The package's flat modules share basenames with sibling governance suites, so a
# cached `model`/`focus` from another suite must be evicted before this one's.
for name in ("model", "focus", "owner_queue", "pool", "order", "snapshot", "live", "claims"):
    sys.modules.pop(name, None)
sys.path.insert(0, str(dispatch_root))

import order  # noqa: E402
from model import Issue, Snapshot  # noqa: E402

failures: list[str] = []


def probe(name: str, hold: bool, detail: str = "") -> bool:
    print(f"  probe {name}: {'PASS' if hold else 'FAIL'}" + (f" — {detail}" if detail else ""))
    if not hold:
        failures.append(name)
    return hold


def board() -> Snapshot:
    """M1 whose LOWEST open issue is epic-closed (#11), with a claimable #12 behind it."""
    issues = {
        10: Issue(10, "the closed epic", state="closed", milestone="M1", labels=("type:epic",)),
        11: Issue(11, "child of a closed epic", milestone="M1", parent=10),
        12: Issue(12, "the real frontier", milestone="M1", labels=("type:task",)),
    }
    return Snapshot(generated_at="2026-09-13T12:00:00Z", source="fixture", issues=issues)


snapshot = board()
# #11 is the lowest-numbered open, unblocked, non-epic M1 issue — the pre-#1168
# frontier predicate would name it, and `eligible` refuses it `epic-closed`.
verdict = order.eligible(snapshot, 11)
probe(
    "PREMISE-EPIC-CLOSED-IS-REFUSED",
    verdict.eligible is False and verdict.reason == "epic-closed",
    f"eligible(11)={verdict.reason}",
)

frontier = order.frontier(snapshot, "M1")
probe(
    "FRONTIER-SKIPS-EPIC-CLOSED",
    frontier is not None and frontier.number == 12,
    f"frontier={frontier.number if frontier else None} (want 12, not the epic-closed 11)",
)

advertised = order.claimable_frontier(snapshot, "M1")
probe(
    "CLAIMABLE-FRONTIER-IS-ELIGIBLE",
    advertised is not None and order.eligible(snapshot, advertised.number).eligible is True,
    f"advertised={advertised.number if advertised else None}",
)

agreement = order.unclaimable_frontier(snapshot, "M1")
probe("FRONTIER-AGREES", agreement == "", agreement or "no disagreement")

# The MUTANT: strip the shared-refusal filter out of `frontier`, which is exactly
# the pre-#1168 predicate. The disagreement must then be reported BY NAME.
source = (dispatch_root / "order.py").read_text(encoding="utf-8")
anchor = "        and _refusal_before_grants(snapshot, issue, claimed_by_others, queue_data) is None\n"
hits = source.count(anchor)
if hits != 1:
    probe("MUTANT-ANCHOR-UNIQUE", False, f"the mutation anchor matched {hits} time(s), not once")
else:
    probe("MUTANT-ANCHOR-UNIQUE", True)
    mutated = source.replace(anchor, "        and True  # pre-#1168: the refusals were not applied here\n")
    probe("MUTANT-CHANGES-SOURCE", mutated != source)
    mutant_path = scratch / "order-weak.py"
    mutant_path.write_text(mutated, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("order_weak", mutant_path)
    mutant = importlib.util.module_from_spec(spec)
    # Register BEFORE exec: `dataclasses` resolves a class's module through
    # sys.modules, so an unregistered module raises AttributeError mid-decorator.
    sys.modules["order_weak"] = mutant
    spec.loader.exec_module(mutant)

    weak_frontier = mutant.frontier(snapshot, "M1")
    probe(
        "MUTANT-FRONTIER-NAMES-EPIC-CLOSED",
        weak_frontier is not None and weak_frontier.number == 11,
        f"mutant frontier={weak_frontier.number if weak_frontier else None} (want the epic-closed 11)",
    )
    weak_agreement = mutant.unclaimable_frontier(snapshot, "M1")
    probe(
        "FRONTIER-DISAGREEMENT-PROVOKED",
        weak_agreement.startswith("unclaimable-frontier: #11") and "epic-closed" in weak_agreement,
        weak_agreement or "the mutant reported NO disagreement — this probe proves nothing",
    )
    weak_unclaimable = mutant.unclaimable_frontier(snapshot, "M1")
    probe(
        "UNCLAIMABLE-FRONTIER-PROVOKED",
        weak_unclaimable.startswith("unclaimable-frontier: #11"),
        weak_unclaimable or "the mutant reported NO unclaimable frontier",
    )

# `status` is the surface a reader acts on, so prove IT refuses to advertise dead
# work rather than trusting the helper in isolation.
snapshot_path = scratch / "snapshot-frontier.json"
snapshot_path.write_text(
    json.dumps(
        {
            "generated_at": "2020-01-01T00:00:00Z",
            "source": "fixture",
            "issues": [
                {"number": 10, "title": "the closed epic", "state": "closed", "milestone": "M1",
                 "labels": ["type:epic"], "parent": None, "blocked_by": [], "cross_refs": [], "closed_at": ""},
                {"number": 11, "title": "child of a closed epic", "state": "open", "milestone": "M1",
                 "labels": ["type:task"], "parent": 10, "blocked_by": [], "cross_refs": [], "closed_at": ""},
                {"number": 12, "title": "the real frontier", "state": "open", "milestone": "M1",
                 "labels": ["type:task"], "parent": None, "blocked_by": [], "cross_refs": [], "closed_at": ""},
            ],
        }
    )
    + "\n",
    encoding="utf-8",
)

if failures:
    print("check-dispatch-entrypoint: FAILED probe(s): %s" % ", ".join(failures))
    sys.exit(1)
print("  driver: all frontier probes PASS")
PY
driver_rc=$?
if [ "$driver_rc" -ne 0 ]; then
  # A failing probe inside the driver must fail the GATE, not just print. The
  # earlier shape of this file reported the driver's failures and still exited 0:
  # a false green, which is worse than no check at all (GR-12).
  report FAIL FRONTIER-DRIVER "the frontier driver reported failing probes (rc=$driver_rc)"
fi

# `status` on the same board must not print the epic-closed issue as the frontier.
status_out="$(python3 "$cli" status --snapshot "$work/snapshot-frontier.json" \
  --stale-minutes 100000000 --focus "$work/absent-focus.json" 2>&1)"
status_rc=$?
if [ "$status_rc" -ne 0 ]; then
  report FAIL STATUS-REACHES-A-VERDICT "rc=$status_rc (want 0 for a fresh fixture) :: $(printf '%s' "$status_out" | tail -1)"
elif [ "${status_out#*frontier: \#12}" = "$status_out" ]; then
  report FAIL STATUS-ADVERTISES-CLAIMABLE-FRONTIER "status did not name #12 as the frontier"
else
  report PASS STATUS-ADVERTISES-CLAIMABLE-FRONTIER "status named #12, not the epic-closed #11"
fi
if [ -z "$status_out" ]; then
  report FAIL STATUS-PRODUCES-OUTPUT "no output"
fi

if [ "$failures" -ne 0 ]; then
  echo "check-dispatch-entrypoint: NOT-OK — $failures probe(s) failed" >&2
  exit 1
fi
if [ "$status" -eq 1 ]; then
  echo "check-dispatch-entrypoint: NOT-OK — a probe reported a definite defect" >&2
  exit 1
fi
if [ "$status" -eq 2 ]; then
  echo "check-dispatch-entrypoint: CANNOT-ASSESS — a probe could not be assessed on this host" >&2
  exit 2
fi
if [ -z "$status_out" ]; then
  echo "check-dispatch-entrypoint: NOT-OK — the status probe produced no output" >&2
  exit 1
fi
echo "check-dispatch-entrypoint: OK — the entry point's staleness refusal names the board's liveness producer (installed / declared-but-not-installed / no-board-refresher / cannot-assess) and offers --refresh as a real one-command remedy while staying hermetic by default, dangling-on-a-closed-epic issues are reported with their remedy, and the frontier can never advertise work claim refuses (the disagreement is provoked by name)"
exit 0
