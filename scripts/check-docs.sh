#!/usr/bin/env bash
# Docs/foundation gate for `make verify` (GR-12): required files present,
# markdown relative links resolve, product text files are free of trailing
# whitespace, and code files carry no unfinished markers. Every branch exits
# nonzero on failure — no-false-green (fleet doctrine).
#
# Legacy extraction artifacts (MIGRATION_NOTES.md, VALIDATION.md,
# .github/workflows/*) are preserved as-is and excluded from the whitespace
# scan; they are not product docs. .github/workflows IS yaml-parsed by the
# yaml-lint check.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

fail=0

# --- 1. Required foundation + pillar files ---------------------------------
echo "== foundation files =="
required=(
  AGENTS.md CLAUDE.md .cursorrules .gitmessage .gitleaks.toml
  .pre-commit-config.yaml CONTRIBUTING.md RELEASING.md Makefile README.md
  .gitignore
  docs/ARCHITECTURE.md docs/EXECUTION-PLAN.md docs/GOVERNANCE.md docs/README.md
  registry/README.md gateway/README.md engine/README.md guardrails/README.md
  telemetry/README.md identity/README.md control-plane/README.md portal/README.md
  infra/README.md
)
for f in "${required[@]}"; do
  if [ -f "$f" ]; then
    printf '  OK    %s\n' "$f"
  else
    printf '  FAIL  %s (missing)\n' "$f" >&2
    fail=$((fail + 1))
  fi
done

# --- 2. Markdown relative-link resolution ----------------------------------
echo "== markdown links =="
md_fail=0
while IFS= read -r md; do
  dir="$(dirname "$md")"
  while IFS= read -r target; do
    [ -z "$target" ] && continue
    case "$target" in
      http://*|https://*|mailto:*|ftp://*|tel:*|data:*|irc:*|\#*) continue ;;
    esac
    # strip inline title, then any fragment
    target="${target%% *}"
    target="${target%%\"*}"
    target="${target%%#*}"
    [ -z "$target" ] && continue
    if [ -e "$dir/$target" ]; then
      :
    else
      printf '  FAIL  %s -> %s (not found)\n' "$md" "$target" >&2
      md_fail=$((md_fail + 1))
    fi
  done < <(grep -oE '\]\([^)]*\)' "$md" | sed -E 's/^\]\((.*)\)$/\1/')
done < <(find . -type f -name '*.md' \
  -not -path './.git/*' \
  -not -path './vendor/*' \
  -not -path './.research/*' \
  | LC_ALL=C sort)
if [ "$md_fail" -ne 0 ]; then
  printf 'markdown links: %s broken link(s)\n' "$md_fail" >&2
  fail=$((fail + md_fail))
else
  echo "markdown links: OK"
fi

# --- 3. Trailing whitespace (product text files) ---------------------------
echo "== trailing whitespace =="
ws_fail=0
while IFS= read -r f; do
  if grep -qE '[[:blank:]]+$' "$f"; then
    printf '  FAIL  %s (trailing whitespace)\n' "$f" >&2
    ws_fail=$((ws_fail + 1))
  fi
done < <(find . -type f \( \
  -name '*.md' -o -name '*.sh' -o -name '*.py' -o -name '*.yaml' -o \
  -name '*.yml' -o -name '*.toml' -o -name 'Makefile' -o \
  -name '.gitmessage' -o -name '.cursorrules' \) \
  -not -path './.git/*' \
  -not -path './vendor/*' \
  -not -path './.research/*' \
  -not -path './MIGRATION_NOTES.md' \
  -not -path './VALIDATION.md' \
  -not -path './.github/*' \
  | LC_ALL=C sort)
if [ "$ws_fail" -ne 0 ]; then
  printf 'trailing whitespace: %s file(s) FAILED\n' "$ws_fail" >&2
  fail=$((fail + ws_fail))
else
  echo "trailing whitespace: OK"
fi

# --- 4. Unfinished markers in code -----------------------------------------
echo "== unfinished markers in code =="
mk_fail=0
mk_pattern="(TO""DO|FIX""ME|HA""CK|XX""X)\\b"
while IFS= read -r f; do
  if grep -qE "$mk_pattern" "$f"; then
    printf '  FAIL  %s (unfinished marker)\n' "$f" >&2
    mk_fail=$((mk_fail + 1))
  fi
done < <(find . -type f \( -name '*.sh' -o -name '*.py' -o -name '*.go' \) \
  -not -path './.git/*' \
  -not -path './vendor/*' \
  -not -path './.research/*' \
  | LC_ALL=C sort)
if [ "$mk_fail" -ne 0 ]; then
  printf 'unfinished markers: %s file(s) FAILED\n' "$mk_fail" >&2
  fail=$((fail + mk_fail))
else
  echo "unfinished markers: OK"
fi

# --- Summary ----------------------------------------------------------------
if [ "$fail" -ne 0 ]; then
  printf 'docs: %s problem(s)\n' "$fail" >&2
  exit 1
fi
echo "docs: OK"
