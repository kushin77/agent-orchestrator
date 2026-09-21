# hermes-head guardrail canary (issue #1519)

The canary scope in which `guardrails/policy/bundles/platform/hermes-head.yaml`
is **proven to fire one BLOCK**, before any decision to promote its control.

- Parent: issue #1510 (HERMES-REVIEW-2026-09-20 §2,4 item 2 / top-5 item 4).
- Policy: [`../bundles/platform/hermes-head.yaml`](../bundles/platform/hermes-head.yaml) (issue #951, ADR-0029 — consumed unmodified; this lane writes no policy rules).
- Control: `hermes-head-guardrails` in [`../controls.yaml`](../controls.yaml) — production, still `enabled: false`.
- Canary registry: [`controls.canary.yaml`](controls.canary.yaml) — the same control, `enabled: true`.

## The scope

```
registry : guardrails/policy/canary/controls.canary.yaml   (one control, ON)
bundle   : guardrails/policy/bundles/platform/hermes-head.yaml
```

The scope is deliberately narrow: it loads only the hermes-head policy, so the
BLOCK it proves can only have come from that policy, and it registers only the
one control that policy references. There is therefore no second copy of the
other nine registry entries to keep in sync, and the bundle is the shipped one
— no duplicate policy file exists.

It is **not** a promotion. `default_controls_file()` still resolves to
`guardrails/policy/controls.yaml`, nothing loads the canary path unless a caller
names it, and production still ships the control OFF (asserted by the test suite
and by `scripts/check-guardrail-controls.sh`, which refuses any registry entry
that ships ON).

## The proof

Run from the repository root. Every command below was run on
`HEAD=0002a852` (`issue-1519`) and the output is verbatim.

### 1. The canary scope is a committed artifact, and production is unchanged

```console
$ python3 guardrails/policy/cli.py controls --controls guardrails/policy/canary/controls.canary.yaml
id                           enabled   mode    name
hermes-head-guardrails       on        block   Hermes head-of-org guardrails
rc=0

$ python3 guardrails/policy/cli.py controls | sed -n '1p;/hermes/p'
id                           enabled   mode    name
hermes-head-guardrails       off       block   Hermes head-of-org guardrails
rc=0
```

### 2. One BLOCK fires, and it names the rule

```console
$ python3 guardrails/policy/cli.py evaluate agent.dispatch \
    --subject hermes --tenant acme --context '{"channel":"sideband"}' \
    --bundle guardrails/policy/bundles/platform/hermes-head.yaml \
    --controls guardrails/policy/canary/controls.canary.yaml \
    --audit /tmp/ao1519/canary-audit.jsonl
audit: appended 1 record to /tmp/ao1519/canary-audit.jsonl
{
  "action": "agent.dispatch",
  "decision": "block",
  "error": null,
  "matched_rules": [
    {
      "action": "agent.dispatch",
      "decision": "block",
      "policy_id": "hermes-head-guardrails",
      "policy_version": 1,
      "reason": "hermes agent.dispatch bypassed the gateway (channel=sideband); every directive must go through the gateway",
      "rule_id": "block-directive-off-gateway",
      "subject": "hermes",
      "tenant": "acme"
    }
  ],
  "policies_consulted": ["hermes-head-guardrails"],
  "subject": "hermes",
  "tenant": "acme",
  "uncovered": false
}
rc=2
```

`uncovered: false` is the load-bearing field: the policy **governs** the action,
so this is the named rule refusing it — not the engine's uncovered/deny-by-default
fallback, and not the policy's own `default: block`.

### 3. The block reaches `audit.py`'s ledger on disk

`--audit` sinks the decision through the real `JsonlAuditLog`
([`../audit.py`](../audit.py)) — no bespoke writer:

```console
$ cat /tmp/ao1519/canary-audit.jsonl
{"action": "agent.dispatch", "decision": "block", "error": null, "evidence": {...},
 "outcome": "blocked", "policy_ids": ["hermes-head-guardrails"],
 "reason": "hermes agent.dispatch bypassed the gateway (channel=sideband); every directive must go through the gateway",
 "rule_ids": ["block-directive-off-gateway"], "sequence": 1, "subject": "hermes",
 "tenant": "acme", "timestamp": "2026-09-20T19:32:30+00:00"}
```

### 4. Negative control — the allow-list is not refusing everything

The *same* action, differing only in `channel`:

```console
$ python3 guardrails/policy/cli.py evaluate agent.dispatch \
    --subject hermes --tenant acme --context '{"channel":"gateway"}' \
    --bundle guardrails/policy/bundles/platform/hermes-head.yaml \
    --controls guardrails/policy/canary/controls.canary.yaml \
    --audit /tmp/ao1519/canary-audit.jsonl
{ "decision": "log", "matched_rules": [ { "rule_id": "allow-dispatch-via-gateway", ... } ],
  "uncovered": false, ... }
rc=0

$ wc -l /tmp/ao1519/canary-audit.jsonl
2
```

Two records, append-only, one `blocked` and one `allowed`.

> **Property observed while measuring this (not a defect this lane owns):** each
> CLI run constructs its own engine, and `AuditRecord.sequence` is that engine's
> counter — so two *separate processes* both write `sequence: 1`. Within one
> engine the sequence is monotonic (asserted in the test suite); a ledger that
> needs a global sequence across processes is the observability lane's
> tamper-evident ledger (issue #31), which `audit.py` names as its consumer.

### 5. The differential — the canary flip is what makes the rule fire

```console
$ python3 guardrails/policy/cli.py evaluate agent.dispatch \
    --subject hermes --tenant acme --context '{"channel":"sideband"}' \
    --bundle guardrails/policy/bundles/platform/hermes-head.yaml
{ "decision": "block", "matched_rules": [], "policies_consulted": [], "uncovered": true }
rc=2
```

With the **shipped** registry the same action is `uncovered` — no policy is
active, so no rule fires. The named BLOCK in step 2 therefore comes from the
canary registry, not from some other active policy.

## The gate that keeps it honest

`guardrails/policy/tests/test_hermes_guardrail_canary.py` (15 cases) runs inside
the already-declared `guardrails/policy` suite
([`scripts/pytest-suites.txt`](../../../scripts/pytest-suites.txt), named by
[`scripts/check-pytest-suites.sh`](../../../scripts/check-pytest-suites.sh)), so
it is exercised by the gate of record with no new wiring. It asserts:

| Assertion | Why it cannot pass vacuously |
|---|---|
| The canary registry enables exactly one control, `hermes-head-guardrails` | a second enabled control fails the test by id |
| The canary entry mirrors the shipped control field for field, apart from `enabled` / `on_since_rationale` / `notes` | drift in `name`/`description`/`mode`/`implemented_by`/`since` fails by field name |
| Production still ships the control `enabled: false` | a silent promotion fails |
| `agent.dispatch` off-gateway is `BLOCK`, `blocked is True`, **`uncovered is False`** | a generic default-deny would leave `uncovered` true (or match no rule) and fail |
| The ledger on disk holds a record with `outcome="blocked"` and `rule_ids == ["block-directive-off-gateway"]` | a decision that never reached the sink fails |
| A fresh reader reloads that same record from disk | an in-memory-only decision fails |
| `channel == gateway` is `LOG`/not blocked; `model.call` at 0.2 utilization is not blocked | an over-broad allow-list-as-deny-list fails |
| The two synthetic actions differ only in `channel` | a BLOCK attributable to subject/tenant/action name fails |
| Forcing the canary control back to `enabled: false` stops the named rule firing | a rule that fires regardless of the control fails |
| The shipped registry does not fire the named rule (differential) | a fixture-only proof fails |
| The same two outcomes reproduce with the **whole** `bundles/platform` bundle loaded | an interference from another active policy fails |

## `enable_hermes` (E4) — status, measured

`infra/feature-flags/registry.yaml` carries `tf_flag: enable_hermes` for the
`hermes` provider (`promoted: false`, phase 2), and the registry's own entry
states the boundary:

> The declared `enable_hermes` variable is the switch itself; the gateway
> provider does not yet read it, so today the switch is a declared kill-switch
> surface only, not a live gate on the in-process adapter.

So the flag exists as a declaration but is not read by any call site, and it
gates the *gateway provider*, not the policy engine. Two consequences, stated
plainly:

- **No temporary override was needed, and none was added.** The canary proves
  the policy engine's behaviour, which does not consult `enable_hermes`. Nothing
  in this lane weakens a flag, and the merged PR leaves no override behind.
- **The flag being declared-only is a named boundary of this proof.** This lane
  does not claim production rollout, and does not claim a live hermes call site
  consults the policy — wiring `PolicyEngine.evaluate` into the gateway
  provider adapters is follow-up work owned outside `guardrails/**` (#888/#889,
  ADR-0029), and `guardrails/README.md` already names it.

## Not claimed

- **No production promotion.** The control stays OFF in
  `guardrails/policy/controls.yaml`. Promotion is a separate recorded decision
  citing E1's ADR; this file is not that decision.
- **No live service check.** The guardrail plane is offline by construction (no
  server, no network: `guardrails/policy/README.md`). The "live check" here is a
  real command run against this repository's real artifacts, with the decision
  written to a real ledger on disk — it is not a claim about a running hermes
  process, because no `PolicyEngine.evaluate` call site exists yet.
- **No new policy rules.** `hermes-head.yaml` is consumed unmodified.

## Retirement

Delete `controls.canary.yaml` (and this record) when the control's production
promotion is decided and recorded, or supersede the scope with a wider canary.
