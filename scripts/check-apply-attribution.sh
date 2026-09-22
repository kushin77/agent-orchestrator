#!/usr/bin/env bash
# check-apply-attribution.sh — the apply-attribution declaration gate (EPIC #1295
# bullet 2; issue #2006).
#
# EPIC #1295's DoD bullet 2 is "An apply is only ever attributable to the pair's
# deployer identity (audit log)". Its audit-log half needs a live `gcloud` read
# and stays owner-side, but its DECLARATION half is checkable in-repo and was
# previously unchecked — a rule with no gate is a formality (GR-12 / AO-GR-4).
#
# This check asserts the declaration half, offline and deterministically:
#
#   1. exactly ONE build config under `infra/cloudbuild/` runs a command-form
#      `terraform apply` — the repo documents `apply.yaml` as "the ONLY apply
#      route for infrastructure changes (no console path)". A second one is
#      ambiguous attribution, and is refused BY NAME.
#   2. that route names an explicit identity (`serviceAccount:`). An apply route
#      with no identity applies as the builder's default — unattributable.
#   3. when that identity is a substitution (`serviceAccount: $_X`), the paired
#      `*-trigger.yaml` DECLARES `_X:` in its `substitutions:` block. A
#      substitution nobody declares is the #1389 class of defect, and is refused
#      BY NAME rather than discovered on the first live run.
#   4. every `*-trigger.yaml` states `disabled:` EXPLICITLY. Cloud Build treats an
#      absent `disabled` as ENABLED, so an omitted field is a silent enablement.
#   5. the deployer service account is managed as a Terraform module AND its
#      creation is flag-gated (GR-5 / the IaC mandate).
#
# Four NEGATIVE CONTROLS are provoked on scratch copies (never the committed
# files), each required to be refused BY NAME — so this check can never be a
# formality (no-false-green doctrine).
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
# invariants: "the repo declares exactly one apply route, named, and flag-gated"
# gotchas: "Cloud Build treats an absent `disabled:` as ENABLED"
# related: ["#1295", "#2006", "#1415", "#1389"]
# do_not_duplicate: scripts/check-cloudbuild.sh
# ---knowledge---
set -u

source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"
root="$(find_repo_root)"
cd "$root" || exit 2

CB_REL="infra/cloudbuild"
TF_REL="infra/terraform/modules/deployer-sa"

if [ ! -d "$root/$CB_REL" ]; then
  echo "check-apply-attribution: CANNOT-ASSESS — $CB_REL missing" >&2
  exit 2
fi

# analyse <cloudbuild-dir> <deployer-sa-dir>
# Prints one refusal CODE per line, in a stable order. Empty output == clean.
analyse() {
  local cb="$1" sa="$2" f base id sub
  local apply_configs=()

  # 1. exactly one build config that can run `terraform apply`.
  # Detection is deliberately BROAD: any non-comment occurrence counts, in any
  # spelling (`terraform apply`, `args: ["terraform apply"]`). A narrower
  # command-form match is defeated by quoting the arg, and an apply route that
  # hides behind quoting is exactly the attribution gap this gate exists for.
  # False positives are avoided by stripping comments: prose that merely names
  # the apply pipeline (`rollout-promote.yaml`) does not count.
  for f in "$cb"/*.yaml; do
    [ -e "$f" ] || continue
    case "$(basename "$f")" in
      *-trigger.yaml) continue ;;
    esac
    if grep -vE '^[[:space:]]*#' "$f" 2>/dev/null \
       | grep -qE 'terraform[[:space:]]+apply'; then
      apply_configs+=("$f")
    fi
  done
  if [ "${#apply_configs[@]}" -eq 0 ]; then
    echo "APPLY-ROUTE-ABSENT"
  elif [ "${#apply_configs[@]}" -gt 1 ]; then
    echo "APPLY-ROUTE-AMBIGUOUS"
  fi

  # 2 + 3. the apply route names an identity, and its substitution is declared.
  for f in "${apply_configs[@]}"; do
    id="$(grep -m1 -E '^serviceAccount:' "$f" 2>/dev/null | sed 's/^serviceAccount:[[:space:]]*//')"
    if [ -z "$id" ]; then
      echo "APPLY-ROUTE-IDENTITY-MISSING"
      continue
    fi
    case "$id" in
      \$*)
        sub="${id#\$}"
        base="$(basename "$f" .yaml)"
        if [ -f "$cb/$base-trigger.yaml" ]; then
          if ! grep -qE "^[[:space:]]*${sub}:" "$cb/$base-trigger.yaml" 2>/dev/null; then
            echo "APPLY-IDENTITY-UNDECLARED"
          fi
        else
          echo "APPLY-IDENTITY-UNDECLARED"
        fi
        ;;
    esac
  done

  # 4. every trigger states `disabled:` explicitly.
  for f in "$cb"/*-trigger.yaml; do
    [ -e "$f" ] || continue
    if ! grep -qE '^disabled:' "$f" 2>/dev/null; then
      echo "TRIGGER-DISABLED-UNSTATED"
    fi
  done

  # 5. the deployer SA is a flag-gated Terraform module.
  if [ ! -f "$sa/main.tf" ]; then
    echo "DEPLOYER-SA-UNMANAGED"
  elif ! grep -qE 'var\.enabled[[:space:]]*\?' "$sa/main.tf" 2>/dev/null; then
    echo "DEPLOYER-SA-UNMANAGED"
  fi
}

not_ok=0
arms=0

# --- the real tree ---------------------------------------------------------
findings="$(analyse "$root/$CB_REL" "$root/$TF_REL")"
arms=$((arms + 1))
if [ -n "$findings" ]; then
  not_ok=$((not_ok + 1))
  while IFS= read -r code; do
    printf '  FAIL  refused BY NAME: %s\n' "$code"
  done <<<"$findings"
else
  printf '  OK    the apply route is single, named, declared, and flag-gated\n'
fi
printf '  arm   apply attribution declaration half                     expect=clean actual=%s\n' \
  "$([ -z "$findings" ] && echo clean || echo "$(echo "$findings" | tr '\n' ',')")"

# --- four provoked negative controls, on scratch copies ---------------------
provoke() { # <label> <expected-code> <mutate-fn>
  local label="$1" want="$2" mutate="$3" scratch got
  scratch="$(mktemp -d /tmp/ao-applyattr.XXXXXX)" || return
  cp -r "$root/$CB_REL" "$scratch/cb" 2>/dev/null
  cp -r "$root/$TF_REL" "$scratch/sa" 2>/dev/null
  "$mutate" "$scratch" || true
  got="$(analyse "$scratch/cb" "$scratch/sa")"
  arms=$((arms + 1))
  if printf '%s\n' "$got" | grep -qx "$want"; then
    printf '  OK    arm   %-44s expect=%-32s actual=%s\n' "$label" "$want" "$want"
  else
    not_ok=$((not_ok + 1))
    printf '  FAIL  arm   %-44s expect=%-32s actual=%s\n' "$label" "$want" "${got:-none}"
  fi
  rm -rf "$scratch"
}

nc_identity_missing() { # strip the identity off the apply build config
  local s="$1" f
  for f in "$s"/cb/*.yaml; do
    case "$(basename "$f")" in *-trigger.yaml) continue ;; esac
    grep -qE '^serviceAccount:' "$f" 2>/dev/null && sed -i '/^serviceAccount:/d' "$f"
  done
}

nc_second_route() { # plant a second apply route, in the QUOTED-ARG form
  printf '%s\n' 'steps:' '  - id: second-apply' '    args: ["terraform apply"]' \
    > "$1/cb/second-route.yaml"
}

nc_disabled_unstated() { # remove an explicit `disabled:` from the apply route's trigger
  local f
  f="$1/cb/apply-trigger.yaml"
  [ -f "$f" ] || f="$(ls "$1"/cb/*-trigger.yaml 2>/dev/null | head -1)"
  [ -n "$f" ] && sed -i '/^disabled:/d' "$f"
}

nc_identity_undeclared() { # strip the substitution DECLARATION from the paired trigger
  local f
  f="$1/cb/apply-trigger.yaml"
  [ -f "$f" ] || f="$(ls "$1"/cb/*-trigger.yaml 2>/dev/null | head -1)"
  [ -n "$f" ] && sed -i '/^[[:space:]]*_[A-Z0-9_]*:[[:space:]]*/d' "$f"
}

provoke "identity-missing refused"     "APPLY-ROUTE-IDENTITY-MISSING" nc_identity_missing
provoke "second-apply-route refused"   "APPLY-ROUTE-AMBIGUOUS"        nc_second_route
provoke "disabled-unstated refused"    "TRIGGER-DISABLED-UNSTATED"    nc_disabled_unstated
provoke "identity-undeclared refused"  "APPLY-IDENTITY-UNDECLARED"    nc_identity_undeclared

printf '  arms: %s arm(s), %s not-ok\n' "$arms" "$not_ok"

if [ "$not_ok" -ne 0 ]; then
  echo "check-apply-attribution: NOT-OK — $not_ok finding(s)"
  exit 1
fi
echo "check-apply-attribution: OK"
exit 0
