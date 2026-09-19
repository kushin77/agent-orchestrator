#!/usr/bin/env bash
# check-cloudbuild-env-surface.sh — the Cloud Build substitution surface gate
# (issue #1425, RCA of #1242).
#
# THE DEFECT THIS EXISTS FOR
#   #1242 was a leaked/misconfigured Cloud Build substitution (`CLOUD_LOGGING_ONLY`)
#   patched by hand, one YAML edit, no repeatable gate. The only env-var check in
#   this repo (`scripts/check-portal-auth-env.sh`) is hardcoded to two names
#   (`PORTAL_AUTH_GATE_JWKS`, `ROOT_ADMIN_EMAILS`); `scripts/check-env-surface.sh`
#   (#944) is a SEPARATE gate for the Python console's `os.environ` surface
#   (`infra/env/registry.yaml`). Neither one reads the `infra/cloudbuild/*.yaml`
#   trigger substitution blocks against what the repo's OWN scripts actually
#   reference. A new `_FOO` substitution referenced by a script but never
#   declared in any trigger config is silently empty in CI — exactly the shape
#   of #1242 — and nothing catches it.
#
# WHAT THIS GATE MEASURES (declared vs. observed, refused by name)
#   DECLARED  — every `_[A-Z_]+` key under a `substitutions:` block in
#               infra/cloudbuild/*.yaml (trigger configs AND the build configs
#               they name both declare valid substitutions; either counts).
#   OBSERVED  — every `_[A-Z_]+` token referenced as `$_FOO` / `${_FOO}` in a
#               shell context, or as an `os.environ`/`os.getenv` literal, under
#               scripts/, gateway/, fleet/, control-plane/ (excluding
#               infra/cloudbuild/ itself, which is the declaration side).
#   (a) OBSERVED but never DECLARED anywhere under infra/cloudbuild/ —
#       `undeclared-cloudbuild-var` — FAILS the gate. This is the #1242 class: a
#       script trusts a substitution that no trigger config supplies, so it is
#       empty in CI however the code reads.
#   (b) DECLARED but never OBSERVED outside infra/cloudbuild/ — reported as a
#       dead declaration, but does NOT fail the gate (too noisy: a substitution
#       consumed only inside its own build config's `script:` step, e.g.
#       `_AR_REPO`, is legitimately declared-and-used without ever reaching
#       scripts/gateway/fleet/control-plane).
#   Cloud Build's own built-ins and GitHub-trigger defaults (BRANCH_NAME,
#   PROJECT_ID, `$_HEAD_BRANCH`, `$_PR_NUMBER`, ...) are exempt by name — they
#   are supplied by the platform, not declared by a trigger, and check-cloudbuild.sh
#   pins the same list.
#
# SELF-TEST (refused by name, both ways, #1425's own acceptance bar)
#   `--self-test` proves the gate can fail: it plants a scratch script that
#   references `$_AO_SELFTEST_UNDECLARED_VAR` with NO declaring trigger config,
#   and requires the scan to refuse it by name; it then plants the matching
#   declaration and requires the SAME scan to accept the (otherwise identical)
#   twin. Nothing is written into the real tree; both fixtures live under a
#   scratch directory outside the repo.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage:
#   bash scripts/check-cloudbuild-env-surface.sh              # gate the real tree
#   bash scripts/check-cloudbuild-env-surface.sh --self-test   # provoke + accept
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root" || exit 2

if ! command -v python3 >/dev/null 2>&1; then
  echo "check-cloudbuild-env-surface: CANNOT-ASSESS — python3 not found" >&2
  exit 2
fi

if [ ! -d infra/cloudbuild ]; then
  echo "check-cloudbuild-env-surface: CANNOT-ASSESS — infra/cloudbuild is missing" >&2
  exit 2
fi

mode="scan"
if [ "${1:-}" = "--self-test" ]; then
  mode="self-test"
fi

exec python3 - "$root" "$mode" <<'PY'
"""Cloud Build substitution surface: declared vs. observed, refused by name (#1425)."""
import re
import sys
import tempfile
import shutil
from pathlib import Path

root = Path(sys.argv[1]).resolve()
mode = sys.argv[2]

VAR = re.compile(r"_[A-Z][A-Z0-9_]*")
DECL_LINE = re.compile(r"^\s{2}(_[A-Z][A-Z0-9_]*):")
SUBS_HEADER = re.compile(r"^substitutions:\s*$")
SHELL_REF = re.compile(r"\$\{?(_[A-Z][A-Z0-9_]*)\}?")
PY_ENV = re.compile(r"os\.(?:environ(?:\.get)?|getenv)\(\s*[\"'](_[A-Z][A-Z0-9_]*)[\"']")

# Same list check-cloudbuild.sh pins (platform-supplied, never declared by a
# trigger's own substitutions: block).
GITHUB_TRIGGER_DEFAULTS = frozenset(
    "_HEAD_BRANCH _BASE_BRANCH _HEAD_REPO_URL _PR_NUMBER".split()
)

SCAN_DIRS = ("scripts", "gateway", "fleet", "control-plane")
CODE_SUFFIXES = {".sh", ".py", ".yaml", ".yml"}


def declared_vars(cloudbuild_dir, scan_root):
    declared = {}
    for path in sorted(cloudbuild_dir.glob("*.yaml")):
        in_block = False
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if SUBS_HEADER.match(line):
                in_block = True
                continue
            if in_block:
                if line.startswith(" "):
                    m = DECL_LINE.match(line)
                    if m:
                        declared.setdefault(m.group(1), []).append(str(path.relative_to(scan_root)))
                    continue
                in_block = False
    return declared


def observed_vars(scan_root, skip_cloudbuild):
    observed = {}
    for top in SCAN_DIRS:
        base = scan_root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in CODE_SUFFIXES:
                continue
            rel = path.relative_to(scan_root)
            if skip_cloudbuild and rel.parts[:2] == ("infra", "cloudbuild"):
                continue
            if "cloudbuild" in rel.parts:
                # A cloudbuild-named file living under a scanned dir (rare) is
                # still declaration surface, not an observer of it.
                continue
            if path.name.startswith("check-cloudbuild"):
                # This gate and its sibling (check-cloudbuild.sh) are ABOUT the
                # substitution surface: their own docstrings, comments and
                # provocation fixtures embed example/mutant var names
                # ($_FLAG, $_ZZZ_ABSENT, $_FOO, ...) as literal text, not live
                # references a deploy needs supplied. Scanning them as
                # observers would flag the gate's own test prose as a defect.
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in SHELL_REF.finditer(text):
                observed.setdefault(m.group(1), []).append(str(rel))
            for m in PY_ENV.finditer(text):
                observed.setdefault(m.group(1), []).append(str(rel))
    return observed


def run_scan(scan_root, cloudbuild_rel="infra/cloudbuild"):
    cloudbuild_dir = scan_root / cloudbuild_rel
    declared = declared_vars(cloudbuild_dir, scan_root) if cloudbuild_dir.is_dir() else {}
    observed = observed_vars(scan_root, skip_cloudbuild=True)

    undeclared = sorted(
        v for v in observed if v not in declared and v not in GITHUB_TRIGGER_DEFAULTS
    )
    dead = sorted(v for v in declared if v not in observed)
    return declared, observed, undeclared, dead


def report(declared, observed, undeclared, dead, label):
    print("== cloudbuild-env-surface%s ==" % label)
    print("  declared substitutions : %d" % len(declared))
    print("  observed references    : %d" % len(observed))
    ok = True
    for name in undeclared:
        sites = ", ".join(observed[name][:3])
        print(
            "  FAIL  undeclared-cloudbuild-var: %s is referenced (%s) but no "
            "infra/cloudbuild/*.yaml substitutions: block declares it" % (name, sites),
            file=sys.stderr,
        )
        ok = False
    for name in dead:
        sites = ", ".join(declared[name][:3])
        print(
            "  ..    declared-cloudbuild-var-unused: %s is declared (%s) but "
            "nothing under scripts/ gateway/ fleet/ control-plane/ references it "
            "(not a failure)" % (name, sites)
        )
    if ok:
        print("  OK    every observed _VAR is declared by a Cloud Build config")
    return ok


if mode == "scan":
    declared, observed, undeclared, dead = run_scan(root)
    ok = report(declared, observed, undeclared, dead, "")
    if not ok:
        print(
            "check-cloudbuild-env-surface: FAIL — %d undeclared Cloud Build "
            "substitution(s) referenced in code" % len(undeclared),
            file=sys.stderr,
        )
        sys.exit(1)
    print("check-cloudbuild-env-surface: OK")
    sys.exit(0)

# --- self-test: refused by name, both ways ----------------------------------
work = Path(tempfile.mkdtemp(prefix="ao1425-cbenv-"))
try:
    varname = "_AO_SELFTEST_UNDECLARED_VAR"

    def make_fixture(declare):
        fx = work / ("declared" if declare else "undeclared")
        (fx / "infra" / "cloudbuild").mkdir(parents=True, exist_ok=True)
        (fx / "scripts").mkdir(parents=True, exist_ok=True)
        (fx / "scripts" / "uses-var.sh").write_text(
            '#!/usr/bin/env bash\necho "value is ${%s}"\n' % varname, encoding="utf-8"
        )
        subs = "substitutions:\n  %s: \"x\"\n" % varname if declare else ""
        (fx / "infra" / "cloudbuild" / "build.yaml").write_text(
            "steps: []\n%s" % subs, encoding="utf-8"
        )
        return fx

    fail = 0

    undeclared_fx = make_fixture(declare=False)
    _, _, undeclared, _ = run_scan(undeclared_fx)
    if varname in undeclared:
        print(
            "  OK    self-test: an undeclared %s is refused by name" % varname
        )
    else:
        print(
            "  FAIL  self-test: an undeclared %s was NOT refused" % varname,
            file=sys.stderr,
        )
        fail += 1

    declared_fx = make_fixture(declare=True)
    _, _, undeclared2, _ = run_scan(declared_fx)
    if varname not in undeclared2:
        print(
            "  OK    self-test: the same var, once declared, is accepted"
        )
    else:
        print(
            "  FAIL  self-test: the declared twin was still refused",
            file=sys.stderr,
        )
        fail += 1

    if fail:
        print(
            "check-cloudbuild-env-surface: FAIL — self-test did not refuse/accept as expected",
            file=sys.stderr,
        )
        sys.exit(1)
    print("check-cloudbuild-env-surface: OK (self-test)")
    sys.exit(0)
finally:
    shutil.rmtree(work, ignore_errors=True)
PY
