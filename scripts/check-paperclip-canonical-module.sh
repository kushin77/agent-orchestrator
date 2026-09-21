#!/usr/bin/env bash
# check-paperclip-canonical-module.sh — the canonical-home guard for the
# paperclip boundary adapter (issue #448; ADR-0012 / ADR-0013).
#
# The paperclip boundary adapter has exactly one canonical home: the module
# `integrations/paperclip/` (transport-seamed `client.py`, deterministic
# `mapping.py`; landed by issue #428, PR #433, commit 9cd5794). The lane briefs
# written before that module landed named paths that never existed —
# `paperclip/auth/**` (#412), `paperclip/api/**` (#413),
# `paperclip/adapters/<name>/**` (#414-#419) and `paperclip/reporting/**`
# (#447). A lane that follows one of those briefs verbatim builds a *second*
# adapter module beside the real one — the duplicate-module failure the
# `#307`/`#304` collision already paid for, and the half-coupling ADR-0012
# forbids (two adapter implementations for one boundary).
#
# A convention a brief asks a human to remember is not a guard (no-false-green
# doctrine, AO-GR-4/GR-12). This check fails, BY NAME, when a second top-level
# paperclip module appears:
#
#   * the canonical module `integrations/paperclip/` must exist and carry its
#     `mapping.py` and `client.py`;
#   * a repo-root `paperclip/` tree is refused *unconditionally* — issue #457
#     consolidated the former declared EPIC #410 tree under
#     `integrations/paperclip/adapters/`, so the sibling escape hatch is gone;
#   * any `paperclip/` directory elsewhere in the tree is refused when it is a
#     module (carries `__init__.py`); schema/catalog companions that are not
#     modules are named and passed;
#   * a top-level directory (or `integrations/*`) matching `*paperclip*` other
#     than `integrations/paperclip` is refused;
#   * a `paperclip/{auth,api,adapters,reporting}` path is refused, naming the
#     canonical path the brief should have used instead of just "fail".
#
# Before #457 the guard reported a `paperclip/` tree carrying the EPIC #410
# marker as a KNOWN sibling (a pass). That tree is now the *duplicate* and is
# refused by name, so this check is strictly stronger than the one it replaces:
# there is exactly one home, `integrations/paperclip/` (issue #457, ADR-0016).
#
# It also runs a self-mutating negative control: it builds a clean throwaway
# root containing only the canonical module, asserts the checker is GREEN, then
# provokes it twice — (A) a decoy `paperclip/auth/x.py` (a briefed-but-wrong
# glob) and (B) the former *declared sibling* shape, a `paperclip/` module whose
# `__init__.py` names EPIC #410 — asserting for each that the enumerated input
# CHANGED (sha256) and that the checker refuses it BY NAME. It removes the
# provocations, asserts the enumeration hashes identically and the checker is
# GREEN again. If a mutant passes, this gate prints FAIL and exits non-zero — a
# check that cannot fail is a formality. The main verdict is propagated after the
# control runs, so a passing control can never mask a failing main check (until
# issue #457 the script ended `exit 0` unconditionally: a duplicate module
# printed FAIL and still exited 0).
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS. CANNOT-ASSESS never
# exits 0, and a missing canonical module is never a PASS.
#
# Usage:
#   bash scripts/check-paperclip-canonical-module.sh
#   bash scripts/check-paperclip-canonical-module.sh --root DIR
#   bash scripts/check-paperclip-canonical-module.sh --enumerate
#   bash scripts/check-paperclip-canonical-module.sh --no-controls
#
# ---knowledge---
# module_id: scripts.check-paperclip-canonical-module
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, named-refusal, deterministic, schema-validation]
# derives_from: null
# owner_sme: platform-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#304", "#307", "#410", "#412"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
scan_root="$root"
mode="check"
controls=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --root) scan_root="${2:-}"; shift 2 ;;
    --enumerate) mode="enumerate"; controls=0; shift ;;
    --no-controls) controls=0; shift ;;
    -h|--help) sed -n '2,58p' "$0"; exit 0 ;;
    *) printf 'check-paperclip-canonical-module: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-paperclip-canonical-module: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -d "$scan_root" ]; then
  echo "check-paperclip-canonical-module: CANNOT-ASSESS — root not found: $scan_root" >&2
  exit 2
fi

# inspect <root> <mode> — 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# mode=check     prints findings and returns the tri-state verdict.
# mode=enumerate prints one `ENUM <path>` line per candidate considered, sorted,
#                so a caller can prove that a mutation changed the input.
inspect() {
  python3 - "$@" <<'PY'
import os
import sys
from pathlib import Path

CANONICAL = "integrations/paperclip"
CANONICAL_FILES = ("mapping.py", "client.py")
BRIEFED_CONCERNS = ("auth", "api", "adapters", "reporting")
SKIP_DIRS = {".git", "vendor", ".research", "node_modules", ".venv", "__pycache__"}

root = Path(sys.argv[1]).resolve()
mode = sys.argv[2]


def rel(path):
    return str(path.relative_to(root)).replace(os.sep, "/")


def paperclip_dirs():
    found = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in list(dirnames):
            if name == "paperclip":
                found.append(Path(dirpath) / name)
    return sorted(found, key=lambda p: rel(p))


integrations = root / "integrations"
canonical = integrations / "paperclip"

if not integrations.is_dir():
    sys.stderr.write(
        "check-paperclip-canonical-module: CANNOT-ASSESS — no integrations/ tree under %s\n"
        % root
    )
    raise SystemExit(2)

offenses = []
scanned = []


def add_offense(path, reason):
    for existing, _ in offenses:
        if existing == path:
            return
    offenses.append((path, reason))


def note_scan(path):
    if path not in scanned:
        scanned.append(path)


# --- the canonical module must exist and carry its identity files ------------
if not canonical.is_dir():
    add_offense(
        CANONICAL,
        "the canonical module is missing — the boundary adapter has no home",
    )
else:
    for name in CANONICAL_FILES:
        if not (canonical / name).is_file():
            add_offense(
                "%s/%s" % (CANONICAL, name),
                "the canonical module is missing a required file",
            )

# --- (1) top-level entries matching *paperclip* other than integrations/ -----
for name in sorted(os.listdir(root)):
    path = root / name
    if not path.is_dir():
        continue
    if name == "integrations":
        continue
    if "paperclip" not in name:
        continue
    path_rel = rel(path)
    note_scan(path_rel)
    if name == "paperclip":
        add_offense(
            path_rel,
            "a second top-level paperclip module beside %s — the former EPIC "
            "#410 tree was consolidated under %s/adapters/ by issue #457"
            % (CANONICAL, CANONICAL),
        )
    else:
        add_offense(
            path_rel,
            "a top-level directory matching *paperclip* that is not %s" % CANONICAL,
        )

# --- (2) integrations/* other than integrations/paperclip --------------------
for name in sorted(os.listdir(integrations)):
    if name == "paperclip":
        note_scan(CANONICAL)
        continue
    if "paperclip" in name:
        path_rel = rel(integrations / name)
        note_scan(path_rel)
        add_offense(path_rel, "a sibling paperclip module under integrations/")

# --- (3) a paperclip/ directory elsewhere: module vs companion ----------------
for path in paperclip_dirs():
    path_rel = rel(path)
    if path_rel == CANONICAL or path_rel.startswith(CANONICAL + "/"):
        continue
    note_scan(path_rel)
    if not (path / "__init__.py").is_file():
        # a data / companion directory (seam schemas, catalog rows), not a module
        continue
    add_offense(
        path_rel,
        "a second paperclip module — there is one home, %s/" % CANONICAL,
    )

# --- (4) the briefed-but-wrong globs, refused by name ------------------------
# Only paperclip directories that are NOT the canonical module (or beneath it)
# are scanned: the canonical module legitimately owns
# `integrations/paperclip/adapters/<family>/` and `integrations/paperclip/<concern>.py`,
# so it must never be refused for owning one. A `paperclip/{auth,api,adapters,
# reporting}` path anywhere else is a stale-brief artifact and is refused by
# name — there is no longer any declared sibling to exempt.
for path in paperclip_dirs():
    path_rel = rel(path)
    if path_rel == CANONICAL or path_rel.startswith(CANONICAL + "/"):
        continue
    for concern in BRIEFED_CONCERNS:
        candidate = path / concern
        if not candidate.exists():
            continue
        note_scan(rel(candidate))
        add_offense(
            rel(candidate),
            "a briefed-but-wrong paperclip/<concern> path; extend %s instead "
            "(%s/adapters/<family>/ or %s/<concern>.py)"
            % (CANONICAL, CANONICAL, CANONICAL),
        )

scanned = sorted(set(scanned))

if mode == "enumerate":
    for path in scanned:
        print("ENUM %s" % path)
    raise SystemExit(0)

if offenses:
    for path, reason in offenses:
        print("  FAIL  %s — %s" % (path, reason), file=sys.stderr)
    raise SystemExit(1)

print(
    "  OK    canonical module present: %s/ (%s)"
    % (CANONICAL, ", ".join(CANONICAL_FILES))
)
print(
    "  OK    no second paperclip module — %s/ is the sole home "
    "(%d candidate(s) checked)" % (CANONICAL, len(scanned))
)
raise SystemExit(0)
PY
}

if [ "$mode" = "enumerate" ]; then
  inspect "$scan_root" enumerate || exit $?
  exit 0
fi

rc_main=0
inspect "$scan_root" check || rc_main=$?

case "$rc_main" in
  0) : ;;
  1)
    echo "check-paperclip-canonical-module: FAIL — a second top-level module for the" >&2
    echo "  paperclip boundary adapter is present." >&2
    echo "  canonical home : integrations/paperclip/ (mapping.py + client.py;" >&2
    echo "                   gate scripts/check-paperclip-integration-adapter.sh)" >&2
    echo "  briefed-but-wrong globs (never legitimate):" >&2
    echo "    paperclip/auth/**, paperclip/api/**, paperclip/reporting/**," >&2
    echo "    paperclip/adapters/<name>/**" >&2
    echo "  fix            : extend integrations/paperclip/ — as" >&2
    echo "                   integrations/paperclip/<concern>.py," >&2
    echo "                   integrations/paperclip/<concern>/ or" >&2
    echo "                   integrations/paperclip/adapters/<family>/." >&2
    echo "  note           : the former EPIC #410 paperclip/ tree is now a" >&2
    echo "                   duplicate; issue #457 moved it under integrations/paperclip/adapters/." >&2
    ;;
  *)
    echo "check-paperclip-canonical-module: CANNOT-ASSESS — inspector returned $rc_main" >&2
    exit 2
    ;;
esac

if [ "$controls" -eq 0 ]; then
  exit "$rc_main"
fi

# --- self-mutating negative control ------------------------------------------
# Build a clean throwaway root holding ONLY the canonical module: the checker
# must be GREEN. Provoke it twice: (A) a decoy module paperclip/auth/x.py and
# (B) the former *declared sibling* shape (a paperclip/ module naming EPIC #410).
# For each, the enumerated input must change (sha256) AND the checker must
# refuse it by name. Remove the provocations: the enumeration must hash
# identically and the checker must be GREEN again.
scratch="/tmp/ao457.$$.$(date +%s)"
if ! mkdir "$scratch" 2>/dev/null; then
  echo "check-paperclip-canonical-module: CANNOT-ASSESS — cannot create a scratch directory" >&2
  exit 2
fi
trap 'rm -rf "$scratch"' EXIT

mut="$scratch/root"
mkdir -p "$mut/integrations/paperclip"
for f in mapping.py client.py; do
  if [ -f "$root/integrations/paperclip/$f" ]; then
    cp "$root/integrations/paperclip/$f" "$mut/integrations/paperclip/$f"
  else
    printf '"""decoy stand-in for %s"""\n' "$f" > "$mut/integrations/paperclip/$f"
  fi
done

enum_sha() {
  inspect "$1" enumerate | sha256sum | awk '{print $1}'
}

before_sha="$(enum_sha "$mut")"
if [ -z "$before_sha" ]; then
  echo "check-paperclip-canonical-module: CANNOT-ASSESS — the clean root produced no enumeration" >&2
  exit 2
fi

clean_out="$(inspect "$mut" check 2>&1)"
clean_rc=$?
if [ "$clean_rc" -ne 0 ]; then
  echo "check-paperclip-canonical-module: CANNOT-ASSESS — the clean control root is not GREEN (rc=$clean_rc)" >&2
  printf '%s\n' "$clean_out" >&2
  exit 2
fi

# mutation: a decoy second module exactly as a stale brief would build it
mkdir -p "$mut/paperclip/auth"
printf 'x = 1\n' > "$mut/paperclip/auth/x.py"

after_sha="$(enum_sha "$mut")"
if [ "$after_sha" = "$before_sha" ]; then
  echo "check-paperclip-canonical-module: CANNOT-ASSESS — the mutation did not change the enumerated input" >&2
  exit 2
fi

mut_out="$(inspect "$mut" check 2>&1)"
mut_rc=$?
if [ "$mut_rc" -eq 1 ] && printf '%s\n' "$mut_out" | grep -qF "paperclip/auth"; then
  echo "  OK    negative control A: the decoy paperclip/auth/x.py is refused by name"
  echo "        enumerated input changed: $before_sha -> $after_sha"
else
  echo "check-paperclip-canonical-module: FAIL — the decoy paperclip/auth/x.py was not refused by name" >&2
  printf '%s\n' "$mut_out" >&2
  exit 1
fi

# mutation B: the former *declared sibling* shape — a paperclip/ module whose
# __init__.py names EPIC #410. Before #457 this was reported as KNOWN (a pass);
# it must now be refused by name, proving the check is strictly stronger.
rm -rf "$mut/paperclip"
mkdir -p "$mut/paperclip/adapters"
printf '"""Paperclip-ing parity adapters (EPIC #410)."""\n' > "$mut/paperclip/__init__.py"
printf '"""adapters package."""\n' > "$mut/paperclip/adapters/__init__.py"
b_sha="$(enum_sha "$mut")"
if [ "$b_sha" = "$before_sha" ]; then
  echo "check-paperclip-canonical-module: CANNOT-ASSESS — mutation B did not change the enumerated input" >&2
  exit 2
fi
b_out="$(inspect "$mut" check 2>&1)"
b_rc=$?
if [ "$b_rc" -eq 1 ] && printf '%s\n' "$b_out" | grep -qF "paperclip"; then
  echo "  OK    negative control B: the former EPIC #410 declared sibling is refused by name"
  echo "        enumerated input changed: $before_sha -> $b_sha"
else
  echo "check-paperclip-canonical-module: FAIL — the former declared sibling (paperclip/ naming EPIC #410) was not refused by name" >&2
  printf '%s\n' "$b_out" >&2
  exit 1
fi

rm -rf "$mut/paperclip"
restore_sha="$(enum_sha "$mut")"
if [ "$restore_sha" != "$before_sha" ]; then
  echo "check-paperclip-canonical-module: FAIL — the enumeration is not stable across the control" >&2
  echo "  before=$before_sha" >&2
  echo "  after =$restore_sha" >&2
  exit 1
fi

restore_out="$(inspect "$mut" check 2>&1)"
restore_rc=$?
if [ "$restore_rc" -ne 0 ]; then
  echo "check-paperclip-canonical-module: FAIL — the control root did not return to GREEN after restore" >&2
  printf '%s\n' "$restore_out" >&2
  exit 1
fi
echo "  OK    control root restored byte-identical (enumeration sha256 $before_sha)"

# Propagate the MAIN verdict. The self-mutating control runs on its own scratch
# root and is independent of the main check, so a passing control must never mask
# a failing main check (that was a false green until issue #457: the script ended
# `exit 0` unconditionally, so a duplicate module printed FAIL and still exited 0).
if [ "$rc_main" -ne 0 ]; then
  exit "$rc_main"
fi

echo "check-paperclip-canonical-module: OK — integrations/paperclip/ is the sole"
echo "  canonical module for the paperclip boundary adapter; no second paperclip"
echo "  module — the former EPIC #410 sibling and the decoy briefed glob are both"
echo "  refused by name."
exit 0
