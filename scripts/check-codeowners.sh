#!/usr/bin/env bash
# check-codeowners.sh — GR-9-adjacent, declared ownership (issue #1073, cites
# #803 row 12: platform-level enforcement of a control that used to be prose).
#
# THE DEFECT THIS EXISTS FOR
#   `.github/CODEOWNERS` did not exist. AGENTS.md and docs/ARCHITECTURE.md
#   declare a five-pillar architecture plus cross-cutting areas, but nothing
#   mapped that declared shape onto a reviewer-ownership file, and nothing
#   would notice if it drifted: a pillar added to the tree with no matching
#   rule, a rule left behind naming a directory that was renamed or removed, or
#   an owner token typo'd into something GitHub silently ignores.
#
# STRUCTURAL — `.github/CODEOWNERS` exists, is non-empty, declares a `*`
# default; every pillar/cross-cutting directory from the fixed list below that
# is actually present in the tree has an explicit rule; every rule names a
# path that exists in the tree (a rule naming a missing path is a stale entry
# -> FAIL, by name); every owner token matches
# `^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$`.
#
# PROVOKED — the comparator is a FUNCTION (`compare_codeowners`), parameterised
# on a CODEOWNERS file and a tree root, so the provocation below drives the
# EXACT SAME code path the live check uses — a provocation that copies the
# logic proves nothing about the path that actually runs. Four fixtures, each
# built under a temp root: (a) a valid map -> exit 0; (b) a tree with a pillar
# dir and no rule for it -> exit 1, naming the pillar; (c) a rule naming a path
# that does not exist in the tree -> exit 1, naming the path; (d) a malformed
# owner token -> exit 1, naming the token. Asserting only the happy path would
# pass a comparator that never looks.
#
# Exit codes: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
#
# Usage: bash scripts/check-codeowners.sh
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

command -v python3 >/dev/null 2>&1 || { echo "check-codeowners: CANNOT-ASSESS — python3 not found" >&2; exit 2; }
command -v mktemp >/dev/null 2>&1 || { echo "check-codeowners: CANNOT-ASSESS — coreutils mktemp not found" >&2; exit 2; }

# The declared pillar/cross-cutting surface (AGENTS.md, docs/ARCHITECTURE.md).
PILLARS=(registry gateway engine guardrails telemetry identity control-plane portal governance infra scripts docs fleet .github)

# The comparator. $1 = CODEOWNERS file, $2 = tree root to validate rules
# against. Prints one "  FAIL  ..." line per defect found and returns 1 if any
# were found, 0 otherwise. Kept as a function so STRUCTURAL and PROVOKED share
# one code path.
compare_codeowners() {
  local file="$1" tree="$2"
  local fail=0

  if [ ! -f "$file" ]; then
    echo "  FAIL  CODEOWNERS file missing: $file"
    return 1
  fi
  if [ ! -s "$file" ]; then
    echo "  FAIL  CODEOWNERS file is empty: $file"
    return 1
  fi

  if ! grep -qE '^\*[[:space:]]+@' "$file"; then
    echo "  FAIL  no default '*' owner rule in $file"
    fail=1
  fi

  local -a rule_paths=()
  local line path owners owner stripped

  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|[[:space:]]*'#'*|'#'*) continue ;;
    esac
    path="$(printf '%s' "$line" | awk '{print $1}')"
    [ -n "$path" ] || continue
    rule_paths+=("$path")

    owners="$(printf '%s' "$line" | awk '{$1=""; print $0}')"
    for owner in $owners; do
      if ! printf '%s' "$owner" | grep -qE '^@[A-Za-z0-9-]+(/[A-Za-z0-9._-]+)?$'; then
        echo "  FAIL  malformed owner token '$owner' on rule '$path'"
        fail=1
      fi
    done
  done < "$file"

  # Every declared pillar that is present in this tree must have an explicit
  # rule (the '*' default alone does not count as a pillar rule).
  local p rp found
  for p in "${PILLARS[@]}"; do
    [ -e "$tree/$p" ] || continue
    found=0
    for rp in "${rule_paths[@]}"; do
      [ "$rp" = "*" ] && continue
      stripped="${rp#/}"
      stripped="${stripped%/}"
      if [ "$stripped" = "$p" ]; then
        found=1
        break
      fi
    done
    if [ "$found" -eq 0 ]; then
      echo "  FAIL  pillar '$p' is present in the tree but has no explicit CODEOWNERS rule"
      fail=1
    fi
  done

  # Every rule (other than the default '*') must name a path that exists.
  for rp in "${rule_paths[@]}"; do
    [ "$rp" = "*" ] && continue
    stripped="${rp#/}"
    stripped="${stripped%/}"
    if [ ! -e "$tree/$stripped" ]; then
      echo "  FAIL  rule names a path that does not exist in the tree: '$rp'"
      fail=1
    fi
  done

  return "$fail"
}

fail=0

echo "== codeowners: declared ownership map (issue #1073) =="

# 1. STRUCTURAL — the real repository.
out="$(compare_codeowners "$root/.github/CODEOWNERS" "$root" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "  OK  $root/.github/CODEOWNERS declares the full pillar map, no stale entries"
else
  printf '%s\n' "$out"
  echo "check-codeowners: FAIL — the live CODEOWNERS map does not hold (see FAIL lines above)" >&2
  fail=1
fi

# 2. PROVOKED — drive the SAME comparator against temp fixtures.
fixture_root="$(python3 -c 'import tempfile; print(tempfile.mkdtemp(prefix="codeowners-"))')" || exit 2
trap 'rm -rf "$fixture_root"' EXIT

# (a) valid — a subset tree with matching rules must pass.
valid_tree="$fixture_root/valid"
mkdir -p "$valid_tree/registry" "$valid_tree/gateway"
valid_file="$fixture_root/valid-CODEOWNERS"
cat > "$valid_file" <<'EOF'
*            @kushin77
/registry/   @kushin77
/gateway/    @kushin77
EOF
out="$(compare_codeowners "$valid_file" "$valid_tree" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "check-codeowners: FAIL — a VALID fixture was refused; the comparator is broken" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  fail=1
else
  echo "  OK  a VALID fixture (subset pillar map) is accepted"
fi

# (b) missing pillar rule — must be refused, naming the pillar.
missing_tree="$fixture_root/missing"
mkdir -p "$missing_tree/registry" "$missing_tree/gateway"
missing_file="$fixture_root/missing-CODEOWNERS"
cat > "$missing_file" <<'EOF'
*            @kushin77
/registry/   @kushin77
EOF
out="$(compare_codeowners "$missing_file" "$missing_tree" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "check-codeowners: FAIL — a MISSING pillar rule (gateway) was NOT detected" >&2
  fail=1
elif ! printf '%s' "$out" | grep -q "pillar 'gateway'"; then
  echo "check-codeowners: FAIL — the missing pillar was detected but NOT NAMED" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  fail=1
else
  echo "  OK  a fixture missing a pillar rule (gateway) is refused, by name:"
  printf '%s\n' "$out" | grep "pillar 'gateway'" | sed 's/^/      /'
fi

# (c) stale rule — a rule naming a path that does not exist must be refused,
# naming the path.
stale_tree="$fixture_root/stale"
mkdir -p "$stale_tree/registry"
stale_file="$fixture_root/stale-CODEOWNERS"
cat > "$stale_file" <<'EOF'
*             @kushin77
/registry/    @kushin77
/retired-pillar/  @kushin77
EOF
out="$(compare_codeowners "$stale_file" "$stale_tree" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "check-codeowners: FAIL — a STALE rule (/retired-pillar/) was NOT detected" >&2
  fail=1
elif ! printf '%s' "$out" | grep -q "retired-pillar"; then
  echo "check-codeowners: FAIL — the stale rule was detected but NOT NAMED" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  fail=1
else
  echo "  OK  a stale rule (/retired-pillar/) is refused, by name:"
  printf '%s\n' "$out" | grep "retired-pillar" | sed 's/^/      /'
fi

# (d) malformed owner — must be refused, naming the token.
malformed_tree="$fixture_root/malformed"
mkdir -p "$malformed_tree/registry"
malformed_file="$fixture_root/malformed-CODEOWNERS"
cat > "$malformed_file" <<'EOF'
*             @kushin77
/registry/    not-an-owner
EOF
out="$(compare_codeowners "$malformed_file" "$malformed_tree" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ]; then
  echo "check-codeowners: FAIL — a MALFORMED owner token was NOT detected" >&2
  fail=1
elif ! printf '%s' "$out" | grep -q "not-an-owner"; then
  echo "check-codeowners: FAIL — the malformed owner was detected but NOT NAMED" >&2
  printf '%s\n' "$out" | sed 's/^/    /' >&2
  fail=1
else
  echo "  OK  a malformed owner token is refused, by name:"
  printf '%s\n' "$out" | grep "not-an-owner" | sed 's/^/      /'
fi

[ "$fail" -eq 0 ] || exit 1
echo "check-codeowners: OK — the declared ownership map holds, and the detector is PROVEN to fire"
exit 0
