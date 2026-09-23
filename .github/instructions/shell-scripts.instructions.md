---
description: "Use when: creating, editing, reviewing, or debugging a shell script (.sh, bash), adding a new script under scripts/ or guardrails/, writing gate or verification shell code, or running bash -n. Covers CMR shell-script standards, real exit codes, and the hard-deny floor."
applyTo: "**/*.sh"
---

# Shell scripts — CMR

> Copilot-discoverable rendering. Canonical sources:
> `guardrails/instructions/agent-operating-doctrine.md` (denied-action rule §7/§7a,
> evidence rule §4) and `docs/PERMISSIONS-OPERATIONS.md` (runtime permission modes and
> the hard-deny floor). Script conventions and the canonical header block:
> `guardrails/instructions/instruction-authoring.md` (DR-045).

## Rules

1. **Real exit codes only (GR-12).** A check that cannot fail is a formality — never
   `|| true` your way past a failure, never exit 0 while doing nothing, never pass on an
   empty scan set. If a script skips, say so loudly and distinguish SKIP from PASS.
2. **Standard hardening.** `set -euo pipefail` at the top of every script; quote variables
   (`"$var"`); prefer `$()` over backticks; `[[ ]]` over `[ ]`; no sub-shell wrappers
   (`bash -c "..."`) unless explicitly asked.
3. **Syntax-check before claiming done.** `bash -n <script>.sh`. It exits **2** on a
   syntax error (not 1). The repo gate's shell-syntax scan covers only `SHELL_DIRS`
   (`board channels controller fleet scripts ops`) — a new script under `guardrails/**`,
   `sync/**`, or `registry/**` is **not** scanned, so run `bash -n` directly, and add a
   self-test that proves the script can both PASS and FAIL.
4. **Headers.** If the file is in the DR-045 header-scanned set, carry the canonical
   header block (Owner-lane / Class / Connects-to / Env / Updated-by / Landed-by) defined
   in `guardrails/instructions/instruction-authoring.md`.
5. **Temporary files.** Use `mktemp -d /tmp/<name>.XXXXXX` — `$TMPDIR` here is a shared,
   periodically-cleaned cache and `$$` is constant in the persistent shell. Redirect long
   output to a uniquely-named log (`/tmp/<name>.$(date +%s).log`) instead of parsing a
   shared terminal.
6. **Hard-deny floor (never bypass).** `guardrails/hooks/shell-aware-deny.sh` and
   `guardrails/hooks/scale-aware-allow.sh` enforce the deny floor for
   `terraform apply` / force-push / history-rewrite classes; a `PreToolUse` deny
   overrides `bypassPermissions`. Compound-form evasions (`true; terraform apply`) are
   covered by `docs/RCA-647.md`'s canonicalizer — run
   `python3 guardrails/tests/run_escape_cases.py` (or `make guardrails-check`) after
   touching hook-adjacent shell logic. Never route around a denial (§7).
7. **Secrets.** Never in code, files, or git history — env / GSM only.

## Pointers

- Behavior/lane rules: `agent-operating-doctrine.instructions.md` · `agent-behavior.instructions.md`.
- Runtime permission modes: `docs/PERMISSIONS-OPERATIONS.md` · RCA: `docs/RCA-647.md`.
- Verification: `make verify` (gate of record) + the script's own `Verify:` command.
