#!/usr/bin/env bash
# terraform gate for `make verify` (issue #6): format + offline validate of
# infra/terraform. Honest degrade contract:
#
#   * terraform binary absent        -> visible SKIP (never silent, never false-fail)
#   * no local provider cache        -> validate SKIPs visibly (offline box cannot
#                                       resolve providers); fmt still runs
#   * cache present                  -> offline `terraform init` + `terraform
#                                       validate` MUST pass; any failure is real
#
# `terraform init` is always offline (-plugin-dir pointing at the local cache)
# and uses a throwaway TF_DATA_DIR so nothing is written into the repo tree.
# Usage: scripts/check-terraform.sh [all|fmt|validate]
set -u

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tf_root="$root/infra/terraform"

command -v python3 >/dev/null 2>&1 || { echo "check-terraform: FAIL — python3 not found" >&2; exit 1; }

tf_fmt() {
  if ! command -v terraform >/dev/null 2>&1; then
    echo "  SKIP  terraform fmt (terraform not installed)"
    return 0
  fi
  if [ ! -d "$tf_root" ]; then
    echo "  FAIL  $tf_root missing" >&2
    return 1
  fi
  if ! terraform fmt -check -recursive "$tf_root"; then
    echo "  FAIL  terraform fmt — run: terraform fmt -recursive $tf_root" >&2
    return 1
  fi
  echo "  OK    terraform fmt"
  return 0
}

tf_validate() {
  if ! command -v terraform >/dev/null 2>&1; then
    echo "  SKIP  terraform validate (terraform not installed)"
    return 0
  fi
  if [ ! -d "$tf_root" ]; then
    echo "  FAIL  $tf_root missing" >&2
    return 1
  fi

  # Local provider cache (offline validation). TF_PLUGIN_CACHE_DIR wins.
  local cache="${TF_PLUGIN_CACHE_DIR:-}"
  if [ -z "$cache" ] && [ -d "$HOME/.terraform.d/plugin-cache" ]; then
    cache="$HOME/.terraform.d/plugin-cache"
  fi
  if [ -z "$cache" ]; then
    echo "  SKIP  terraform validate (no local provider cache; offline validate unavailable)"
    return 0
  fi

  local td
  td="$(mktemp -d)"
  local rc=0

  # terraform writes the dependency lock file next to the configuration even
  # with TF_DATA_DIR redirected; record whether it pre-existed so we can clean
  # up the copy this run generates (locks are not committed here).
  local lock_file="$tf_root/.terraform.lock.hcl"
  local lock_preexisting=0
  [ -f "$lock_file" ] && lock_preexisting=1

  if ! (cd "$tf_root" && TF_DATA_DIR="$td" terraform init -backend=false -plugin-dir="$cache" -input=false >/dev/null 2>&1); then
    echo "  FAIL  terraform init (offline, plugin-dir=$cache)" >&2
    [ "$lock_preexisting" -eq 0 ] && rm -f "$lock_file"
    rm -rf "$td"
    return 1
  fi
  if ! (cd "$tf_root" && TF_DATA_DIR="$td" terraform validate -no-color); then
    echo "  FAIL  terraform validate" >&2
    [ "$lock_preexisting" -eq 0 ] && rm -f "$lock_file"
    rm -rf "$td"
    return 1
  fi
  [ "$lock_preexisting" -eq 0 ] && rm -f "$lock_file"
  rm -rf "$td"
  echo "  OK    terraform validate"
  return 0
}

mode="${1:-all}"
failed=0
echo "== terraform =="
case "$mode" in
  fmt)      tf_fmt || failed=1 ;;
  validate) tf_validate || failed=1 ;;
  all)      tf_fmt || failed=1
            tf_validate || failed=1 ;;
  *) echo "check-terraform: unknown mode '$mode' (all|fmt|validate)" >&2; exit 1 ;;
esac

if [ "$failed" -ne 0 ]; then
  echo "terraform: FAILED" >&2
  exit 1
fi
echo "terraform: OK"
