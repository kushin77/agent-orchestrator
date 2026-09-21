#!/usr/bin/env bash
# check-cockpit.sh — the terminal cockpit CLIENT renders exactly the declared
# RC-10 function set, headlessly, and refuses to start while its surface is off
# (issue #1162, RC-11 of EPIC #551).
#
# WHY THIS GATE EXISTS
#
# `control-plane/cockpit/cockpit/` is a shipped, runnable operator client
# (`make cockpit`, documented in `control-plane/cockpit/README.md`) whose *frame*
# half was gated and whose *client* half was not: `scripts/check-control-functions.sh`
# proves the RC-10 registry and `fleet/console.py`'s sections, and the cockpit
# suite proves the modules through their injection seams -- but nothing drove the
# shipped entry point `control-plane/cockpit/cockpit/__main__.py` end to end.
# This gate does exactly that, and nothing else. It deliberately does NOT re-run
# `control-plane/cockpit/tests` (which `scripts/verify.sh` already runs as the
# `pytest-cockpit` check and `scripts/pytest-suites.txt` declares), so the suite
# is not paid for twice.
#
# WHAT IT PROVES, IN FOUR PARTS
#
#   1. THE SHIPPED STATE IS HONEST. In this repository `--once` exits 2 with the
#      named `FLAG_OFF` condition, because `surfaces.cockpit` defaults to off
#      (`infra/feature-flags/registry.yaml`). That is the declared contract, not a
#      defect: an unpromoted surface must be absent and must NAME the flag. Once a
#      lane promotes the surface the same check requires a rendered frame instead.
#      Either way a bare rc 2 with no named reason is a FAILURE.
#   2. THE CLIENT RENDERS THE DECLARED SET, HEADLESSLY. In a scratch copy with the
#      surface promoted, the shipped entry point renders one frame per role (and
#      the documented default role) with no TTY, no stdin and its own session, and
#      the panels it renders are EXACTLY the function set RC-10's own
#      `render_workspace` declares for that role -- no declared function missing,
#      none invented, none rendered twice, and no panel reading FAILED. A client
#      that cannot render what the registry declares is refused BY NAME.
#   3. THE FLAG GATE FAILS CLOSED. With the flag registry removed the client must
#      still exit 2 naming `FLAG_OFF` -- never 0 with a frame (GR-5 / AO-GR-6).
#   4. IT REFUSES ITS OWN DEFECTS. Six defects are provoked, each in the scratch
#      copy: a client that drops a declared panel, one that renders an undeclared
#      panel, one that exits 0 with no frame (a silent success), one whose fixture
#      seam is broken (FAILED panels), one whose flag reader fails OPEN, and one
#      that skips its startup flag gate entirely. Each is REQUIRED to be refused
#      by name -- so a pass here cannot be vacuous (GR-12: a gate that cannot fail
#      is a formality). Every mutation is proven to have LANDED (sha256 before and
#      after) before its verdict is trusted, and the repository's own files are
#      proven unchanged: `control-plane/cockpit` byte-for-byte, and the run itself
#      proven to have added NOTHING to the tree's dirty set -- a SAME-TREE DELTA
#      against the tree it started from, never the tree's absolute state, because
#      an absolute assertion's verdict depends on what happened to run before it
#      in the same worktree (issues #1162 + #1313, PR #1306). The set
#      `governance/isolation/worktree.py` DECLARES machine-managed
#      (`MACHINE_MANAGED_PATHS` / `MACHINE_MANAGED_PREFIXES` -- `.board/focus.json`
#      is rewritten by the board machinery, never by the client) is excluded from
#      that delta, read from that one authority so this gate cannot disagree with
#      the worktree reaper (#1285). The client is a client (ADR-0026 D5/D6) and
#      writes no fleet state.
#
# The scratch copy is the repository minus VCS, the pinned submodule, the
# research clones and the runtime state peers regenerate -- the same venue
# `scripts/check-control-functions.sh` uses. The child's environment has the
# caller's fleet/session/plane variables REMOVED, so a neighbour lane's live
# fleet cannot decide this gate's frame, and a secret in the environment cannot
# reach it.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS (no python3, no PyYAML,
# no sha256sum, a scratch directory that cannot be created, not a git checkout, an
# unreadable machine-managed declaration). CANNOT-ASSESS never reads as a pass.
#
# Usage: bash scripts/check-cockpit.sh
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

FAILED=0
TMPD=""
cleanup() { [ -n "$TMPD" ] && rm -rf "$TMPD" || true; }
trap cleanup EXIT

say() { printf '%s\n' "$*"; }
cannot_assess() {
  printf 'check-cockpit: CANNOT-ASSESS -- %s\n' "$*" >&2
  exit 2
}

# --- the declared machine-managed set, from its ONE source -------------------
# `.board/focus.json` is in `governance/isolation/worktree.py`'s
# `MACHINE_MANAGED_PATHS`: it is rewritten by the board machinery, not by the
# cockpit client, and a concurrent rewrite during this run is indistinguishable
# from one this run caused. Reading the declaration from that single authority
# (the one the worktree reaper consumes, #1285) means this gate cannot disagree
# with the reaper about what is machine-owned, and narrowing the declaration
# narrows this gate with it.
machine_managed_declaration() {
  python3 - "$root" <<'PYEOF'
import sys

sys.path.insert(0, sys.argv[1])
try:
    from governance.isolation import worktree
except Exception as exc:  # noqa: BLE001 - an unreadable authority is CANNOT-ASSESS
    print("governance/isolation/worktree.py could not be imported: %s" % (exc,), file=sys.stderr)
    raise SystemExit(2)
for declared in worktree.MACHINE_MANAGED_PATHS:
    print("P\t%s" % declared)
for declared in worktree.MACHINE_MANAGED_PREFIXES:
    print("X\t%s" % declared)
PYEOF
}

mm_paths=()
mm_prefixes=()
if ! mm_declaration="$(machine_managed_declaration)"; then
  cannot_assess "the declared machine-managed set is unreadable (governance/isolation/worktree.py)"
fi
while IFS=$'\t' read -r mm_kind mm_value; do
  [ -n "$mm_value" ] || continue
  case "$mm_kind" in
    P) mm_paths+=("$mm_value") ;;
    X) mm_prefixes+=("$mm_value") ;;
  esac
done <<< "$mm_declaration"

is_machine_managed() {
  local candidate="$1" declared
  for declared in ${mm_paths[@]+"${mm_paths[@]}"}; do
    [ "$candidate" = "$declared" ] && return 0
  done
  for declared in ${mm_prefixes[@]+"${mm_prefixes[@]}"}; do
    case "$candidate" in "$declared"*) return 0 ;; esac
  done
  return 1
}

# snapshot_tree <out-prefix> -- the tree's dirty set as two comparable files:
#   <out>.S        one porcelain line per dirty/untracked path this gate must not move
#   <out>.H        "<path>\t<sha256>" for each of those paths that exists on disk
#   <out>.skipped  how many dirty paths were skipped as declared machine-managed
# The hash half is load-bearing: porcelain v1 reports ` M` both for a file that was
# already dirty on entry and for one this run dirtied, so a status line alone
# cannot see a second write to an already-dirty file. The two files are kept APART
# because the two findings are different questions -- a path in only the later set
# is a creation, a path in both can only be a rewrite -- and one combined file
# cannot tell them apart: a created file has no prior hash to compare with, so its
# own hash line would read as "changed". The delta is one-directional (what the run
# ADDED); a concurrent host writer reverting pre-existing state is not this gate's
# finding, and the whole-tree comparison this replaced could not tell the two apart
# at all. A path porcelain QUOTES (a space, a non-ASCII byte) cannot match the
# declared set and is therefore reported: the uncertain direction is loud, silent is
# the unsafe one.
snapshot_tree() {
  local out="$1" line path skipped=0
  : > "$out.S"
  : > "$out.H"
  git status --porcelain -uall 2>/dev/null \
    | grep -v -E '^[?][?] scripts/check-cockpit[.]sh$' > "$out.raw" || true
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    path="${line:3}"
    if is_machine_managed "$path"; then
      skipped=$((skipped + 1))
      continue
    fi
    printf '%s\n' "$line" >> "$out.S"
    [ -f "$path" ] || continue
    printf '%s\t%s\n' "$path" "$(sha256sum -- "$path" | cut -d' ' -f1)" >> "$out.H"
  done < "$out.raw"
  printf '%s\n' "$skipped" > "$out.skipped"
}

# --- preconditions ----------------------------------------------------------
command -v python3 >/dev/null 2>&1 || cannot_assess "python3 not found"
command -v sha256sum >/dev/null 2>&1 \
  || cannot_assess "sha256sum not found (the controls prove their own mutations land)"
python3 -c 'import yaml' >/dev/null 2>&1 \
  || cannot_assess "PyYAML is not importable, so the RC-10 registry cannot be read"
# The final assertion measures the tree's dirty set before and after the run, so a
# tree git cannot read is a CANNOT-ASSESS, never a pass: failing open here is how
# the assertion would silently stop existing (GR-12).
git rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || cannot_assess "this is not a git checkout, so the run's effect on the tree cannot be measured"

entry="control-plane/cockpit/cockpit/__main__.py"
for required in \
  "$entry" \
  control-plane/cockpit/cockpit/cli.py \
  control-plane/cockpit/cockpit/app.py \
  control-plane/cockpit/cockpit/flags.py \
  control-plane/cockpit/cockpit/registry.py \
  control-plane/cockpit/README.md \
  control-plane/functions/functions.yaml \
  control-plane/functions/cockpit_registry.py \
  control-plane/functions/cockpit_render.py \
  infra/feature-flags/registry.yaml; do
  [ -e "$required" ] || cannot_assess "$required is missing"
done

echo "== check-cockpit $(date -Is) =="
echo "   root        $root"
echo "   head        $(git rev-parse --short HEAD 2>/dev/null || echo '(not a git checkout)')"
echo "   python3     $(python3 -V 2>&1)"

# The real client's bytes, measured before anything runs: the controls below act
# on a scratch copy, and this is what proves it.
client_before="$(find control-plane/cockpit -type f -not -name '*.pyc' -print0 \
  | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"

# --- the scratch venue ------------------------------------------------------
# An explicit template, never mktemp's own default: the shared $TMPDIR is not
# private and a scratch directory there can vanish mid-run (this repo's SP-9).
TMPD="${TMPDIR:-/tmp}/cockpit.$$.$(date +%s)"
work="$TMPD"
repo="$work/repo"
mkdir -p "$repo" 2>/dev/null || cannot_assess "cannot create a scratch directory under $work"

# The tree this gate must not move, measured before the first driver runs. An
# earlier check in the composite gate legitimately dirties tracked files in the
# SAME worktree -- the fleet/brain suite rewrites `.board/focus.json` -- so the
# assertion at the end compares against THIS, never the tree's absolute state.
snapshot_tree "$work/tree.before" || true

while IFS= read -r scratch_entry; do
  case "$scratch_entry" in
    ./.git|.git|./.git/*|./vendor|vendor|./vendor/*) continue ;;
    ./.research|./.research/*|./.fleet|./.fleet/*|./.board|./.board/*) continue ;;
    ./.*) continue ;;
  esac
  cp -a "$scratch_entry" "$repo/" 2>/dev/null \
    || cannot_assess "cannot copy $scratch_entry into the scratch tree"
done < <(find . -maxdepth 1 -mindepth 1 | LC_ALL=C sort)

# A stale bytecode cache can shadow the module under test; the scratch tree
# starts with none and every invocation below writes none.
find "$repo" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find "$repo" -name '*.pyc' -delete 2>/dev/null
[ -f "$repo/$entry" ] || cannot_assess "the scratch tree is incomplete ($entry absent)"

flag_registry="$repo/infra/feature-flags/registry.yaml"
[ -f "$flag_registry" ] || cannot_assess "the scratch tree is incomplete ($flag_registry absent)"

# --- the driver ------------------------------------------------------------
cat > "$work/drive.py" <<'PYEOF'
"""check-cockpit's driver: run the shipped cockpit entry point, read the frame.

Every invocation is headless by construction -- stdin is /dev/null, stdout and
stderr are pipes, and the child gets its own session -- so a frame that only
renders on a TTY is refused here rather than passing on the box that has one.
The declared function set is read from the RC-10 authority
(`control-plane/functions/cockpit_registry.py`), never from the client, so the
comparison has two independent sides.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True

#: The frame's panel marker, read exactly as `control-plane/cockpit/tests/conftest.py`
#: reads it (`panel_ids`), so the gate and the suite cannot disagree on the shape.
PANEL_RE = re.compile("\u250c\u2500 ([A-Z][A-Z0-9]{0,11}) ")

ENTRY = "control-plane/cockpit/cockpit/__main__.py"
FUNCTIONS = "control-plane/functions/functions.yaml"
FLAG_OFF = "FLAG_OFF"

#: The caller's environment must not decide this gate's frame: the fleet dir
#: points at another checkout's live state, and the session token is a secret.
SCRUB = ("AO_FLEET_DIR", "AO_CONTROL_SESSION", "AO_CONTROL_PLANE")

EXIT_OK, EXIT_NOT_OK, EXIT_CANNOT_ASSESS = 0, 1, 2


def fail(finding):
    print(f"check-cockpit: FAIL \u2014 {finding}")
    return EXIT_NOT_OK


def ok(finding):
    print(f"check-cockpit: OK \u2014 {finding}")
    return EXIT_OK


def child_env():
    env = dict(os.environ)
    for key in SCRUB:
        env.pop(key, None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_client(repo, argv):
    """One headless invocation of the shipped entry point: (rc, stdout, stderr)."""
    proc = subprocess.run(
        [sys.executable, ENTRY, *argv],
        cwd=repo,
        env=child_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        timeout=180,
    )
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def declared(repo, role):
    """(registry, the role's declared function ids) from the RC-10 authority."""
    functions = Path(repo, "control-plane", "functions")
    if str(functions) not in sys.path:
        sys.path.insert(0, str(functions))
    import cockpit_registry as authority

    registry = authority.load(Path(repo, FUNCTIONS))
    return registry, list(authority.render_workspace(registry, role))


def first_failed_line(frame):
    for line in frame.splitlines():
        if "FAILED" in line:
            return line.strip()
    return ""


def check_frame(repo, role, argv, label):
    """The core assertion: a rendered frame is EXACTLY the declared set."""
    try:
        rc, out, err = run_client(repo, argv)
    except subprocess.TimeoutExpired:
        return fail(f"the client did not finish within 180s ({label})")
    if rc != 0:
        return fail(
            f"the client did not render a frame headlessly ({label}: rc {rc}); "
            f"stdout={out.strip()!r} stderr={err.strip()!r}"
        )
    frame = out
    if not frame.strip():
        return fail(
            f"the client exited 0 with an empty frame \u2014 a silent success is "
            f"never a pass ({label})"
        )
    failed_line = first_failed_line(frame)
    if failed_line:
        return fail(
            f"the frame renders FAILED for a panel \u2014 a panel that cannot load "
            f"must name its reason, never read as data ({label}: {failed_line})"
        )
    try:
        registry, workspace = declared(repo, role)
    except Exception as exc:  # noqa: BLE001 - an unreadable authority is CANNOT-ASSESS
        print(f"check-cockpit: CANNOT-ASSESS \u2014 the RC-10 registry is unreadable: {exc}")
        return EXIT_CANNOT_ASSESS
    if not workspace:
        return fail(
            f"the declared function set for role {role} is empty \u2014 nothing to "
            f"render is not a pass ({label})"
        )
    rendered = PANEL_RE.findall(frame)
    seen = set(rendered)
    declared_ids = set(registry.functions)
    extra = sorted(seen - declared_ids)
    if extra:
        return fail(
            f"the client renders {extra[0]!r}, which the RC-10 registry does not "
            f"declare ({label})"
        )
    missing = sorted(set(workspace) - seen)
    if missing:
        return fail(
            f"the client does not render declared function {missing[0]!r} "
            f"({label})"
        )
    duplicated = sorted({one for one in rendered if rendered.count(one) > 1})
    if duplicated:
        return fail(f"the client renders {duplicated[0]!r} twice ({label})")
    return ok(
        f"{label}: rc 0 headless, {len(rendered)} panel(s), exactly the "
        f"{len(workspace)} declared function(s) of role {role}"
    )


def cmd_frames(args):
    role = args.role
    argv = ["--once"] if args.default else ["--once", "--role", role]
    label = "the documented default invocation (`--once`)" if args.default \
        else f"role {role}"
    return check_frame(args.repo, role, argv, label)


def cmd_gated(args):
    """The startup flag gate: off (or absent) refuses BY NAME, never renders."""
    try:
        rc, out, err = run_client(args.repo, ["--once"])
    except subprocess.TimeoutExpired:
        return fail("the client did not finish within 180s (flag gate)")
    if args.expect == "off":
        if rc == 0:
            return fail(
                "the cockpit rendered a frame while its flag is not on \u2014 the "
                "flag gate must fail closed (GR-5 / AO-GR-6)"
            )
        if rc != 2:
            return fail(
                f"a flag-off cockpit must exit 2 (CANNOT-ASSESS); it exited {rc}"
            )
        if FLAG_OFF not in out:
            return fail(
                "rc 2 without the named FLAG_OFF condition \u2014 a CANNOT-ASSESS "
                "must name its reason"
            )
        return ok("flag off/absent: rc 2, the named FLAG_OFF condition, no frame")
    # auto: the tree as shipped -- either honest state is acceptable, named.
    if rc == 2 and FLAG_OFF in out:
        return ok("shipped state: surfaces.cockpit is off, named FLAG_OFF, rc 2")
    if rc == 0 and out.strip():
        return check_frame(args.repo, "Analyst", ["--once"], "shipped state (promoted)")
    return fail(
        f"the shipped invocation is neither a named FLAG_OFF refusal nor a "
        f"rendered frame (rc {rc}; stdout={out.strip()!r} stderr={err.strip()!r})"
    )


def cmd_mnemonic(args):
    """One declared function, headlessly, without sending anything."""
    try:
        rc, out, err = run_client(args.repo, ["--dry-run", args.mnemonic])
    except subprocess.TimeoutExpired:
        return fail("the client did not finish within 180s (declared mnemonic)")
    text = out + err
    if rc != 0:
        return fail(
            f"the declared function {args.mnemonic} did not resolve headlessly "
            f"(expected rc 0 and the request it would send; got rc {rc}: {text.strip()!r})"
        )
    if "DRY-RUN" not in text or "POST" not in text or "/api/control/" not in text:
        return fail(
            f"the dry run of {args.mnemonic} printed no request \u2014 a declared "
            f"function must resolve to the endpoint it would call ({text.strip()!r})"
        )
    return ok(f"declared function {args.mnemonic}: dry run printed the request, sent nothing")


def cmd_undeclared(args):
    """An undeclared mnemonic is refused BY NAME, and nothing is sent."""
    try:
        rc, out, err = run_client(args.repo, [args.mnemonic])
    except subprocess.TimeoutExpired:
        return fail("the client did not finish within 180s (undeclared mnemonic)")
    text = out + err
    if rc != 1:
        return fail(
            f"the undeclared mnemonic {args.mnemonic} must be refused with rc 1; "
            f"it exited {rc} ({text.strip()!r})"
        )
    if "undeclared_function" not in text or args.mnemonic not in text:
        return fail(
            f"the refusal does not name the undeclared mnemonic "
            f"{args.mnemonic!r} ({text.strip()!r})"
        )
    return ok(f"undeclared mnemonic {args.mnemonic}: refused by name, rc 1, nothing sent")


def cmd_venue(args):
    """Prove the venue is headless: the child sees no TTY on any stream."""
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys;print(int(sys.stdin.isatty()), int(sys.stdout.isatty()), "
            "int(sys.stderr.isatty()))",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        timeout=60,
    )
    seen = proc.stdout.decode().strip()
    if seen != "0 0 0":
        return fail(
            f"the child venue is not headless (stdin/stdout/stderr isatty = {seen!r})"
        )
    return ok("headless venue: stdin /dev/null, stdout+stderr pipes, own session")


def main():
    parser = argparse.ArgumentParser(prog="drive.py")
    sub = parser.add_subparsers(dest="command", required=True)

    frames = sub.add_parser("frames")
    frames.add_argument("--repo", required=True)
    frames.add_argument("--role", default="Analyst")
    frames.add_argument("--default", action="store_true")
    frames.set_defaults(func=cmd_frames)

    gated = sub.add_parser("gated")
    gated.add_argument("--repo", required=True)
    gated.add_argument("--expect", choices=("auto", "off"), default="auto")
    gated.set_defaults(func=cmd_gated)

    mnemonic = sub.add_parser("mnemonic")
    mnemonic.add_argument("--repo", required=True)
    mnemonic.add_argument("--mnemonic", default="HEADER")
    mnemonic.set_defaults(func=cmd_mnemonic)

    undeclared = sub.add_parser("undeclared")
    undeclared.add_argument("--repo", required=True)
    undeclared.add_argument("--mnemonic", default="BOGUS")
    undeclared.set_defaults(func=cmd_undeclared)

    venue = sub.add_parser("venue")
    venue.set_defaults(func=cmd_venue)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
PYEOF
[ -f "$work/drive.py" ] || cannot_assess "the driver could not be written"

# --- 1. the shipped state is honest -----------------------------------------
echo "== the shipped invocation is honest (FLAG_OFF named, never a silent pass) =="
python3 "$work/drive.py" venue > "$work/venue.txt" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  sed 's/^/  /' "$work/venue.txt"
else
  echo "  FAIL  the venue is not headless" >&2
  sed 's/^/      /' "$work/venue.txt" >&2
  FAILED=1
fi

python3 "$work/drive.py" gated --repo "$root" --expect auto > "$work/shipped.txt" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  sed 's/^/  /' "$work/shipped.txt"
else
  echo "  FAIL  the shipped invocation is not an honest state" >&2
  sed 's/^/      /' "$work/shipped.txt" >&2
  FAILED=1
fi

# --- 2. the scratch venue renders the declared set --------------------------
echo "== the client renders the declared set, headlessly (scratch, flag on) =="
python3 - "$flag_registry" <<'PYEOF' >> "$work/promote.txt" 2>&1
"""Promote surfaces.cockpit in the SCRATCH copy (the repository is never edited).

Idempotent on purpose: a lane that promotes the surface in the repository itself
must not break this venue, and this is the SCRATCH copy either way.
"""
import io
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
start = src.index("\n  cockpit:\n")
at = src.index("    default: ", start)
end = src.index("\n", at)
out = src[:at] + "    default: on" + src[end:]
assert "    default: on" in out, "the surfaces.cockpit declaration was not found"
io.open(path, "w", encoding="utf-8").write(out)
print("scratch venue: surfaces.cockpit default is now 'on'")
PYEOF
rc=$?
if [ "$rc" -eq 0 ]; then
  sed 's/^/  /' "$work/promote.txt"
else
  echo "  FAIL  the scratch flag registry could not be promoted" >&2
  sed 's/^/      /' "$work/promote.txt" >&2
  FAILED=1
fi

for role in CTO VP-Eng Manager Analyst; do
  python3 "$work/drive.py" frames --repo "$repo" --role "$role" > "$work/frames.$role.txt" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    sed 's/^/  /' "$work/frames.$role.txt"
  else
    echo "  FAIL  role $role did not render the declared function set" >&2
    sed 's/^/      /' "$work/frames.$role.txt" >&2
    FAILED=1
  fi
done

python3 "$work/drive.py" frames --repo "$repo" --role Analyst --default > "$work/frames.default.txt" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  sed 's/^/  /' "$work/frames.default.txt"
else
  echo "  FAIL  the documented default invocation did not render the declared set" >&2
  sed 's/^/      /' "$work/frames.default.txt" >&2
  FAILED=1
fi

# --- 3. the flag gate fails closed ------------------------------------------
echo "== the flag gate fails closed (registry absent -> named FLAG_OFF) =="
mv "$flag_registry" "$work/registry.yaml.saved" || cannot_assess "cannot stage the flag registry"
python3 "$work/drive.py" gated --repo "$repo" --expect off > "$work/closed.txt" 2>&1
rc=$?
mv "$work/registry.yaml.saved" "$flag_registry" || cannot_assess "cannot restore the flag registry"
if [ "$rc" -eq 0 ]; then
  sed 's/^/  /' "$work/closed.txt"
else
  echo "  FAIL  a cockpit whose flag registry is absent did not fail closed" >&2
  sed 's/^/      /' "$work/closed.txt" >&2
  FAILED=1
fi

# --- 4. one declared function, one undeclared mnemonic ----------------------
echo "== one declared function resolves headlessly; an undeclared one is refused by name =="
python3 "$work/drive.py" mnemonic --repo "$repo" --mnemonic HEADER > "$work/mnemonic.txt" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  sed 's/^/  /' "$work/mnemonic.txt"
else
  echo "  FAIL  the declared function HEADER did not resolve headlessly" >&2
  sed 's/^/      /' "$work/mnemonic.txt" >&2
  FAILED=1
fi

python3 "$work/drive.py" undeclared --repo "$repo" --mnemonic BOGUS > "$work/undeclared.txt" 2>&1
rc=$?
if [ "$rc" -eq 0 ]; then
  sed 's/^/  /' "$work/undeclared.txt"
else
  echo "  FAIL  an undeclared mnemonic was not refused by name" >&2
  sed 's/^/      /' "$work/undeclared.txt" >&2
  FAILED=1
fi

# --- the negative controls ---------------------------------------------------
# provoke <label> <relative-file> <check> <needle-1> <needle-2> <mutator-file>
provoke() {
  local label rel check needle1 needle2 mutator
  label="$1"; rel="$2"; check="$3"; needle1="$4"; needle2="$5"; mutator="$6"
  ran=$((ran + 1))

  local before after
  if ! cp "$root/$rel" "$repo/$rel" 2>/dev/null; then
    printf '  FAIL  %s: cannot reset %s in the scratch tree\n' "$label" "$rel" >&2
    FAILED=1
    return
  fi
  before="$(sha256sum "$repo/$rel" | cut -d' ' -f1)"
  if ! python3 "$work/$mutator" "$repo/$rel"; then
    printf '  FAIL  %s: the mutator could not apply (the anchor moved)\n' "$label" >&2
    FAILED=1
    return
  fi
  after="$(sha256sum "$repo/$rel" | cut -d' ' -f1)"
  if [ "$before" = "$after" ]; then
    printf '  FAIL  %s: the mutation changed nothing -- the control would prove nothing\n' "$label" >&2
    FAILED=1
    return
  fi

  local out_file rc
  out_file="$work/provoke.txt"
  if [ "$check" = "frames" ]; then
    python3 "$work/drive.py" frames --repo "$repo" --role CTO > "$out_file" 2>&1
    rc=$?
  else
    python3 "$work/drive.py" gated --repo "$repo" --expect off > "$out_file" 2>&1
    rc=$?
  fi

  if [ "$rc" -eq 1 ] && grep -F -q -- "$needle1" "$out_file" && grep -F -q -- "$needle2" "$out_file"; then
    printf '  OK    %s (rc=1, refused by name)\n' "$label"
    sed 's/^/          /' "$out_file"
  else
    printf '  FAIL  %s: expected rc=1 naming "%s" and "%s", got rc=%s\n' \
      "$label" "$needle1" "$needle2" "$rc" >&2
    sed 's/^/          /' "$out_file" >&2
    FAILED=1
  fi
  cp "$root/$rel" "$repo/$rel" 2>/dev/null || true
}

echo "== negative controls (each defect provoked in the scratch copy) =="
ran=0

cat > "$work/mutate_drop_panel.py" <<'PYEOF'
"""A client that stops rendering one declared function (FOCUS)."""
import io
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = """        panels = [
            (function_id, self.panel_frame(function_id, self.fixtures.get(function_id)))
            for function_id in self.workspace_ids()
        ]"""
new = """        panels = [
            (function_id, self.panel_frame(function_id, self.fixtures.get(function_id)))
            for function_id in self.workspace_ids()
            if function_id != "FOCUS"
        ]"""
assert old in src, "the compose() panel list was not found"
out = src.replace(old, new, 1)
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PYEOF

cat > "$work/mutate_undeclared_panel.py" <<'PYEOF'
"""A client that renders a panel nothing declares."""
import io
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = '        alerts = ticker_module.render(self.alerts, self.acked, self.width).splitlines()'
new = (
    '        panels.append(("BOGUS", "  \\u250c\\u2500 BOGUS " + "\\u2500" * 40))\n'
    + old
)
assert old in src, "the compose() alerts line was not found"
out = src.replace(old, new, 1)
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PYEOF

cat > "$work/mutate_silent_success.py" <<'PYEOF'
"""A client that exits 0 with no frame (a silent success)."""
import io
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = "        stdout.write(cockpit_app.compose() + \"\\n\")\n        return EXIT_OK"
new = "        return EXIT_OK"
assert old in src, "the --once branch was not found"
out = src.replace(old, new, 1)
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PYEOF

cat > "$work/mutate_broken_fixtures.py" <<'PYEOF'
"""A client whose fixture seam returns nothing, so panels render FAILED."""
import io
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = "    fixtures = render_module.load_fixtures()"
new = "    fixtures = {}"
assert old in src, "the panel_fixtures() loader line was not found"
out = src.replace(old, new, 1)
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PYEOF

cat > "$work/mutate_flag_fail_open.py" <<'PYEOF'
"""A flag reader that fails OPEN: a surface with no readable registry is on."""
import io
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
anchor = "def read_surface(\n"
start = src.index(anchor)
at = src.index("    _paths.ensure_paths()\n", start) + len("    _paths.ensure_paths()\n")
out = src[:at] + '    return "on"\n' + src[at:]
assert out != src, "the read_surface() body was not found"
io.open(path, "w", encoding="utf-8").write(out)
PYEOF

cat > "$work/mutate_skip_flag_gate.py" <<'PYEOF'
"""A client that skips its startup flag gate entirely."""
import io
import sys

path = sys.argv[1]
src = io.open(path, encoding="utf-8").read()
old = "    if flag_state.state(flags_module.SURFACE) != \"on\":"
new = "    if False:"
assert old in src, "the startup flag gate was not found"
out = src.replace(old, new, 1)
assert out != src, "the mutation changed nothing"
io.open(path, "w", encoding="utf-8").write(out)
PYEOF

provoke "a declared panel the client stops rendering is refused" \
  "control-plane/cockpit/cockpit/app.py" frames \
  "does not render declared function 'FOCUS'" "(role CTO" \
  mutate_drop_panel.py

provoke "a panel nothing declares is refused" \
  "control-plane/cockpit/cockpit/app.py" frames \
  "which the RC-10 registry does not declare" "(role CTO" \
  mutate_undeclared_panel.py

provoke "a client that exits 0 with no frame is refused" \
  "control-plane/cockpit/cockpit/cli.py" frames \
  "exited 0 with an empty frame" "(role CTO" \
  mutate_silent_success.py

provoke "a broken fixture seam (FAILED panels) is refused" \
  "control-plane/cockpit/cockpit/registry.py" frames \
  "renders FAILED for a panel" "(role CTO" \
  mutate_broken_fixtures.py

# The two flag-gate controls need the fail-closed venue: the registry is absent.
mv "$flag_registry" "$work/registry.yaml.saved" || cannot_assess "cannot stage the flag registry"

provoke "a flag reader that fails open is refused" \
  "control-plane/cockpit/cockpit/flags.py" gated-off \
  "rendered a frame while its flag is not on" "fail closed" \
  mutate_flag_fail_open.py

provoke "a client that skips its flag gate is refused" \
  "control-plane/cockpit/cockpit/cli.py" gated-off \
  "rendered a frame while its flag is not on" "fail closed" \
  mutate_skip_flag_gate.py

mv "$work/registry.yaml.saved" "$flag_registry" || cannot_assess "cannot restore the flag registry"

# --- the repository's own files are untouched --------------------------------
echo "== the repository was not edited, and the client wrote no fleet state =="
client_after="$(find control-plane/cockpit -type f -not -name '*.pyc' -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d' ' -f1)"
if [ "$client_before" != "$client_after" ]; then
  echo "  FAIL  control-plane/cockpit changed during the run -- the controls are not acting on a copy" >&2
  FAILED=1
else
  echo "  OK    control-plane/cockpit is byte-identical after the run ($client_after)"
fi

# The property is "the client writes no fleet state", so it is measured as the
# DELTA this run caused, never as the tree's absolute state: a path that was
# already dirty on entry is not this gate's business, but one this run CREATES in
# the dirty set, or one already in it whose BYTES it changes, is -- and each is
# refused by name. Declared machine-managed paths are excluded (see above).
snapshot_tree "$work/tree.after" || true
LC_ALL=C sort "$work/tree.before.S" > "$work/before.S.sorted"
LC_ALL=C sort "$work/tree.after.S" > "$work/after.S.sorted"
comm -13 "$work/before.S.sorted" "$work/after.S.sorted" > "$work/created.txt"

# Only a path that was ALREADY dirty can be "rewritten": a path this run created has
# no earlier hash to compare against, and the creation finding above names it once.
: > "$work/rewritten.txt"
while IFS=$'\t' read -r path sha_before; do
  [ -n "$path" ] || continue
  sha_after="$(awk -F'\t' -v p="$path" '$1 == p { print $2; exit }' "$work/tree.after.H")"
  [ -n "$sha_after" ] || continue
  [ "$sha_after" = "$sha_before" ] || printf '%s\n' "$path" >> "$work/rewritten.txt"
done < "$work/tree.before.H"

pre_existing="$(wc -l < "$work/before.S.sorted" | tr -d ' ')"
machine_managed="$(< "$work/tree.before.skipped")"
[ -n "$machine_managed" ] || machine_managed=0
printf '   compared %s pre-existing entr(ies); excluded as machine-managed (governance/isolation/worktree.py): %s\n' \
  "$pre_existing" "$machine_managed"
if [ -s "$work/created.txt" ] || [ -s "$work/rewritten.txt" ]; then
  echo "  FAIL  the run left changes behind -- the client is a client and writes no fleet state:" >&2
  sed 's/^/          /' "$work/created.txt" >&2
  while IFS= read -r path; do
    [ -n "$path" ] || continue
    printf '          %s (its bytes changed during the run)\n' "$path" >&2
  done < "$work/rewritten.txt"
  FAILED=1
else
  echo "  OK    the run added nothing to the tree's dirty set ($pre_existing pre-existing entry/entries compared, $machine_managed machine-managed excluded, no already-dirty file's bytes changed) -- no fleet state written"
fi

# --- the verdict -------------------------------------------------------------
if [ "$FAILED" -ne 0 ]; then
  echo "check-cockpit: FAIL -- the terminal cockpit client is not what the registry declares" >&2
  exit 1
fi

echo "  OK    $ran defect(s) provoked, each refused by name"
echo "check-cockpit: OK -- the cockpit client renders the declared RC-10 function set"
echo "                headlessly, and refuses to start while its surface flag is off"
