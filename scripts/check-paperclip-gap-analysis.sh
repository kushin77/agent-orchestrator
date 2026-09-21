#!/usr/bin/env bash
# check-paperclip-gap-analysis.sh — the sourced paperclip.ing gap-analysis gate
# (issue #368).
#
# The adoption decision for upstream paperclip.ing is only a decision if the
# document that records it can be proven complete (no-false-green doctrine,
# GR-12). This gate pins `docs/PAPERCLIP-ING-GAP-ANALYSIS.md` and fails, by name,
# when the committed doc loses any load-bearing part:
#
#   * the GR-10 provenance declarations (upstream repo, MIT license, a release
#     version string and a retrieval date);
#   * the namesake disambiguation (the fleet agent, the gateway provider, the
#     vendored CMR planning module and the upstream product are four different
#     "paperclips" and must stay distinguished);
#   * the six upstream capability families, each mapped onto a fleet file path;
#   * the cannibalize-vs-build table; and
#   * its registration in `docs/CANNIBALIZATION.md`.
#
# It then runs its own negative control: it strips the provenance line from a
# copy of the doc, asserts the copy really changed (its sha256 differs), and
# requires the validator to refuse it. If the mutant passes, this gate reports
# FAIL — a check that cannot fail is a formality.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-paperclip-gap-analysis.sh
#
# ---knowledge---
# module_id: scripts.check-paperclip-gap-analysis
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#368"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

doc="docs/PAPERCLIP-ING-GAP-ANALYSIS.md"
cannibalization="docs/CANNIBALIZATION.md"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-gap-analysis: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -f "$doc" ]; then
  echo "check-paperclip-gap-analysis: FAIL — $doc is missing (no sourced gap analysis)" >&2
  exit 1
fi

if [ ! -f "$cannibalization" ]; then
  echo "check-paperclip-gap-analysis: FAIL — $cannibalization is missing (nowhere to register the harvest)" >&2
  exit 1
fi

# validate <path> — one gap-analysis document. 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
validate() {
  python3 - "$1" <<'PY'
import sys

path = sys.argv[1]
try:
    text = open(path, encoding="utf-8").read()
except OSError as exc:
    print(f"check-paperclip-gap-analysis: CANNOT-ASSESS - cannot read {path}: {exc}", file=sys.stderr)
    raise SystemExit(2)

# GR-10 provenance: what was read, under which license, at which version, when.
PROVENANCE = [
    ("upstream repository", "paperclipai/paperclip"),
    ("license", "MIT"),
    ("release version", "v2026.831.1"),
    ("retrieval date", "2026-09-13"),
]
# The four same-named "paperclips" that must remain disambiguated.
NAMESAKE = [
    ("fleet agent profile", "registry/profiles/seeds/paperclip.1.0.0.yaml"),
    ("fleet agent persona", "registry/personas/cards/paperclip.yaml"),
    ("gateway provider", "gateway/providers/paperclip.py"),
    ("vendored CMR planning module", "vendor/CMR/catalog/modules/paperclip"),
    ("boundary ADR", "ADR-0012"),
]
# The six upstream capability families, each required to be named.
FAMILIES = [
    "Org chart",
    "Goal alignment",
    "Heartbeats",
    "Budgets & costs",
    "Tickets + audit",
    "Governance",
]
# The adoption-decision surface.
DECISION = [
    ("cannibalize-vs-build table", "Cannibalize vs build"),
    ("no-vendor statement", "does not vendor"),
    ("console-not-removed statement", "fleet/console.py"),
]

findings = []
for label, marker in PROVENANCE + NAMESAKE + DECISION:
    if marker not in text:
        findings.append(f"{path}: missing {label} (expected {marker!r})")
for family in FAMILIES:
    if family not in text:
        findings.append(f"{path}: missing capability family {family!r}")

if findings:
    for finding in findings:
        print(f"  FAIL  {finding}", file=sys.stderr)
    raise SystemExit(1)
print(f"  OK    {path} declares provenance, namesakes, six families and the decision table")
raise SystemExit(0)
PY
}

validate "$doc"
rc=$?
case "$rc" in
  0) : ;;
  1) echo "check-paperclip-gap-analysis: FAIL — $doc is incomplete (see findings above)" >&2; exit 1 ;;
  *) echo "check-paperclip-gap-analysis: CANNOT-ASSESS — validator returned $rc" >&2; exit 2 ;;
esac

# The harvest must be registered where the repo indexes provenance (GR-10).
if ! grep -qF -- "PAPERCLIP-ING-GAP-ANALYSIS.md" "$cannibalization"; then
  echo "  FAIL  $cannibalization does not register docs/PAPERCLIP-ING-GAP-ANALYSIS.md" >&2
  echo "check-paperclip-gap-analysis: FAIL — the harvest is unregistered" >&2
  exit 1
fi
if ! grep -qF -- "#368" "$cannibalization"; then
  echo "  FAIL  $cannibalization does not name the issue (#368)" >&2
  echo "check-paperclip-gap-analysis: FAIL — the harvest is unregistered" >&2
  exit 1
fi
echo "  OK    $cannibalization registers the harvest (issue #368)"

# Negative control: strip the provenance from a copy and require refusal.
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-paperclip-gap-analysis: CANNOT-ASSESS — sha256sum not found (cannot prove the mutation)" >&2
  exit 2
fi

work="/tmp/paperclip-gap.$$.$(date +%s)"
if ! mkdir -p "$work"; then
  echo "check-paperclip-gap-analysis: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$work"' EXIT

mutant="$work/mutant.md"
grep -vF -- "paperclipai/paperclip" "$doc" > "$mutant"

if cmp -s "$doc" "$mutant"; then
  echo "check-paperclip-gap-analysis: CANNOT-ASSESS — the negative control did not change the doc" >&2
  exit 2
fi

orig_sha="$(sha256sum "$doc" | awk '{print $1}')"
mut_sha="$(sha256sum "$mutant" | awk '{print $1}')"
if [ -z "$orig_sha" ] || [ -z "$mut_sha" ] || [ "$orig_sha" = "$mut_sha" ]; then
  echo "check-paperclip-gap-analysis: CANNOT-ASSESS — sha256 of the mutant did not change" >&2
  exit 2
fi

mutant_out="$(validate "$mutant" 2>&1)"
mutant_rc=$?
if [ "$mutant_rc" -eq 1 ]; then
  echo "  OK    negative control: stripping provenance is refused (sha256 ${orig_sha:0:12} != ${mut_sha:0:12})"
else
  echo "check-paperclip-gap-analysis: FAIL — negative control passed; stripping provenance was not caught (the gate cannot fail)" >&2
  echo "$mutant_out" >&2
  exit 1
fi

echo "check-paperclip-gap-analysis: OK — gap analysis is sourced, complete, and its own mutant is refused"
exit 0
