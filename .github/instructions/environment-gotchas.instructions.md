---
description: "Use when: running terminal commands, diagnosing a failed or blocked command, using mktemp/redirects/logs, reasoning about sandbox, network, or filesystem denials, running make verify or other long gates, using the gh CLI, or running terraform init/validate. Condensed facts about how this machine actually runs."
applyTo: "**"
---

# Environment gotchas — CMR

> Condensed from `.github/copilot-instructions.md` → "Environment — how this machine
> actually runs". These are **measured** facts; do not re-derive them, and do not treat a
> blocked command as a broken one.

- **Terminal sandbox.** Commands run sandboxed: network blocked, filesystem read-only
  outside the workspace and `$TMPDIR`. Network-dependent commands (git fetch/push, package
  installs, `gh`, curl) need a network grant; commands writing outside the workspace need
  an unsandboxed grant. **A denial is a signal, not an obstacle** — never retry verbatim,
  never route around it.
- **`$TMPDIR` is not private.** It points at a shared, periodically-cleaned cache, so a
  bare `mktemp -d` can vanish mid-run. Use an explicit template:
  `mktemp -d /tmp/<name>.XXXXXX` (`/tmp` **is** writable).
- **The shell is shared and persistent.** Parallel lanes interleave output into the same
  terminal, so an inline command return can contain another lane's text. Redirect to a
  uniquely-named log (`/tmp/<name>.$(date +%s).log` — not `$$`, constant in this shell)
  and read it back with the file reader, not `cat`.
- **Long gates get signal-killed.** A parallel lane's `Ctrl-C` lands on a foreground
  `make verify`, which dies with `Interrupt` — that is not a code failure. Detach:
  `setsid env -u CATALOG_DIR make verify > /tmp/verify.$(date +%s).log 2>&1; echo "RC=$?"`.
  (Do not run `make verify` from an instruction/parity lane; it is slow and signal-prone.)
- **Gate scope traps.** The shell-syntax scan covers only `SHELL_DIRS` (`board channels
  controller fleet scripts ops`); scripts under `guardrails/**`, `sync/**`, `registry/**`
  are **not** scanned — run `bash -n <script>` directly. `bash -n` exits **2** on a syntax
  error, not 1. A `CATALOG_DIR` exported by another lane leaks into this shell and breaks
  module resolution — use `env -u CATALOG_DIR` or an explicit `CATALOG_DIR=$PWD/catalog/modules`.
- **GitHub CLI.** `gh issue view` is unreliable here (deprecated classic-Projects GraphQL
  returns stale bodies) — use REST `gh api repos/kushin77/CMR/issues/<n>`. `--paginate`
  with `--jq '[...]'` applies the filter per page and concatenates arrays into invalid
  JSON; for whole-list work use `gh issue list --state all --limit 1000 --json ...`. Pinned
  issues are capped at 3 per repo and pin failures are swallowed — verify via GraphQL.
- **Terraform.** `infra/terraform/**` has a root-owned `.terraform` uid 1000 cannot write,
  so pass an isolated data dir **per command** (`env TF_DATA_DIR="$(mktemp -d)" terraform
  init`) — never `export` it into the shared shell. Plugin cache:
  `TF_PLUGIN_CACHE_DIR=/home/akushnir/.terraform.d/plugin-cache`.
- **Secrets.** Never in code, files, or git history — env / GCP Secret Manager only.
  `.gitleaks.toml` is canonical, scanned `--no-git --source .`; editing it requires
  re-syncing the three byte-identical copies under `guardrails/gates/corpus/fixtures/`.
- **Unattended delivery.** GitHub Actions is disabled (GR-15) — verification is code-native
  (`make verify`, run by `ops/run.sh` and cron). Fleet delivery: `controller/app-token.sh`
  + `ops/standards-push.sh` (dry-run by default); scale tripwire:
  `ops/scale-tripwire.sh` (threshold 10).
