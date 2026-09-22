#!/usr/bin/env bash
# check-apply-attribution.sh — the single-apply-route gate (EPIC #1295 bullet 2;
# issue #2006).
#
# EPIC #1295's DoD bullet 2 is "An apply is only ever attributable to the pair's
# deployer identity (audit log)". Its audit-log half needs a live `gcloud` read
# and stays owner-side; this gate covers the DECLARATION half that nothing else
# enforces.
#
# SCOPE, deliberately narrow. The rest of the declaration story is ALREADY
# enforced elsewhere, and a weaker duplicate of an existing gate is itself a
# finding, so this check must not re-implement it:
#   * that every `*-trigger.yaml` states `disabled:` — scripts/check-cloudbuild.sh
#     reads the declaration's `disabled` and compares it to the recorded live
#     inventory (issue #1415);
#   * that every substitution a build config references is DECLARED — the same
#     check's `template-baseline` half (the #1389 class);
#   * that the deployer SA module's variables are flag-gated OFF — the
#     flag-default-true refusal in scripts/check-terraform-iac.sh (GR-28).
# What is left uncovered, and is the whole of this check:
#   1. exactly ONE build config under `infra/cloudbuild/` can run `terraform
#      apply`. `infra/cloudbuild/apply.yaml` calls itself "the ONLY apply route
#      for infrastructure changes (no console path)", and nothing mechanical
#      holds that claim: a second apply path is ambiguous attribution. Refused
#      BY NAME (APPLY-ROUTE-AMBIGUOUS), and an absent one too
#      (APPLY-ROUTE-ABSENT).
#   2. that route names an explicit identity (`serviceAccount:`). An apply route
#      with no identity applies as the builder default — unattributable.
#
# DETECTION is token-pair based on NON-COMMENT lines: full-line comments are
# dropped, then trailing comments, then a `terraform`/`apply` pair is required on
# a line that can BE a command. A YAML `key: value` line is skipped unless its key
# is command-shaped (`args`/`entrypoint`/`script`/`command`), so prose such as
# `description: "applies the terraform change"` cannot manufacture a second apply
# route — a false positive would red a clean tree. That catches `terraform apply`,
# `terraform -auto-approve apply` and `args: ["terraform", "apply"]`.
# It is NOT a YAML parse. Two limits are documented rather than hidden, and both
# are load-bearing for a reader: a block scalar splitting the two tokens across
# lines evades it, and the trailing-comment strip cuts at ` #`, so a `#` inside a
# quoted shell string would truncate a real command. Arm 2 probes the detector.
#
# Five arms run: one tree arm, one detector probe, and three provoked negative
# controls on `mktemp -d` scratch copies (never the committed files). Each
# provoked arm must be refused BY NAME and must PROVE its mutation took effect —
# an arm whose mutant is byte-identical to the original proves nothing and is
# reported, not counted as a pass. A scratch that cannot be created, or an
# unexpected arm count, yields CANNOT-ASSESS, never a silent OK (AO-GR-4: a gate
# whose failure paths collapse into one exit code is a formality).
#
# Precondition: `sed -i` is used for the scratch mutations, so the mutation arms
# assume GNU sed. On a BSD sed the mutation no-ops, the cksum matches, and the run
# reports CANNOT-ASSESS — loud, never a false green.
#
# Exit-code contract: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.
# Usage: bash scripts/check-apply-attribution.sh
#
# ---knowledge---
# module_id: scripts.check-apply-attribution
# system: governance
# app: gates
# solution_class: enterprise
# patterns: [tri-state-exit, provoked-negative-control, no-false-green, offline-hermetic, single-apply-route]
# derives_from: scripts/check-cloudbuild.sh
# owner_sme: iac-sme
# tier: L1
# interfaces: [exit 0 OK, exit 1 NOT-OK, exit 2 CANNOT-ASSESS]
# invariants: "exactly one apply route is declared, and it names its identity"
# gotchas: "detection is token-pair based on non-comment lines, not a YAML parse"
# related: ["#1295", "#2006", "#1415", "#1389"]
# do_not_duplicate: scripts/check-cloudbuild.sh
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

CB_REL="infra/cloudbuild"
CB="$root/$CB_REL"

if [ ! -d "$CB" ]; then
  echo "check-apply-attribution: CANNOT-ASSESS — $CB_REL missing" >&2
  exit 2
fi

scratch=""
cleanup() { [ -n "$scratch" ] && rm -rf "$scratch"; }
trap cleanup EXIT

# body <file> — the file's non-comment text. Cut only at ` #` so a `#` inside a
# quoted shell string does not truncate a real command.
body() { grep -vE '^[[:space:]]*#' "$1" 2>/dev/null | sed 's/ #.*//'; }

# can_apply <file> — a terraform/apply token pair on a line that can BE a command.
can_apply() {
  local txt hits
  txt="$(body "$1")"
  # The containment test is `[ -n "$hits" ]`, never a quiet grep at the end of a
  # pipe: `grep -q` exits at its first match, so it can SIGPIPE a producer that is
  # still writing — the defect `check-verdict-contains` exists to refuse (#2017).
  # Only the *quiet* grep is gone; the filtering greps and their case-insensitivity
  # are unchanged, so `hits` holds exactly what the old pipeline would have matched.
  hits="$(printf '%s\n' "$txt" \
    | grep -vE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_-]*:[[:space:]]' \
    | grep -iE 'terraform[^#]*\bapply\b|\bapply\b[^#]*\bterraform\b')"
  [ -n "$hits" ] && return 0
  hits="$(printf '%s\n' "$txt" \
    | grep -iE '^[[:space:]]*("?args"?|"?entrypoint"?|"?script"?|"?command"?):' \
    | grep -iE 'terraform[^#]*\bapply\b|\bapply\b[^#]*\bterraform\b')"
  [ -n "$hits" ]
}

# apply_routes <cloudbuild-dir> — one path per apply-capable build config.
apply_routes() {
  local f
  for f in "$1"/*.y*ml; do
    [ -e "$f" ] || continue
    case "$(basename "$f")" in *-trigger.yaml) continue ;; esac
    can_apply "$f" && printf '%s\n' "$f"
  done
}

# analyse <cloudbuild-dir> — refusal codes, one per line; empty == clean.
analyse() {
  local cb="$1" routes r n id
  routes="$(apply_routes "$cb")"
  n="$(printf '%s\n' "$routes" | grep -c .)"
  if [ "$n" -eq 0 ]; then echo "APPLY-ROUTE-ABSENT"; return; fi
  if [ "$n" -gt 1 ]; then echo "APPLY-ROUTE-AMBIGUOUS"; return; fi
  r="$routes"
  id="$(grep -m1 -E '^serviceAccount:' "$r" 2>/dev/null | sed 's/^serviceAccount:[[:space:]]*//')"
  [ -z "$id" ] && echo "APPLY-ROUTE-IDENTITY-MISSING"
  return 0
}

arms=0
not_ok=0
cannot=0

# --- arm 1: the committed tree is clean -------------------------------------
arms=$((arms + 1))
findings="$(analyse "$CB")"
if [ -n "$findings" ]; then
  not_ok=$((not_ok + 1))
  printf '  FAIL  the committed tree is refused: %s\n' "$(printf '%s' "$findings" | tr '\n' ',')"
else
  printf '  OK    exactly one apply route, and it names its identity\n'
fi

# --- arm 2: the detector probe (the claim above, measured) ------------------
arms=$((arms + 1))
scratch="$(mktemp -d /tmp/ao-applyattr.XXXXXX 2>/dev/null)"
if [ -z "$scratch" ]; then
  cannot=$((cannot + 1))
  printf '  FAIL  arm   %-42s expect=%-28s actual=scratch-unavailable\n' "detector probe" "detected"
else
  printf '%s\n' 'steps:' '  - id: p' '    args: ["terraform", "apply"]' > "$scratch/probe.yaml"
  if can_apply "$scratch/probe.yaml"; then
    printf '  OK    arm   %-42s expect=%-28s actual=detected\n' "detector catches arg-split" "detected"
  else
    not_ok=$((not_ok + 1))
    printf '  FAIL  arm   %-42s expect=%-28s actual=missed\n' "detector catches arg-split" "detected"
  fi
  rm -rf "$scratch"; scratch=""
fi

# --- provoke <label> <expected-code> <mutate-fn> ----------------------------
provoke() {
  local label="$1" want="$2" mutate="$3" before after got
  arms=$((arms + 1))
  scratch="$(mktemp -d /tmp/ao-applyattr.XXXXXX 2>/dev/null)"
  if [ -z "$scratch" ] || ! cp -r "$CB" "$scratch/cb" 2>/dev/null; then
    cannot=$((cannot + 1))
    printf '  FAIL  arm   %-42s expect=%-28s actual=scratch-unavailable\n' "$label" "$want"
    [ -n "$scratch" ] && rm -rf "$scratch"; scratch=""
    return 0
  fi
  before="$(find "$scratch/cb" -type f -exec sha256sum {} + 2>/dev/null | LC_ALL=C sort | sha256sum)"
  "$mutate" "$scratch/cb" || true
  after="$(find "$scratch/cb" -type f -exec sha256sum {} + 2>/dev/null | LC_ALL=C sort | sha256sum)"
  if [ "$before" = "$after" ]; then
    cannot=$((cannot + 1))
    printf '  FAIL  arm   %-42s expect=%-28s actual=mutation-was-a-noop\n' "$label" "$want"
    rm -rf "$scratch"; scratch=""
    return 0
  fi
  got="$(analyse "$scratch/cb")"
  # Exact-line match, bash-native (`contains` from scripts/lib/common.sh, already
  # sourced): wrapping both sides in newlines makes a substring test an exact LINE
  # test. No quiet grep sits at the end of a pipe (#2017).
  if contains $'\n'"$got"$'\n' $'\n'"$want"$'\n'; then
    printf '  OK    arm   %-42s expect=%-28s actual=%s\n' "$label" "$want" "$got"
  else
    not_ok=$((not_ok + 1))
    printf '  FAIL  arm   %-42s expect=%-28s actual=%s\n' "$label" "$want" "${got:-none}"
  fi
  rm -rf "$scratch"; scratch=""
}

nc_identity_missing() { # strip the identity off the apply build config
  local f
  for f in "$1"/*.y*ml; do
    case "$(basename "$f")" in *-trigger.yaml) continue ;; esac
    can_apply "$f" && sed -i '/^serviceAccount:/d' "$f"
  done
}

nc_second_route() { # plant a second apply route, in the arg-split spelling
  printf '%s\n' 'steps:' '  - id: second-apply' '    args: ["terraform", "apply"]' \
    > "$1/second-route.yaml"
}

nc_absent_route() { # remove the only apply route's command line
  sed -i '/terraform[^#]*apply/d;/\bapply\b[^#]*terraform/d' "$1"/apply.yaml 2>/dev/null || true
}

provoke "identity-missing refused"   "APPLY-ROUTE-IDENTITY-MISSING" nc_identity_missing
provoke "second-apply-route refused" "APPLY-ROUTE-AMBIGUOUS"        nc_second_route
provoke "absent-apply-route refused" "APPLY-ROUTE-ABSENT"           nc_absent_route

printf '  arms: %s arm(s), %s not-ok, %s cannot-assess\n' "$arms" "$not_ok" "$cannot"

# An arm that vanished is a false green: the expected count is load-bearing.
if [ "$arms" -ne 5 ]; then
  echo "check-apply-attribution: CANNOT-ASSESS — expected 5 arms, ran $arms"
  exit 2
fi
if [ "$cannot" -ne 0 ]; then
  echo "check-apply-attribution: CANNOT-ASSESS — $cannot arm(s) could not be provoked"
  exit 2
fi
if [ "$not_ok" -ne 0 ]; then
  echo "check-apply-attribution: NOT-OK — $not_ok finding(s)"
  exit 1
fi
echo "check-apply-attribution: OK"
exit 0
