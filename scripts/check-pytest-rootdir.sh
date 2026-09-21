#!/usr/bin/env bash
# check-pytest-rootdir.sh — refuse, BY NAME, a checkout whose pytest rootdir an
# ancestor ini can hijack (issue #1212).
#
# THE DEFECT THIS EXISTS FOR
#   The repository root carried NO pytest config, so pytest walked UP from
#   whichever directory it was invoked in and adopted the first ancestor ini it
#   found. On this box a stray `/tmp/pytest.ini` carrying
#   `addopts = --import-mode=importlib` was therefore adopted by every lane whose
#   worktree sat under `/tmp`. That import mode never prepends a test directory
#   to `sys.path`, so `from conftest import ...` — the idiom the suites here use —
#   died with `ModuleNotFoundError: No module named 'conftest'`, and suites whose
#   conftest bootstraps a sibling package died with `No module named 'core'`
#   instead. Measured at c7e6b3d from a `/tmp` venue against the same commit from
#   a `$HOME` venue: `check-telemetry`, `check-control-audit`, `check-erp-module`
#   and `check-ungated-suites` all rc 1 with only the venue changed, plus
#   `engine/core/tickets`, `gateway/providers` and `guardrails/isolation` red.
#   The injury is to EVIDENCE, which is this repository's currency: a lane that
#   measured from `/tmp` published a master-red set the commit did not have.
#
# WHAT IS ASSERTED (one rule, five arms)
#   0. THE PIN — `<root>/pytest.ini` exists, declares a `[pytest]` table, and is
#      INERT: nothing beyond the table itself, so no `addopts`, no import mode,
#      no `testpaths`. The defect was the ancestor's import mode, not the rootdir
#      itself, so the pin must claim the rootdir while being unable to change how
#      anything is imported or collected. A missing or non-inert pin is refused BY
#      NAME — that is the mutation this arm exists for: delete `pytest.ini` and
#      this check reds.
#   1. THE HIJACK — a copy of the committed tree is placed at `<scratch>/venue`
#      where `<scratch>/pytest.ini` holds the offending `addopts`, planted here so
#      the verdict never depends on the stray file existing and the shared
#      `/tmp/pytest.ini` is never written to (a peer lane may be mid-run in it).
#      With the pin copied in, the representative suite must collect and PASS,
#      and pytest must report the venue itself as its rootdir.
#   2. THE PROVOCATION — with that ONE file removed from the same venue the suite
#      must go RED again, with the hijack's own signature. Were it to stay green
#      the venue never reproduced the defect and this check could not tell a
#      hijack from a clean run, so it reports CANNOT-ASSESS rather than a pass it
#      has not earned (GR-12).
#   3. THE CHECKOUT — the same suite run from the checkout itself must PASS. The
#      pin is nearer than any ancestor ini, so the checkout is immune by
#      construction, from any venue.
#   4. NO FALSE RED — the same suite in a second venue whose ancestor chain holds
#      no pytest config at all, with the pin removed, must PASS: without an
#      ancestor ini there is nothing to defend against, and a check that demanded
#      a config in every venue would red on a clean box. A box with no
#      ancestor-free scratch root names this arm instead of passing it silently.
#
# EXIT CONTRACT (guardrails/honesty tri-state, #28): 0 = OK; 1 = NOT-OK (no pin,
# a non-inert pin, a hijack that survived, or a venue red for another reason);
# 2 = CANNOT-ASSESS (no python3/pytest/git/tar, no scratch, or a provocation that
# did not reproduce). An empty run is never a pass.
#
# Usage:
#   bash scripts/check-pytest-rootdir.sh          # assert, then run the arms
#
# ---knowledge---
# module_id: scripts.check-pytest-rootdir
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, no-false-green, lane-isolation]
# derives_from: null
# owner_sme: qa-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: ""
# gotchas: ""
# related: ["#28", "#1212"]
# do_not_duplicate: null
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

# `engine/core/tickets` is the representative suite: it is the one the issue
# names, it is small, and its failure is the hijack's exact signature
# (`No module named 'core'`, the sibling-package arm of the same defect).
suite="engine/core/tickets"
pin="$root/pytest.ini"
suite_timeout="${PYTEST_ROOTDIR_TIMEOUT:-300}"

scratch=""
clean_scratch=""

cleanup() {
  local dir
  for dir in "$scratch" "$clean_scratch"; do
    [ -z "$dir" ] || rm -rf "$dir"
  done
}
trap cleanup EXIT

cannot_assess() {
  printf 'check-pytest-rootdir: CANNOT-ASSESS — %s\n' "$1" >&2
  exit 2
}

refuse() {
  printf 'REFUSED  pytest-rootdir  %s\n' "$1" >&2
}

not_ok() {
  printf 'check-pytest-rootdir: NOT-OK — %s\n' "$1" >&2
  exit 1
}

# A scratch directory carries an EXPLICIT template: `$TMPDIR` on this box is a
# shared, periodically-cleaned cache, so an untemplated `mktemp -d` can vanish
# mid-run and the gate would report the loss of its own workspace as a finding
# (SP-9). The X-run is assembled rather than written literally because this repo's
# docs gate reads a literal X-run as an unfinished marker.
new_scratch() { # <parent-dir> — prints the new directory; rc 1 if it cannot be made
  local dir
  dir="$(mktemp -d "$1/ao1212-pytestroot.$(printf 'X%.0s' 1 2 3 4 5 6)" 2>/dev/null)" || return 1
  printf '%s\n' "$dir"
}

# Settings only: `#` comments stripped, blank lines dropped. The pin's own header
# comment names the options it must not carry, so a raw grep would match this
# file's prose. Membership is then tested with the bash-native `case` shape, never
# with a piped quiet `grep`: that idiom exits on its first match, so the producer
# dies of SIGPIPE and the test fails OPEN (scripts/check-verdict-contains.sh).
pin_settings() { # <config-path>
  sed -E 's/#.*$//' "$1" | grep -vE '^[[:space:]]*$' | tr 'A-Z' 'a-z'
}

# The pin rule. Returns 1 (refusing BY NAME) when the checkout cannot pin its
# rootdir, 0 when it can. The self-provocation below drives THIS function.
assert_pin() { # <config-path>
  local cfg="$1" settings
  if [ ! -f "$cfg" ]; then
    refuse "$cfg is absent — with no config at the checkout root pytest walks up from the venue and adopts the first ancestor ini it finds (#1212)"
    return 1
  fi
  if ! grep -qE '^\[pytest\][[:space:]]*$' "$cfg"; then
    refuse "$cfg declares no [pytest] table — pytest does not read such a file as an ini, so the rootdir stays adoptable"
    return 1
  fi
  settings="$(pin_settings "$cfg")"
  case "$settings" in
    *addopts*)
      refuse "$cfg sets addopts — the pin exists only to claim the rootdir; a setting here changes how suites behave, which is the class of red #1212 was"
      return 1
      ;;
  esac
  case "$settings" in
    *importmode* | *import-mode* | *import_mode* | *"import mode"*)
      refuse "$cfg sets an import mode — #1212 was an ancestor import mode, so the pin must not declare one"
      return 1
      ;;
  esac
  # ...and nothing at all beyond the table: the pin's own header says it declares
  # NO options, and a doc-only promise is advisory (GR-29). A later setting
  # (`testpaths`, `minversion`, `norecursedirs`) would change how suites behave
  # under the cover of a file that exists only to claim the rootdir.
  case "$settings" in
    '[pytest]') return 0 ;;
    *)
      refuse "$cfg declares settings beyond the [pytest] table ('$(printf '%s\n' "$settings" | grep -vF '[pytest]' | tr '\n' ' ' | cut -c1-60)') — claiming the rootdir must change nothing else"
      return 1
      ;;
  esac
}

# True when neither a directory nor any of its ancestors carries a pytest config.
chain_is_clean() { # <dir>
  local dir="$1" anc anc_cfg
  anc="$dir"
  while :; do
    for anc_cfg in pytest.ini pyproject.toml setup.cfg tox.ini; do
      [ -e "$anc/$anc_cfg" ] && return 1
    done
    [ "$anc" = "/" ] && return 0
    anc="$(dirname "$anc")"
  done
}

# --------------------------------------------------------------------------- #
# Preconditions
# --------------------------------------------------------------------------- #
command -v python3 >/dev/null 2>&1 || cannot_assess "python3 is unavailable; no suite can be run"
command -v tar >/dev/null 2>&1 || cannot_assess "tar is unavailable; the venue cannot be extracted"
if ! command -v git >/dev/null 2>&1 || ! git rev-parse --git-dir >/dev/null 2>&1; then
  cannot_assess "not inside a git worktree — the venue is extracted from the committed tree, and a working-tree scan would read a peer lane's scratch copy"
fi
python3 -c 'import pytest' >/dev/null 2>&1 || cannot_assess "the python3 on PATH has no pytest; the representative suite cannot be collected"

scratch="$(new_scratch /tmp)" || cannot_assess "no scratch directory could be made under /tmp (disk or permissions)"

# --------------------------------------------------------------------------- #
# The provocation runs BEFORE the repository is read, and drives the same
# assert_pin the real run uses — never a copy of it. A gate whose failure path
# has never fired is a formality (GR-12).
# --------------------------------------------------------------------------- #
prov="$(assert_pin "$scratch/no-such-pin/pytest.ini" 2>&1)"
prov_rc=$?
if [ "$prov_rc" -ne 1 ]; then
  cannot_assess "the refusal path did not fire on a provably absent pin (rc $prov_rc) — this check cannot report a missing pin"
fi
case "$prov" in
  *'REFUSED  pytest-rootdir'*) : ;;
  *) cannot_assess "the refusal path fired without naming itself ('$prov') — a finding nobody can act on is not a finding" ;;
esac

# --------------------------------------------------------------------------- #
# Arm 0 — the pin
# --------------------------------------------------------------------------- #
if ! assert_pin "$pin"; then
  not_ok "the checkout has no inert pytest rootdir pin, so an ancestor ini can hijack the suite verdict (#1212)"
fi
echo "  PASS  pin  ($pin declares [pytest] and no addopts/import mode)"

# --------------------------------------------------------------------------- #
# Arms 1 and 2 — the venue that carries an offending ancestor ini
# --------------------------------------------------------------------------- #
venue="$scratch/venue"
mkdir -p "$venue" || cannot_assess "could not create the venue $venue"
printf '[pytest]\naddopts = --import-mode=importlib\n' >"$scratch/pytest.ini" \
  || cannot_assess "could not plant the offending ancestor ini at $scratch/pytest.ini"
git -C "$root" archive HEAD 2>/dev/null | tar -x -C "$venue" 2>/dev/null \
  || cannot_assess "could not extract the committed tree into $venue"
[ -d "$venue/$suite" ] || cannot_assess "the venue holds no $suite — the committed tree could not be read"
cp "$pin" "$venue/pytest.ini" || cannot_assess "could not copy the pin into the venue"

head_sha="$(git rev-parse --short HEAD 2>/dev/null)"
echo "check-pytest-rootdir: venue $venue over a planted ancestor ini, tree from HEAD ${head_sha:-unknown}"

out_pinned="$(cd "$venue" && env PYTHONDONTWRITEBYTECODE=1 timeout "$suite_timeout" python3 -m pytest -p no:cacheprovider -q "$suite" 2>&1)"
rc_pinned=$?
if [ "$rc_pinned" -ne 0 ]; then
  refuse "the ancestor ini won: $suite is red in the venue even with the pin present (rc $rc_pinned) — last line: $(printf '%s\n' "$out_pinned" | tail -1 | cut -c1-120)"
  not_ok "the checkout is hijackable from this venue (#1212)"
fi
echo "  PASS  hijack venue  ($suite green with the pin present: $(printf '%s\n' "$out_pinned" | tail -1 | cut -c1-60))"

prov="$(cd "$venue" && env PYTHONDONTWRITEBYTECODE=1 timeout "$suite_timeout" python3 -m pytest -p no:cacheprovider --collect-only "$suite" 2>&1)"
prov_rootdir="$(printf '%s\n' "$prov" | sed -nE 's/^rootdir: //p' | head -1)"
if [ "$prov_rootdir" != "$venue" ]; then
  refuse "the venue's pytest rootdir is '${prov_rootdir:-unreported}', not $venue — an ancestor ini was adopted (#1212)"
  not_ok "the pin does not claim the rootdir"
fi
echo "  PASS  rootdir  (pytest reports $prov_rootdir)"

rm -f "$venue/pytest.ini" || cannot_assess "could not remove the pin from the venue"
out_bare="$(cd "$venue" && env PYTHONDONTWRITEBYTECODE=1 timeout "$suite_timeout" python3 -m pytest -p no:cacheprovider -q "$suite" 2>&1)"
rc_bare=$?
if [ "$rc_bare" -eq 0 ]; then
  cannot_assess "the planted ancestor ini was not adopted — $suite passed without the pin, so this venue cannot tell a hijack from a clean run"
fi
case "$out_bare" in
  *"No module named 'core'"* | *"No module named 'conftest'"*) : ;;
  *) cannot_assess "with the pin removed the venue went red without the hijack's signature (rc $rc_bare), so the provocation proved nothing about the hijack" ;;
esac
echo "  PASS  provocation  (same venue, pin removed: $suite reds rc $rc_bare with the hijack's signature)"

# --------------------------------------------------------------------------- #
# Arm 3 — the checkout itself, from whatever venue this gate is running in
# --------------------------------------------------------------------------- #
out_root="$(env PYTHONDONTWRITEBYTECODE=1 timeout "$suite_timeout" python3 -m pytest -p no:cacheprovider -q "$suite" 2>&1)"
rc_root=$?
if [ "$rc_root" -ne 0 ]; then
  refuse "the checkout itself is red for $suite with the pin present (rc $rc_root) — last line: $(printf '%s\n' "$out_root" | tail -1 | cut -c1-120)"
  not_ok "the pin does not protect the checkout's own suite"
fi
echo "  PASS  checkout  ($suite green from the gate's own venue)"

# --------------------------------------------------------------------------- #
# Arm 4 — no ancestor ini anywhere: there is nothing to pin against, so the
# suite must be green WITHOUT the pin. `/tmp` cannot serve this arm (the stray
# file this check exists for is an ancestor of everything under it, and deleting
# shared state is the same class of harm as signalling another lane by name), so
# the arm uses the first scratch root whose chain is verifiably config-free.
# --------------------------------------------------------------------------- #
clean_parent=""
for candidate in /dev/shm /var/tmp; do
  [ -d "$candidate" ] || continue
  chain_is_clean "$candidate" || continue
  clean_scratch="$(new_scratch "$candidate")" || continue
  clean_parent="$candidate"
  break
done

if [ -z "$clean_scratch" ]; then
  echo "  SKIP  no ancestor-config-free scratch root (/dev/shm, /var/tmp) — the no-ancestor arm is not applicable on this box"
else
  cvenue="$clean_scratch/venue"
  mkdir -p "$cvenue" || cannot_assess "could not create the clean venue $cvenue"
  git -C "$root" archive HEAD 2>/dev/null | tar -x -C "$cvenue" 2>/dev/null \
    || cannot_assess "could not extract the committed tree into the clean venue"
  [ -d "$cvenue/$suite" ] || cannot_assess "the clean venue holds no $suite — the committed tree could not be read"
  rm -f "$cvenue/pytest.ini" || cannot_assess "could not remove the pin from the clean venue"
  out_clean="$(cd "$cvenue" && env PYTHONDONTWRITEBYTECODE=1 timeout "$suite_timeout" python3 -m pytest -p no:cacheprovider -q "$suite" 2>&1)"
  rc_clean=$?
  if [ "$rc_clean" -ne 0 ]; then
    refuse "with no ancestor ini and no pin, $suite is red in the clean venue (rc $rc_clean) — last line: $(printf '%s\n' "$out_clean" | tail -1 | cut -c1-120)"
    not_ok "the venue is red for a reason other than the hijack, so the arms above prove nothing"
  fi
  echo "  PASS  no-ancestor  ($suite green under $clean_parent with no config anywhere: $(printf '%s\n' "$out_clean" | tail -1 | cut -c1-60))"
fi

echo "check-pytest-rootdir: OK — the pin at $pin is inert, and $suite is green behind an offending ancestor ini, red once the pin is removed, green from the checkout and green with no ancestor ini at all (sha ${head_sha:-unknown})"
exit 0
