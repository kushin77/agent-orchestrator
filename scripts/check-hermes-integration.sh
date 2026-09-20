#!/usr/bin/env bash
# check-hermes-integration.sh — the hermes peer-integration surface gate (issue #942).
#
# Hermes is the fleet's orchestration peer (ADR-0012); paperclip is its
# reporting peer. Until issue #942 the `integrations` surface root declared
# paperclip at `faang` and hermes nowhere, so hermes could sit below no class at
# all. This gate is the hermes half of that boundary: it holds the read-only
# projection in `integrations/hermes/` to its own contract, so a surface that is
# now declared can never sit below its declaration silently.
#
# WHAT IT CHECKS
#   1. the `check` verb conforms: the projection validates against the inline
#      CONTRACT and the cross-source consistency rules (capability-set parity,
#      tier parity, tier coverage) — tri-state 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS;
#   2. determinism: two independent `project` processes emit byte-identical
#      documents (same input -> same sha256, twice);
#   3. a mutation control: a phantom capability is planted in a scratch copy of
#      the surface; the checker must refuse it BY NAME (rc 1, naming
#      `phantom-ops`), and the copy must restore byte-identical;
#   4. the test suite passes (the negative control test included).
#
# Offline by construction: `check` and `project` are pure functions of the tree
# and never import the transport; the only network path (HttpTransport) lives in
# the `probe` verb, which this gate never calls. The adapter does import the seam
# it shares with `integrations/paperclip/` (issue #1208,
# `integrations/_seam/`), so the scratch tree below carries that package too —
# without it the mutation control would measure a missing module rather than the
# mutation.
#
# Exit-code contract (repo convention, GR-12): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

checker="integrations/hermes/cli.py"
suite="integrations/hermes/tests"

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-hermes-integration: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi
if [ ! -f "$checker" ]; then
  echo "check-hermes-integration: CANNOT-ASSESS — no hermes adapter CLI at $checker" >&2
  exit 2
fi
if ! command -v sha256sum >/dev/null 2>&1; then
  echo "check-hermes-integration: CANNOT-ASSESS — sha256sum not found (cannot prove the mutation)" >&2
  exit 2
fi

contains() { case "$1" in *"$2"*) return 0 ;; *) return 1 ;; esac }

# --- 1. the check verb is the verdict of record -----------------------------
main_out="$(python3 "$checker" --root "$root" check 2>&1)"
main_rc=$?
printf '%s\n' "$main_out"
case "$main_rc" in
  0) : ;;
  1)
    echo "check-hermes-integration: FAIL — the hermes projection is not conformant (see above)" >&2
    exit 1
    ;;
  *)
    echo "check-hermes-integration: CANNOT-ASSESS — the checker returned $main_rc" >&2
    exit 2
    ;;
esac

# --- 2. determinism: two processes, byte-identical documents -----------------
doc_a="$(python3 "$checker" --root "$root" project 2>/dev/null)"
rc_a=$?
doc_b="$(python3 "$checker" --root "$root" project 2>/dev/null)"
rc_b=$?
if [ "$rc_a" -ne 0 ] || [ "$rc_b" -ne 0 ]; then
  echo "check-hermes-integration: CANNOT-ASSESS — project failed (rc $rc_a/$rc_b)" >&2
  exit 2
fi
sha_a="$(printf '%s\n' "$doc_a" | sha256sum | awk '{print $1}')"
sha_b="$(printf '%s\n' "$doc_b" | sha256sum | awk '{print $1}')"
if [ -z "$sha_a" ] || [ "$sha_a" != "$sha_b" ]; then
  echo "check-hermes-integration: FAIL — the projection is not deterministic ($sha_a vs $sha_b)" >&2
  exit 1
fi
echo "check-hermes-integration: deterministic — sha256=$sha_a across two processes"

# --- 3. mutation control: refuse the phantom capability by name --------------
scratch="/tmp/ao942-hermes.$$.$(date +%s)"
if ! mkdir -p "$scratch"; then
  echo "check-hermes-integration: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

mkdir -p "$scratch/integrations"
cp -R "$root/integrations/hermes" "$scratch/integrations/"
cp -R "$root/integrations/_seam" "$scratch/integrations/"
for rel in \
  registry/personas/cards/hermes.yaml \
  registry/profiles/seeds/hermes.1.0.0.yaml \
  registry/profiles/catalog.yaml \
  registry/profiles/tiers.py \
  gateway/finops/tiers.yaml \
  gateway/catalog/modules/hermes/module.json; do
  mkdir -p "$scratch/$(dirname "$rel")"
  cp "$root/$rel" "$scratch/$rel"
done

persona="$scratch/registry/personas/cards/hermes.yaml"
before_sha="$(sha256sum "$persona" | awk '{print $1}')"

python3 - "$persona" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")
mutated = text.replace("  - test-run\n", "  - test-run\n  - phantom-ops\n", 1)
assert mutated != text, "the mutation did not match the capability list"
path.write_text(mutated, encoding="utf-8")
PY
mut_rc=$?
if [ "$mut_rc" -ne 0 ]; then
  echo "check-hermes-integration: CANNOT-ASSESS — the mutation did not change the fixture" >&2
  exit 2
fi
mut_sha="$(sha256sum "$persona" | awk '{print $1}')"
if [ "$mut_sha" = "$before_sha" ]; then
  echo "check-hermes-integration: CANNOT-ASSESS — the mutation left the fixture unchanged" >&2
  exit 2
fi

mut_out="$(python3 "$scratch/integrations/hermes/cli.py" --root "$scratch" check 2>&1)"
mut_rc=$?
if [ "$mut_rc" -eq 1 ] && contains "$mut_out" "phantom-ops"; then
  echo "  OK    the checker refuses its own mutant by name (phantom-ops)"
else
  echo "check-hermes-integration: FAIL — the mutation was not refused by name (rc=$mut_rc)" >&2
  printf '%s\n' "$mut_out" >&2
  exit 1
fi

# restore byte-identical (the scratch copy only; the real tree was never touched)
cp "$root/registry/personas/cards/hermes.yaml" "$persona"
restored_sha="$(sha256sum "$persona" | awk '{print $1}')"
if [ "$restored_sha" != "$before_sha" ]; then
  echo "check-hermes-integration: CANNOT-ASSESS — the restore did not return the fixture to its original bytes" >&2
  exit 2
fi
echo "  OK    the fixture is restored byte-identical (sha256=$restored_sha)"

# --- 4. the test suite passes (the negative control test included) -----------
pytest_out="$(python3 -m pytest -q "$suite" 2>&1)"
pytest_rc=$?
printf '%s\n' "$pytest_out"
if [ "$pytest_rc" -ne 0 ]; then
  echo "check-hermes-integration: FAIL — the hermes test suite did not pass (rc=$pytest_rc)" >&2
  exit 1
fi

echo "check-hermes-integration: OK"
