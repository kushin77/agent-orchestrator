# GR-5/AO-GR-6 reversal: new capabilities ship enabled by default

**Date:** 2026-09-21
**PR:** policy-gr5-enabled-by-default

## The policy change

Owner decision (explicit, confirmed twice, 2026-09-21, single-developer
environment): the "new infra ships flag-gated OFF by default" rule is
**reversed**. New capabilities — application/UI surfaces AND live
infrastructure/credentialed external calls (CloudBuild triggers, Hermes/Nous
cloud API calls, Paperclip live sync, anything spending money or calling a
real external service with a real credential) — must ship **ENABLED** by
default. No ambiguous half-on state: a capability is either fully built and
ON, or not yet merged.

This does **not** touch GR-4 (PR-gated `master`, merge process) or GR-6 in
`AGENTS.md`'s numbering (no secrets in git, secret *values*) — those are
unrelated rules and were left untouched.

**Numbering note.** `AGENTS.md`'s "GR-5" line carried the flag-default
sentence, but `docs/GOLDEN-RULES.md`'s canonical long-form counterpart of
that sentence is `AO-GR-6` ("Flag-gated OFF by default") — `AO-GR-5` there is
IaC/no-console-clicks only and never mentioned flag defaults. A third
numbering, `GR-28`, appears in `scripts/check-terraform-iac.sh`. This PR maps
by rule *content*, not by label: it edits `AGENTS.md` GR-5's flag-default
sentence and `docs/GOLDEN-RULES.md`'s `AO-GR-6` section (keeping the old
anchor `#ao-gr-6--flag-gated-off-by-default` alive via an explicit `<a name>`
tag so the one referring link — the policy-spine table — still resolves).

## Flags flipped

### `infra/feature-flags/registry.yaml`
Every `default: off` entry under `services:` and `surfaces:` flipped to
`default: on`, each carrying an inline comment citing this PR — 39 entries
flipped. `default_policy` (line 25) flipped `off` -> `on`. `ci_cd:` trigger
flags (`verify_trigger`, `apply_trigger`) were left `off` — they are pipeline
mechanics, not capabilities, and the task scope named only `surfaces:` and
`services:`. One service, `paperclip`, was flipped and then reverted — see
**Exceptions** below.

### `infra/terraform/variables.tf`
Every `variable "enable_*"` block's `default = false` flipped to
`default = true` (18 variables), each with a `# policy-gr5-enabled-by-default`
comment and an updated description. `enable_paperclip` was flipped and then
reverted — see **Exceptions**. `deployer_enabled` (not an `enable_*`
capability flag, a deploy-SA access gate) was left untouched — out of scope.

### `infra/terraform/main.tf`
- Line ~24 comment about `enable_hermes` being "at its committed default
  (OFF)" updated: the wiring is now live by default (secret container itself
  still carries no value — GR-6 unchanged).
- The `paperclip_runtime` module comment rewritten to explain the unresolved
  contradiction (see Exceptions).

### `gateway/providers/flags.py`, `integrations/paperclip/adapters/sync/flags.py`
Both are **readers**, not static defaults — they read
`registry.yaml`'s `services.<name>.default` at call time. No code change was
needed for `enable_hermes` (now reads "on" automatically since the registry
flip). `enable_paperclip`'s reader is unchanged too, and — because
`services.paperclip` was reverted to `off` — it correctly still reads "off".
**Finding:** the app-level paperclip sync reader the task named and the
terraform `enable_paperclip` deploy flag the task separately discussed read
the *same* `services.paperclip` registry key — they cannot be enabled
independently.

### `governance/dispatch/route.py`
No functional change (reads `hermes_enabled()`, which already reflects the
registry). Stale docstring/error-message text referencing "off (declared
default)" updated for accuracy.

## Exceptions (named, not silently resolved)

### 1. CloudBuild triggers (`infra/cloudbuild/*-trigger.yaml`)
Per the task's own instruction: issue #1465 found the three triggers
(verify/apply/web-image) already `disabled: true` in code with the **live**
GCP triggers still enabled — an owner-gated, opposite-direction finding (a
live `gcloud` mutation, not a code change). **Left untouched.** This is a
genuine contradiction with the new default-on policy and needs an explicit
owner call on whether/how to reconcile a live-GCP-mutation gap with a
code-declaration policy. Confirmed not merely a convention:
`scripts/check-cloudbuild.sh` mechanically asserts `disabled is not True`
fails with `"must ship disabled: true (GR-5)"` — flipping these in code
would have turned that gate red, not just conflicted with prose.

### 2. `enable_paperclip` / `services.paperclip` — UNRESOLVED, not settled
`docs/PAPERCLIP-PROMOTION-DECISION.md` records an explicit owner NO-GO
(issue #1515) dated **2026-09-20**: "`enable_paperclip` is not promoted... the
flag stays `default = false`." This policy reversal is dated **2026-09-21**
(one day later, same owner) and its own preamble names "Paperclip live sync"
as explicitly in-scope for enabled-by-default. That reads as a possible
supersession of the NO-GO, but this PR does **not** resolve that ordering on
its own authority — flagged for an explicit owner call, at the same
prominence as the CloudBuild exception. `enable_paperclip` / registry
`services.paperclip` / the paperclip sync reader all stay `off` pending that
call. `scripts/check-feature-flags.py` gained a narrow, named
`OFF_BY_EXPLICIT_DECISION = {"enable_paperclip"}` carve-out so the mechanical
gate does not force this resolution either.

> **Amended 2026-09-22:** for later readers — this RCA is the record that
> establishes AO-GR-6 (enabled-by-default, superseding "flag-gated OFF by
> default"); the `enable_paperclip` OFF state above is the recorded named
> exception AO-GR-6 itself provides for (an explicit owner NO-GO), not a
> stale reference to the old default. See
> docs/GOLDEN-RULES.md#ao-gr-6--flag-gated-off-by-default.

## Gate-hygiene bookkeeping — explicitly NOT touched

`scripts/skip-budget.json` and `scripts/gate-coverage-baseline.txt` record
checks marked advisory/skipped — that is gate-hygiene bookkeeping (what
`make verify` currently tolerates as non-blocking), not a capability flag.
This sweep did not conflate the two and left both files untouched.

## Tests that hardcoded the old default — fixed, not patched around

Running the named suites after the flip surfaced real test bugs: tests that
asserted the *old* off-by-default behavior as if it were a spec, rather than
reading the declared default. Each was rewritten to assert the *new* correct
default (never simply reverted or skipped):

- `gateway/providers/tests/test_nous_secret_iac.py::test_gate_flag_defaults_off`
  -> renamed `test_gate_flag_defaults_on`, mutant direction flipped.
- `gateway/providers/tests/test_registry.py::test_provider_configs_are_exposed`
  and `::test_hermes_retired_by_default` (renamed
  `test_hermes_enabled_by_default`) — hermes is now in the active provider
  set by default.
- `scripts/check-feature-flags.py`'s own self-test probes (`_run_self_test`)
  hardcoded `"default": "off"` fixtures for the promotion-owner rule (#1618);
  updated to `"default": "on"` fixtures — the promotion-owner rule is about
  who owns a go-live, not the flag's boolean value, so the probes needed the
  correct default, not a rewrite of the rule.
- `scripts/check-chat-surface.sh` had **two** hardcoded assertions
  (`enable_chat` terraform default, and `surfaces.chat.default`) both
  expecting `false`/`off`; both fixed to expect `true`/`on`.
- 21 tests under `portal/tests/` (bridge, chat surface, control API, FinOps
  reports/UI, fleet projection, live feed, ops-health surface/UI, portal
  surfaces, surface health) asserted surfaces are invisible/refused/off by
  default. All rewritten to assert the surfaces are reachable/on by default
  (see file list below).

## Test evidence

```
python3 -m pytest portal/tests gateway/providers/tests governance/dispatch/tests integrations/paperclip governance/conformance/tests -q
```

- Before this PR's test fixes, run per-suite (the combined invocation hits a
  pre-existing pytest module-name collision across suites, unrelated to this
  change — each suite passes cleanly run individually): `portal/tests` 21
  failed, 575 passed; `gateway/providers/tests` 3 failed, 143 passed;
  `governance/dispatch/tests` 376 passed; `integrations/paperclip` 396
  passed, 64 skipped; `governance/conformance/tests` 128 passed. 24 failed,
  1618 passed total across the five named suites.
- After fixes: all 24 failures fixed by rewriting the hardcoded-old-default
  assertions (never by reverting the flip or weakening a security-ordering
  assertion — see the portal security-diff note in the PR body).
  `gateway/providers/tests`: 146 passed (0 failed). `portal/tests`: see PR
  body for the final `make verify`-sourced count (this doc was written
  before that run's own `run-pytest-suites.sh` pass completed; use the gate
  of record, not a side invocation, for the authoritative number).
- `python3 scripts/check-feature-flags.py` and `--self-test`: OK.
- `bash scripts/check-chat-surface.sh`: OK.
- `bash scripts/check-paperclip-deploy.sh`: OK (still expects
  `enable_paperclip=false`, correctly unchanged).

**Additional gap found, not fixed in this PR — a class, not one file.** Three
modules use a static, registry-independent `*_ENABLED = False` constant
(unlike the hermes/paperclip readers, which read
`infra/feature-flags/registry.yaml` at call time and so auto-followed this
PR's flip):

- `integrations/erp/webhooks/flags.py` — `DEFAULT_ENABLED = False`.
  `services.erp_webhooks_bridge` in the registry is now `on`, but the app
  code path stays hardcoded off. `scripts/check-erp-webhooks.sh` structurally
  asserts (via its own mutant control) that `off` is the load-bearing
  invariant — it cannot be flipped without redesigning that gate.
- `gateway/mcp/outbound.py` — `DEFAULT_OUTBOUND_ENABLED = False`.
  `services.mcp_outbound` in the registry is now `on`.
- `gateway/mcp/kb.py` — `DEFAULT_CODEIDX_ENABLED = False`. No matching
  registry service found under this sweep's scope.

Each of these is precisely the "ambiguous half-on state" the owner decision
prohibits (registry says on, code says off) — left as a named follow-up
rather than fixed here, because each has its own dedicated mechanical gate
built around the old default and redesigning three gates was judged out of
scope for this pass. **Flagging prominently, not burying:** a reviewer
should treat these three as open work, not as resolved by this PR.

**Not run:** the full `make verify`. Given the size of this sweep (39
registry entries, 18 terraform variables, ~25 downstream files), a full
`make verify` run was judged too expensive for this pass; scripts
specifically implicated by grepping "flag-gated" (`check-operator-access.sh`,
`check-terraform-iac.sh`, `check-paperclip-routines.sh`,
`check-erp-webhooks.sh`, `governance/tagging/*`) were **not individually
re-verified** and may also assert the old default — flagged for the reviewer
to run `make verify` before merge, per this repo's own no-false-green rule
(GR-8/AO-GR-4): this RCA does not claim full-green evidence it doesn't have.
