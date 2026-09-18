#!/usr/bin/env bash
# check-toolchain-parity.sh -- the verify runner's own toolchain is a DECLARED
# contract, and it is asserted (#1264, parent #1254).
#
# THE DEFECT THIS EXISTS FOR
#   `make verify` claims to run "the same everywhere". Measured 2026-09-18
#   (#1245), it did not -- and every difference was found one build at a time,
#   because none of them was DECLARED. The gate asserted the product and never
#   asserted its own instrument:
#     * Python 3.12 on the runner. `Path.glob("x/**")` only matches the FILES
#       below a directory from 3.13 on, so the knowledge index was silently
#       starved of its sources and still reported green.
#     * no git identity. check-reconcile's backdated provocation makes a real
#       commit; with no identity it made none, so the provocation reported
#       success while provoking nothing.
#     * a shallow checkout. lessons-sync and capability-drift walk commit
#       ancestry; the ancestors they cite were simply not here.
#     * `PATH=/usr/bin:/bin` reaching an interpreter without PyYAML -- the deps
#       had been installed into a different interpreter.
#
# WHAT IS CHECKED (every rule refuses BY NAME, or it is a formality -- GR-12)
#   The declaration is infra/cloudbuild/toolchain.yaml (AO_TOOLCHAIN_DECL
#   overrides it; AO_TOOLCHAIN_REPO overrides the repository under test). Every
#   rule reads one declared line and measures the runner against it:
#
#     toolchain:python-minor       the interpreter `python3` resolves to is within `python.range`
#     toolchain:git-identity       `git config user.name` and `user.email` both resolve
#     toolchain:shallow            the checkout is not shallow
#     toolchain:interpreter-split  every declared interpreter that exists agrees on
#                                  major.minor AND imports every declared module
#
# WHY THE DECLARATION IS READ WITHOUT PyYAML
#   A checker that needs the toolchain it is checking cannot diagnose that
#   toolchain's absence: this gate has to run when `python3` is the wrong version
#   or has no PyYAML at all. The declaration is therefore read by a small,
#   dependency-free reader that understands exactly the two-level subset the
#   declaration uses; a key it cannot find is CANNOT-ASSESS (exit 2), never a
#   silent pass.
#
# HOW IT PROVES IT CAN FAIL (the provocation battery runs on EVERY invocation)
#   Each rule is provoked against a SCRATCH fixture -- a stub interpreter on a
#   scratch PATH, a scratch git repository, a scratch declaration -- and each
#   provocation must produce EXACTLY ONE refusal, naming the rule under test.
#   The other rules must stay silent, so a refusal is attributed to the rule and
#   not to a broken fixture. One control is positive (a conforming fixture must
#   pass with no refusal), because a gate that only ever refuses proves nothing.
#   One control mutates a COPY of the declared range and requires the mutant to
#   be refused -- it proves the range reader reads its UPPER bound too, not just
#   its lower one. The contract file's sha256 is compared before and after the
#   battery, so a control that quietly edited the contract fails here. A rule that
#   cannot be provoked is reported as such and fails the gate.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# CANNOT-ASSESS must never be reported as a pass.
#
# Usage: bash scripts/check-toolchain-parity.sh [--assert-only]
#   --assert-only   run the real assertions only. This is the seam the controls
#                   use to invoke this script, so the battery cannot recurse.
set -uo pipefail

self="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/$(basename "${BASH_SOURCE[0]}")"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
decl="${AO_TOOLCHAIN_DECL:-$root/infra/cloudbuild/toolchain.yaml}"
repo="${AO_TOOLCHAIN_REPO:-$root}"

run_selftest=1
case "${1:-}" in
  "") ;;
  --assert-only) run_selftest=0 ;;
  --self-test) ;;
  *) printf 'check-toolchain-parity: unknown argument: %s\n' "$1" >&2; exit 2 ;;
esac

fail=0
scratch=""
st_total=0
st_fail=0
cleanup() { if [ -n "$scratch" ]; then rm -rf "$scratch"; fi; }
trap cleanup EXIT

cannot_assess() { printf 'check-toolchain-parity: CANNOT-ASSESS -- %s\n' "$1" >&2; exit 2; }
report_ok()     { printf '  %-26s %s\n' "toolchain:$1" "OK -- $2"; }
report_fail()   { printf '  %-26s %s\n' "toolchain:$1" "FAIL -- $2"; fail=$((fail + 1)); }

command -v git >/dev/null 2>&1 || cannot_assess "git is not on PATH"
command -v awk >/dev/null 2>&1 || cannot_assess "awk is not on PATH (the declared toolchain cannot be read)"

# --- the declaration reader (dependency-free; see the header) -----------------
flat_decl() { # <file> -> "path<TAB>value" for the two-level subset this file uses
  awk '
    /^[[:space:]]*#/ { next }
    /^[[:space:]]*$/ { next }
    {
      line = $0
      sub(/[[:space:]]+#.*$/, "", line)
      if (line ~ /^[^[:space:]]/) {
        key = line; sub(/:.*$/, "", key)
        val = line; sub(/^[^:]*:[[:space:]]*/, "", val)
        if (val == "") { section = key } else { print key "\t" val; section = "" }
        next
      }
      key = line; sub(/^[[:space:]]*/, "", key); sub(/:.*$/, "", key)
      val = line; sub(/^[[:space:]]*[^:]*:[[:space:]]*/, "", val)
      if (section != "") { print section "." key "\t" val }
    }
  ' "$1"
}

decl_get() { # <flat> <key> -> the value on stdout; rc 1 when the key is absent
  local v
  v="$(printf '%s\n' "$1" | awk -F'\t' -v k="$2" '$1 == k { print $2; exit }')"
  if [ -z "$v" ]; then return 1; fi
  v="${v%\"}"; v="${v#\"}"
  printf '%s' "$v"
}

decl_list() { # <flat> <key> -> one item per line
  local v
  v="$(decl_get "$1" "$2")" || return 1
  v="${v#[}"; v="${v%]}"
  printf '%s\n' "$v" | tr ',' '\n' \
    | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | sed -E '/^$/d'
}

# --- interpreter and git probes ---------------------------------------------
interp_version() { # <interp> -> "3.14.4"
  local out
  out="$("$1" -V 2>/dev/null)" || return 1
  printf '%s' "$out" | sed -nE 's/^Python[[:space:]]+([0-9][0-9.]*).*$/\1/p'
}

interp_minor() { # <interp> -> "3.14"
  local v
  v="$(interp_version "$1")" || return 1
  printf '%s' "$v" | sed -nE 's/^([0-9]+)\.([0-9]+).*$/\1.\2/p'
}

interp_imports() { # <interp> <module> -> rc 0 when it imports
  "$1" -c "import $2" >/dev/null 2>&1
}

minor_num() { # "3.14" -> 3014; awk only, so no interpreter under test is needed
  printf '%s' "$1" | awk -F. 'NF >= 2 { printf "%d\n", $1 * 1000 + $2; found = 1 } END { if (!found) exit 1 }'
}

range_ok() { # <minor> <range> -> rc 0 when minor satisfies EVERY comma-separated bound
  local got bound op want bounds
  got="$(minor_num "$1")" || return 1
  bounds="$(printf '%s\n' "$2" | tr ',' '\n' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | sed -E '/^$/d')"
  if [ -z "$bounds" ]; then return 1; fi
  while IFS= read -r bound; do
    op="$(printf '%s' "$bound" | sed -nE 's/^(>=|<=|>|<|=).*$/\1/p')"
    want="$(printf '%s' "$bound" | sed -nE 's/^[<>=]+//p')"
    if [ -z "$op" ] || [ -z "$want" ]; then return 1; fi
    want="$(minor_num "$want")" || return 1
    case "$op" in
      '>=') if [ "$got" -lt "$want" ]; then return 1; fi ;;
      '<=') if [ "$got" -gt "$want" ]; then return 1; fi ;;
      '>')  if [ "$got" -le "$want" ]; then return 1; fi ;;
      '<')  if [ "$got" -ge "$want" ]; then return 1; fi ;;
      '=')  if [ "$got" -ne "$want" ]; then return 1; fi ;;
      *)    return 1 ;;
    esac
  done <<< "$bounds"
  return 0
}

git_identity_missing() { # <repo> -> the missing keys; rc 1 when any is missing
  local key val missing=""
  for key in user.name user.email; do
    val="$(git -C "$1" config --get "$key" 2>/dev/null)" || val=""
    if [ -z "$val" ]; then missing="${missing}${missing:+, }$key"; fi
  done
  if [ -n "$missing" ]; then printf '%s' "$missing"; return 1; fi
  return 0
}

is_shallow() { # <repo> -> "true"/"false"; rc 1 when git cannot say
  local out
  out="$(git -C "$1" rev-parse --is-shallow-repository 2>/dev/null)" || return 1
  case "$out" in
    true|false) printf '%s' "$out" ;;
    *) return 1 ;;
  esac
}

sha256_of() { # <file> -> the sha256 digest; rc 1 when no such tool is present
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  else return 1
  fi
}

# --- the rules ---------------------------------------------------------------
rule_python_minor() {
  local range interp ver minor
  range="$(decl_get "$flat" python.range)"
  interp="$(command -v python3 2>/dev/null)" || interp=""
  if [ -z "$interp" ]; then
    report_fail python-minor "python3 does not resolve on PATH (PATH=$PATH)"
    return
  fi
  if ! ver="$(interp_version "$interp")" || [ -z "$ver" ]; then
    report_fail python-minor "$interp did not answer -V like an interpreter"
    return
  fi
  if ! minor="$(interp_minor "$interp")" || [ -z "$minor" ]; then
    report_fail python-minor "could not read a major.minor from $interp (it reported $ver)"
    return
  fi
  if range_ok "$minor" "$range"; then
    report_ok python-minor "python3 = $interp, $ver, within the declared range $range"
  else
    report_fail python-minor "python3 ($interp) is $ver, outside the declared range $range"
  fi
}

rule_git_identity() {
  local want missing
  want="$(decl_get "$flat" git.identity)"
  if [ "$want" != "required" ]; then
    cannot_assess "git.identity must be declared 'required' (declared: '$want')"
  fi
  if missing="$(git_identity_missing "$repo")"; then
    report_ok git-identity "git config user.name and user.email both resolve in $repo"
  else
    report_fail git-identity "no git identity in $repo: $missing does not resolve (declare it: git config user.name / user.email)"
  fi
}

rule_shallow() {
  local want shallow
  want="$(decl_get "$flat" git.shallow)"
  case "$want" in
    true|false) ;;
    *) cannot_assess "git.shallow must be declared true or false (declared: '$want')" ;;
  esac
  if ! shallow="$(is_shallow "$repo")"; then
    cannot_assess "git could not report shallowness for $repo (is it a repository?)"
  fi
  if [ "$shallow" = "$want" ]; then
    report_ok shallow "$repo is not shallow, as declared (git.shallow: $want)"
  else
    report_fail shallow "$repo is shallow (git rev-parse --is-shallow-repository = $shallow) but git.shallow declares $want; the gate walks commit ancestry (lessons-sync, capability-drift) that is not here"
  fi
}

rule_interpreter_split() {
  local path_py p m seen mn v dup detail="" ref_minor="" ref_path="" ok=1
  local -a declared=() present=() mods=()

  while IFS= read -r p; do declared+=("$p"); done < <(decl_list "$flat" interpreters)
  path_py="$(command -v python3 2>/dev/null)" || path_py=""
  if [ -n "$path_py" ]; then declared+=("$path_py"); fi

  for p in "${declared[@]}"; do
    if [ -z "$p" ] || [ ! -x "$p" ]; then continue; fi
    dup=0
    for seen in "${present[@]}"; do
      if [ "$seen" = "$p" ]; then dup=1; break; fi
    done
    if [ "$dup" -eq 0 ]; then present+=("$p"); fi
  done

  if [ "${#present[@]}" -eq 0 ]; then
    cannot_assess "no declared interpreter exists (declared: ${declared[*]:-none})"
  fi

  while IFS= read -r m; do mods+=("$m"); done < <(decl_list "$flat" modules)
  if [ "${#mods[@]}" -eq 0 ]; then
    cannot_assess "the declaration lists no modules to import"
  fi

  for p in "${present[@]}"; do
    v="$(interp_version "$p")" || v="<no version>"
    if ! mn="$(interp_minor "$p")" || [ -z "$mn" ]; then
      ok=0
      detail="${detail}${detail:+; }$p did not report a major.minor (it reported $v)"
      continue
    fi
    if [ -z "$ref_minor" ]; then
      ref_minor="$mn"; ref_path="$p"
    elif [ "$mn" != "$ref_minor" ]; then
      ok=0
      detail="${detail}${detail:+; }$p is $v while $ref_path is $ref_minor (major.minor must match)"
    fi
    for m in "${mods[@]}"; do
      if ! interp_imports "$p" "$m"; then
        ok=0
        detail="${detail}${detail:+; }$p cannot import $m"
      fi
    done
  done

  if [ "$ok" -eq 1 ]; then
    report_ok interpreter-split "${#present[@]} interpreter(s) agree on $ref_minor and import ${mods[*]} (${present[*]})"
  else
    report_fail interpreter-split "$detail"
  fi
}

# --- the provocation battery's fixtures --------------------------------------
make_stub() { # <path> <version> <real-python> <modules-ok:1|0>
  local path="$1" version="$2" real="$3" import_rc=1
  if [ "$4" = "1" ]; then import_rc=0; fi
  {
    printf '#!/usr/bin/env bash\n'
    printf '# a STUB interpreter: a toolchain-parity control, never a real python.\n'
    printf 'case "${1:-}" in\n'
    printf '  -V|--version) echo "Python %s"; exit 0 ;;\n' "$version"
    printf '  -c)\n'
    printf '    case "${2:-}" in\n'
    printf '      *"import "*) exit %s ;;\n' "$import_rc"
    printf '    esac\n'
    printf '    exec %s "$@" ;;\n' "$real"
    printf '  *) exec %s "$@" ;;\n' "$real"
    printf 'esac\n'
  } > "$path"
  chmod +x "$path"
}

write_decl() { # <file> <interpreters-flow-list> <range>
  cat > "$1" <<EOF
python:
  range: "$3"
modules: [yaml]
git:
  identity: required
  shallow: false
interpreters: $2
EOF
}

make_repo() { # <dir> <with-declared-identity:1|0> -- a scratch repo, identity optional
  git init -q "$1" || return 1
  printf 'probe\n' > "$1/file.txt"
  git -C "$1" add file.txt || return 1
  if [ "$2" = "1" ]; then
    git -C "$1" -c user.name=probe -c user.email=probe@example.invalid commit -q -m probe || return 1
    git -C "$1" config user.name probe || return 1
    git -C "$1" config user.email probe@example.invalid || return 1
  else
    # The commit is made with -c so the repository is usable; the identity is
    # deliberately NOT configured, which is the defect being provoked.
    git -C "$1" -c user.name=probe -c user.email=probe@example.invalid commit -q -m probe || return 1
  fi
}

make_shallow() { # <dst> -- a real --depth 1 clone: a genuinely shallow checkout
  make_repo "$scratch/src" 1 || return 1
  git clone -q --depth 1 "file://$scratch/src" "$1" 2>/dev/null || return 1
  git -C "$1" config user.name probe
  git -C "$1" config user.email probe@example.invalid
}

# --- the provocation battery -------------------------------------------------
assert_control() { # <label> <expect-rule|NONE> <expect-rc> <actual-rc> <output>
  local label="$1" expect="$2" expect_rc="$3" rc="$4" out="$5"
  local n_fail names line ok=1
  st_total=$((st_total + 1))
  n_fail="$(printf '%s\n' "$out" | grep -c 'FAIL --' || true)"
  names="$(printf '%s\n' "$out" | grep 'FAIL --' \
    | sed -E 's/^[[:space:]]*(toolchain:[a-z-]+).*$/\1/' | sort -u \
    | awk 'NR > 1 { printf "," } { printf "%s", $0 }')"
  line="$(printf '%s\n' "$out" | grep 'FAIL --' | head -1)"
  printf '  control %-22s expect rc=%s rule=%s\n' "$label" "$expect_rc" "$expect"
  printf '    ACTUAL rc=%s FAIL-lines=%s rule(s)=%s\n' "$rc" "$n_fail" "${names:-<none>}"
  printf '    ACTUAL line: %s\n' "${line:-<no refusal line>}"
  if [ "$rc" != "$expect_rc" ]; then ok=0; fi
  if [ "$expect" = "NONE" ]; then
    if [ "$n_fail" != "0" ]; then ok=0; fi
  else
    if [ "$names" != "$expect" ]; then ok=0; fi
  fi
  if [ "$ok" -eq 1 ]; then
    if [ "$expect" = "NONE" ]; then
      printf '    OK -- a conforming fixture is accepted (the gate is not refusing unconditionally)\n'
    else
      printf '    OK -- refused exactly once, naming %s\n' "$expect"
    fi
    return 0
  fi
  printf '    FAIL -- the provocation did not behave as expected\n' >&2
  return 1
}

selftest() {
  local real_python decl_sha after_sha
  local repo_ok repo_noident repo_shallow
  local decl_a decl_b decl_c decl_d decl_mut out rc
  st_total=0
  st_fail=0

  real_python="$(command -v python3 2>/dev/null)" || real_python=""
  if [ -z "$real_python" ]; then
    cannot_assess "python3 is not on PATH, so the provocation battery cannot build its fixtures"
  fi
  if ! decl_sha="$(sha256_of "$decl")"; then
    cannot_assess "sha256sum/shasum is absent, so the contract cannot be pinned against mutation"
  fi

  scratch="/tmp/ao-toolchain-parity.$$.$(date +%s%N)"
  if ! mkdir -p "$scratch/bin-a" "$scratch/bin-b" "$scratch/bin-c" "$scratch/src"; then
    cannot_assess "cannot create a scratch directory under /tmp"
  fi

  # stub interpreters: the conforming one, the wrong-minor one, and the one that
  # is the right interpreter with no declared module installed.
  make_stub "$scratch/bin-a/python3" 3.14.4 "$real_python" 1 || cannot_assess "cannot write the control's stub interpreter"
  make_stub "$scratch/bin-b/python3" 3.12.0 "$real_python" 1 || cannot_assess "cannot write the control's stub interpreter"
  make_stub "$scratch/bin-c/python3" 3.14.4 "$real_python" 0 || cannot_assess "cannot write the control's stub interpreter"

  decl_a="$scratch/decl-a.yaml"   # wrong minor on PATH, and the only declared interpreter
  decl_b="$scratch/decl-b.yaml"   # the declared interpreters disagree on major.minor
  decl_c="$scratch/decl-c.yaml"   # the declared interpreters agree but one lacks a module
  decl_d="$scratch/decl-d.yaml"   # the real interpreter: conforming
  decl_mut="$scratch/decl-mutated.yaml"
  write_decl "$decl_a" "[$scratch/bin-b/python3]" ">=3.14,<3.15"
  write_decl "$decl_b" "[$scratch/bin-a/python3, $scratch/bin-b/python3]" ">=3.14,<3.15"
  write_decl "$decl_c" "[$scratch/bin-a/python3, $scratch/bin-c/python3]" ">=3.14,<3.15"
  write_decl "$decl_d" "[$real_python]" ">=3.14,<3.15"

  repo_ok="$scratch/repo-ok"
  repo_noident="$scratch/repo-noident"
  repo_shallow="$scratch/repo-shallow"
  make_repo "$repo_ok" 1 || cannot_assess "the control's scratch repository could not be created"
  make_repo "$repo_noident" 0 || cannot_assess "the control's scratch repository could not be created"
  make_shallow "$repo_shallow" || cannot_assess "the control's shallow clone could not be created (git clone --depth 1)"

  # control 1 -- toolchain:python-minor (a 3.12 runner, the measured defect)
  rc=0
  out="$(env PATH="$scratch/bin-b:$PATH" AO_TOOLCHAIN_DECL="$decl_a" AO_TOOLCHAIN_REPO="$repo_ok" \
    bash "$self" --assert-only 2>&1)" || rc=$?
  assert_control python-minor toolchain:python-minor 1 "$rc" "$out" || st_fail=$((st_fail + 1))

  # control 2 -- toolchain:git-identity (no identity configured anywhere)
  rc=0
  out="$(env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    AO_TOOLCHAIN_DECL="$decl_d" AO_TOOLCHAIN_REPO="$repo_noident" \
    bash "$self" --assert-only 2>&1)" || rc=$?
  assert_control git-identity toolchain:git-identity 1 "$rc" "$out" || st_fail=$((st_fail + 1))

  # control 3 -- toolchain:shallow (a real --depth 1 checkout)
  rc=0
  out="$(env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    AO_TOOLCHAIN_DECL="$decl_d" AO_TOOLCHAIN_REPO="$repo_shallow" \
    bash "$self" --assert-only 2>&1)" || rc=$?
  assert_control shallow toolchain:shallow 1 "$rc" "$out" || st_fail=$((st_fail + 1))

  # control 4 -- toolchain:interpreter-split, the two declared interpreters differ
  rc=0
  out="$(env PATH="$scratch/bin-a:$PATH" AO_TOOLCHAIN_DECL="$decl_b" AO_TOOLCHAIN_REPO="$repo_ok" \
    bash "$self" --assert-only 2>&1)" || rc=$?
  assert_control interpreter-minor toolchain:interpreter-split 1 "$rc" "$out" || st_fail=$((st_fail + 1))

  # control 5 -- toolchain:interpreter-split, same minor, one is PyYAML-less
  # (the `PATH=/usr/bin:/bin` defect: the deps went into the other interpreter)
  rc=0
  out="$(env PATH="$scratch/bin-a:$PATH" AO_TOOLCHAIN_DECL="$decl_c" AO_TOOLCHAIN_REPO="$repo_ok" \
    bash "$self" --assert-only 2>&1)" || rc=$?
  assert_control interpreter-module toolchain:interpreter-split 1 "$rc" "$out" || st_fail=$((st_fail + 1))

  # control 6 -- the range's UPPER bound is read, not just its lower one: a COPY
  # of the real declaration with the range narrowed below the interpreter must be
  # refused, and the copy's sha256 must differ (the mutation really landed).
  cp "$decl" "$decl_mut" || cannot_assess "cannot copy the declaration for the mutant control"
  sed -i -E 's/^([[:space:]]*range:[[:space:]]*).*$/\1">=3.12,<3.13"/' "$decl_mut"
  if [ "$(sha256_of "$decl_mut")" = "$decl_sha" ]; then
    st_total=$((st_total + 1))
    printf '  control %-22s FAIL -- the mutation did not land in the copy\n' "range-mutant" >&2
    st_fail=$((st_fail + 1))
  else
    rc=0
    out="$(env AO_TOOLCHAIN_DECL="$decl_mut" AO_TOOLCHAIN_REPO="$repo_ok" \
      bash "$self" --assert-only 2>&1)" || rc=$?
    assert_control range-mutant toolchain:python-minor 1 "$rc" "$out" || st_fail=$((st_fail + 1))
  fi

  # control 7 (positive) -- a conforming fixture must be ACCEPTED: a gate that
  # only ever refuses cannot distinguish a conforming runner from a broken one.
  rc=0
  out="$(env AO_TOOLCHAIN_DECL="$decl_d" AO_TOOLCHAIN_REPO="$repo_ok" \
    bash "$self" --assert-only 2>&1)" || rc=$?
  assert_control conforming NONE 0 "$rc" "$out" || st_fail=$((st_fail + 1))

  # the contract is byte-identical after the battery (a control must not edit it)
  st_total=$((st_total + 1))
  if after_sha="$(sha256_of "$decl")" && [ "$after_sha" = "$decl_sha" ]; then
    printf '  control %-22s OK -- the contract is byte-identical after the battery (sha256 %s)\n' \
      "contract-intact" "$decl_sha"
  else
    printf '  control %-22s FAIL -- the contract changed during the battery (%s -> %s)\n' \
      "contract-intact" "$decl_sha" "${after_sha:-<unreadable>}" >&2
    st_fail=$((st_fail + 1))
  fi

  if [ "$st_fail" -eq 0 ]; then
    printf '  provocation battery: %s of %s controls behaved as expected\n' "$st_total" "$st_total"
  else
    printf '  provocation battery: %s of %s controls FAILED\n' "$st_fail" "$st_total" >&2
  fi
  return "$st_fail"
}

# --- main --------------------------------------------------------------------
if [ ! -f "$decl" ]; then
  printf 'check-toolchain-parity: FAIL -- the declared toolchain contract is missing: %s\n' "$decl" >&2
  exit 1
fi
flat="$(flat_decl "$decl")"
if [ -z "$flat" ]; then
  cannot_assess "$decl is not readable as the declared toolchain (no key was understood)"
fi
for key in python.range git.identity git.shallow interpreters modules; do
  if ! decl_get "$flat" "$key" >/dev/null; then
    cannot_assess "$decl does not declare $key (the contract is incomplete)"
  fi
done

if [ "$run_selftest" -eq 1 ]; then
  echo "== toolchain-parity (provocation battery) =="
  st_rc=0
  selftest || st_rc=$?
  if [ "$st_rc" -ne 0 ]; then fail=$((fail + 1)); fi
fi

echo "== toolchain-parity =="
echo "  declaration: $decl"
echo "  repository:  $repo"
rule_python_minor
rule_git_identity
rule_shallow
rule_interpreter_split

if [ "$fail" -ne 0 ]; then
  printf 'check-toolchain-parity: NOT-OK -- %s finding(s); the runner is not the declared toolchain\n' "$fail" >&2
  exit 1
fi
echo "check-toolchain-parity: OK -- the runner matches the declared toolchain contract"
exit 0
