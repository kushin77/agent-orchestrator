#!/usr/bin/env bash
# Secrets gate for `make verify` (GR-6): a mechanical, always-on pattern scan
# over the repo's text files. The gate never depends on the gitleaks binary
# being installed; `.gitleaks.toml` is consumed by the optional `make gitleaks`
# target and by the pre-commit gitleaks hook. Any finding is a hard failure.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 1

failed=0
count=0

# High-signal secret shapes (never exempted — no legitimate placeholder shape).
RE_EC2KEY='-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY( BLOCK)?-----'
RE_AWS='AKIA[0-9A-Z]{16}'
RE_GHP='(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36,}'
RE_GHAPP='github_pat_[A-Za-z0-9_]{20,}'
RE_SK='(sk|sk-ant|sk-proj)-[A-Za-z0-9_\-]{16,}'
RE_GOOG='AIza[0-9A-Za-z_\-]{30,}'
RE_SLACK='xox[baprs]-[0-9A-Za-z\-]{10,}'
SHAPES="$RE_EC2KEY|$RE_AWS|$RE_GHP|$RE_GHAPP|$RE_SK|$RE_GOOG|$RE_SLACK"

# Generic secret-key assignment: key contains a secret word followed by an
# assignment with a quoted value of >= 8 chars.
RE_GEN="(api[_-]?key|secret|password|token|access[_-]?key|client[_-]?secret)[\"']?[[:space:]]*[:=][[:space:]]*[\"'][^\"']{8,}[\"']"

# Placeholder/example markers: a generic match whose line carries one of these
# is documentation, not a leak (mirrors the .gitleaks.toml allowlist).
RE_PLACE='(?i)(example|sample|placeholder|dummy|fake|changeme|replace[-_ ]?me|xxxxx|your[-_ ]?key|<[^>]{1,60}>|redacted|not[-_ ]?a[-_ ]?real)'

text_files() {
  find . -type f \( \
    -name '*.md' -o -name '*.sh' -o -name '*.py' -o -name '*.go' -o \
    -name '*.yaml' -o -name '*.yml' -o -name '*.toml' -o -name '*.json' -o \
    -name '*.txt' -o -name '*.tsv' -o -name '*.csv' -o -name 'Makefile' -o \
    -name '.gitmessage' -o -name '.gitignore' -o -name '.gitmodules' -o \
    -name '.gitleaks.toml' -o -name '.cursorrules' \) \
    -not -path './.git/*' \
    -not -path './vendor/*' \
    -not -path './.research/*' \
    | LC_ALL=C sort
}

echo "== secrets (mechanical scan) =="
while IFS= read -r f; do
  count=$((count + 1))

  # High-signal shapes: report every match.
  while IFS= read -r hit; do
    printf '  FAIL  %s (possible secret)\n' "$hit" >&2
    failed=$((failed + 1))
  done < <(grep -nHE "$SHAPES" "$f" 2>/dev/null || true)

  # Generic secret-key assignments, exempting example/placeholder values.
  while IFS= read -r hit; do
    text="${hit#*:}"
    if ! printf '%s' "$text" | grep -qiE "$RE_PLACE"; then
      printf '  FAIL  %s (possible secret)\n' "$hit" >&2
      failed=$((failed + 1))
    fi
  done < <(grep -nHE "$RE_GEN" "$f" 2>/dev/null || true)

done < <(text_files)

if [ "$failed" -ne 0 ]; then
  printf 'secrets: %s finding(s) across %s file(s)\n' "$failed" "$count" >&2
  exit 1
fi
printf 'secrets: OK (%s file(s) scanned)\n' "$count"
