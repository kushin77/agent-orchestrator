# Approvals as a projected resource (issue #416, EPIC #410)

Upstream paperclip.ing models an **approval** as a first-class object — approve
a hire, approve a budget top-up, approve/override a strategy — carrying an actor
and an audit trail. The fleet already holds the *authority* for each of those;
what it lacked was an object an operator or an API consumer can act on.

This adapter publishes that object. It obeys the EPIC's one rule:

> **Projection, not authority.** The ledger stays the writer; the adapter
> derives and maps.

There is **no store**. The package has no write path at all — `project` and
`verify` are pure functions of the tree they are handed, and the only way to
change an approval's state is to change the authoritative record it names.

## Kinds and their authority (the mapping)

| Kind | Authority surface | Authoritative store | Grant | Deny | Deciders |
|---|---|---|---|---|---|
| `hire` | `governance/dispatch` | `.board/claims/*.json` (`ClaimEvent`) | `claim` / `take-over` | `reap` | `agent` |
| `top-up` | `.fleet/sent` (brain directive ledger) | `.fleet/sent/*.json` | directive with `approval{kind:"top-up",decision:"grant"}` | same, `decision:"deny"` | `brain` |
| `override` | `fleet/control.py` (the control verb) | `.fleet/sent/*.json` | directive with `control == "override"` | same, with `approval.decision:"deny"` | `operator` |

A **hire** projects from the claim ledger the fleet already writes: a
`claim` / `take-over` event is the grant, a `reap` event is the deny, and the
event's `agent` is the actor. `release` closes a live claim but decides nothing,
so it is deliberately not a decision.

A **top-up** projects from the brain directive ledger. The top-up decision is
carried on the directive under the additive `approval` envelope key — additive
because `fleet/channel.validate` checks the fields it knows and tolerates one
more, so an order may carry it without ceasing to be a valid directive. The
**producer** of a top-up directive (the brain emitting one when it grants) is
adoption work named in `docs/PAPERCLIP-ING-INTEGRATION.md` §5.7, exactly as for
the budget adapter; this lane declares the authority and the encoding and
refuses a top-up that carries no record.

An **override** projects from the directive the control verb orders. That shape
already exists on the fleet today — `fleet/control.py override` orders the
brain, and `fleet/brain.py build_directive` marks the directive it issues with
`control = "override"` — so the override path needs no additive field, and the
actor is the operator who ordered it.

## Requests and `pending`

A **request** is an operator order in `.fleet/brain/inbox/*.json` carrying an
`approval` marker with a `kind` and a `subject` but **no decision**:

```json
{"from":"operator","to":"brain","type":"directive","id":"order-topup-1",
 "task":{"issue":416},
 "approval":{"kind":"top-up","subject":"budget:agent/paperclip","requested_by":"operator"}}
```

An approval is `pending` **exactly when** no authoritative decision record
exists for its `(kind, subject)`. It becomes `granted` or `denied` only when the
authority records a decision. This is the whole of the autonomy rule:

> **Autonomy is granted, never default.** There is no default-grant path — with
> no request and no decision there is no approval at all, and a request with no
> decision can never be promoted by editing the projection.

## Refused, by name (fail closed)

| Code | Provoked by |
|---|---|
| `no-authority` | a kind with no authoritative surface behind it (e.g. `transfer`) |
| `projection-without-record` | a `granted` / `denied` approval with no `decision_ref`, or a projection promoted past its authority |
| `record-absent` | an approval citing a record the authority does not hold |
| `record-contradicts` | a projection whose state/actor disagrees with the record |
| `double-approval` | one `(kind, subject)` granted more than once |
| `conflicting-decision` | one `(kind, subject)` both granted and denied |
| `unauthorised-decider` | a decider whose role is outside the kind's authority |
| `orphan-decision` | an authoritative decision no approval projects |
| `malformed-record` / `malformed-request` | a record or request missing a required field |
| `authority-mismatch` | a projection naming an authority other than the kind's |
| `schema-violation` | a projected approval outside `schema/approval.schema.json` |

## Files

| File | Role |
|---|---|
| `model.py` | kinds, states, the `AUTHORITIES` map, `Approval` / `Decision` / `Request` / `Finding` |
| `mapping.py` | the deterministic projection over the three read-only surfaces |
| `verify.py` | re-reads the authority behind every approval; determinism check |
| `schema.py` + `schema/approval.schema.json` | stdlib JSON-Schema subset validator + the projected shape |
| `cli.py` | `project` / `verify` / `authority` |
| `fixtures.py` | the deterministic fixture tree the tests, the negative controls and the gate share |
| `negative_control.py` | provokes every refusal the adapter claims (GR-12) |

## Verify

```bash
bash scripts/check-paperclip-approvals.sh
python3 integrations/paperclip/adapters/approvals/cli.py verify
python3 -m pytest -q integrations/paperclip/adapters/approvals/tests
```

## The signed record (issue #1272) — a second, distinct authority

Everything above is a *projection*. Issue #1272 is the opposite problem: for
`merge` / `delete` / `pause` / `flip` scopes there was never an authoritative
record to project — "approved" typed in chat was the whole authority. `record.py`
is that authority: `ApprovalRecord{actor, kind, target, ts, expires, signature}`,
HMAC-SHA256-signed with `AO_APPROVALS_HMAC_KEY` (declared in
`infra/env/registry.yaml`, `secret: true`; no key, no key literal in this
package — fail closed, GR-6), stored one JSON file per scope under
`.fleet/approvals/`. `record_cli.py` is the write path:

```bash
python3 integrations/paperclip/adapters/approvals/record_cli.py grant --actor operator --scope merge:pr#123 --ttl-seconds 900
python3 integrations/paperclip/adapters/approvals/record_cli.py check --scope merge:pr#123
python3 integrations/paperclip/adapters/approvals/record_cli.py list
python3 integrations/paperclip/adapters/approvals/record_cli.py self-test
```

`check` refuses by name: `approval-missing:<scope>`, `approval-tampered:<scope>`,
`approval-expired:<scope>`, `approval-used:<scope>` (a `merge` scope is
single-use — the record is marked used on its first successful `check`; the
other three kinds are re-checkable). `scripts/merge-pr.sh` calls `check` before
merging, behind `AO_APPROVAL_REQUIRED=1` (default off — the runner rung flips
it later); `scripts/check-pr-queue-squash-guard.sh` is the negative control
proving the missing-record refusal and the grant->merge path.

`scripts/check-paperclip-approvals.sh` is **not yet wired** into
`make verify`: the three shared build files (`Makefile`, `scripts/verify.sh`,
`scripts/pytest-suites.txt`) are owned by the EPIC's wiring lane **#420**, and
this lane must not edit them. Until #420 lands, the gate and the suite are run
directly, as the EPIC's own `Verify:` block does.
