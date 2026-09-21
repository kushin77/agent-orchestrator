#!/usr/bin/env bash
# check-runner-capability.sh — the runner is a quadruple, and it is checked (#841).
#
# The defect this gate exists for, measured 2026-09-15 on `master` @ `dd8cbfc`:
# every subagent run the fleet dispatched died about ten seconds after it started,
# because the loop asked whether its runner *resolved* and never whether that runner
# could honour the *model*:
#
#     [preflight] runner resolved: /home/akushnir/.local/bin/claude   # the only question asked
#     [subagent]  [claude-code:unrecognized_model] {"model":"deepseek-v4-flash","query_source":"sdk"}
#     [sister]    run finished: rc=1 status=failed
#     .fleet/runs/*.log: 192 model-rejection lines, 40 status=failed, 0 status=ok
#
# `claude` was on PATH, `deepseek-v4-flash` was the declared model, and the BYOK
# environment that reconciles the two was unset. Three of the four parts of a runner
# were individually fine and the dispatch still could not work — while the watchdog
# reported both rungs healthy.
#
# What this gate proves, each by name:
#
#   * the contract's own suite passes;
#   * the BEHAVIOUR, not the source: with the declared environment absent the check
#     refuses and NAMES what is missing; with it present the same call is allowed
#     (the two-sided control — a check that refuses unconditionally proves nothing);
#   * the check can FAIL: `unhonourable` is mutated to always allow, the mutant is
#     asserted to have landed by content hash, and the probe is required to notice.
#     A gate whose pass and fail paths collapse into the same exit code is a
#     formality (GR-12), so the mutation is the point of this script.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

pass=0
fail=0
ok() { printf '  OK    %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf '  FAIL  %s\n' "$1"; fail=$((fail + 1)); }

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-runner-capability: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

target="fleet/runners.py"
[ -f "$target" ] || {
  echo "check-runner-capability: CANNOT-ASSESS — $target is missing (has the contract moved?)" >&2
  exit 2
}

# --- 1. the contract's own suite -------------------------------------------------
if env PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider -q \
  fleet/tests/test_runners.py >/dev/null 2>&1; then
  ok "the capability contract's suite passes (fleet/tests/test_runners.py)"
else
  bad "the capability contract's suite FAILED — run it directly for the failures"
fi

# --- 2. the behaviour, both sides ------------------------------------------------
# A probe that asserts the refusal NAMES the missing variables, so a check that
# refuses for an unrelated reason cannot satisfy it.
probe_unwired() {
  env -u ANTHROPIC_BASE_URL -u ANTHROPIC_AUTH_TOKEN python3 - <<'PY'
import sys

sys.path.insert(0, "fleet")
import runners  # noqa: E402

problem = runners.unhonourable("claude -p", env={})
missing = [name for name in ("ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN") if name not in problem]
if not problem:
    print("the unwired pairing was ALLOWED — the measured defect is back")
    raise SystemExit(1)
if missing:
    print("the refusal does not name: " + ", ".join(missing))
    raise SystemExit(1)
print("refused by name")
PY
}

probe_wired() {
  env ANTHROPIC_BASE_URL=https://example.invalid/placeholder \
    ANTHROPIC_AUTH_TOKEN=placeholder-not-a-credential \
    python3 - <<'PY'
import sys

sys.path.insert(0, "fleet")
import runners  # noqa: E402

problem = runners.unhonourable("claude -p", env={
    "ANTHROPIC_BASE_URL": "https://example.invalid/placeholder",
    "ANTHROPIC_AUTH_TOKEN": "placeholder-not-a-credential",
})
if problem:
    print("the wired pairing was refused: " + problem)
    raise SystemExit(1)
print("allowed")
PY
}

if out=$(probe_unwired 2>&1) && [ "$out" = "refused by name" ]; then
  ok "an unwired runner is refused AND the refusal names every missing variable"
else
  bad "the unwired runner was not refused by name (got: ${out:-<no output>})"
fi

if out=$(probe_wired 2>&1) && [ "$out" = "allowed" ]; then
  ok "the same runner with its declared environment present is allowed (two-sided control)"
else
  bad "the wired runner was refused (got: ${out:-<no output>}) — the check cannot pass, so it proves nothing"
fi

# --- 3. the mutation: prove the check can fail -----------------------------------
before=$(sha256sum "$target" | cut -d' ' -f1)
restore() { cp -f "$target.mutant-backup" "$target" 2>/dev/null; rm -f "$target.mutant-backup"; }
trap 'restore' EXIT INT TERM
cp -f "$target" "$target.mutant-backup"

python3 - "$target" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
src = path.read_text(encoding="utf-8")
needle = "    if profile is None:\n        return _unknown_runner(runner)\n    return _missing_environment(profile, world)\n"
replacement = "    if profile is None:\n        return _unknown_runner(runner)\n    return \"\"  # MUTANT: the capability half disabled\n"
if needle not in src:
    print("mutation target not found — the source moved, so this control is vacuous", file=sys.stderr)
    raise SystemExit(3)
path.write_text(src.replace(needle, replacement, 1), encoding="utf-8")
PY
mutant_rc=$?

after=$(sha256sum "$target" | cut -d' ' -f1)
if [ "$mutant_rc" -ne 0 ]; then
  bad "the mutation could not be applied (rc=$mutant_rc) — this control is CANNOT-ASSESS"
elif [ "$before" = "$after" ]; then
  bad "the mutation did NOT change $target (sha256 identical) — a control that never landed proves nothing"
else
  ok "the mutant landed (sha256 ${before:0:12} -> ${after:0:12})"
  if probe_unwired >/dev/null 2>&1; then
    bad "the mutant PASSED the probe — the check cannot fail, so it is a formality (GR-12)"
  else
    ok "the mutant is CAUGHT — the probe fails when the capability check is disabled"
  fi
  # The refusal that must survive the mutation is the other half: an unknown runner.
  if python3 - <<'PY'
import sys

sys.path.insert(0, "fleet")
import runners  # noqa: E402

raise SystemExit(0 if runners.unhonourable("true", env={}) else 1)
PY
  then
    ok "the mutant is scoped: an unidentifiable runner is still refused"
  else
    bad "the mutant also disabled the unknown-runner refusal — the mutation was too broad"
  fi
fi

restore
trap - EXIT INT TERM
restored=$(sha256sum "$target" | cut -d' ' -f1)
if [ "$restored" = "$before" ]; then
  ok "the source is restored byte-for-byte (sha256 ${restored:0:12})"
else
  bad "the source was NOT restored (${before:0:12} -> ${restored:0:12}) — the tree is dirty"
fi

if [ "$fail" -gt 0 ]; then
  echo "check-runner-capability: NOT-OK ($fail of $((pass + fail)) checks failed)"
  exit 1
fi
echo "check-runner-capability: OK — a runner's (binary, argv shape, vocabulary, environment) is checked"
echo "  as a quadruple: an unwired pairing is refused by name, the wired one is allowed, and the check"
echo "  is proved able to fail by a mutant that disables it ($pass checks)."
exit 0
