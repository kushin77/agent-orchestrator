# Per-agent secret vault primitive (issue #417)

The fleet's first-class primitive for *"this agent, this credential, this
rotation"* — a **GSM-backed reference/rotation view** that **names** a secret and
never carries its value. It is one of the six thin adapters of EPIC #410, and it
is the one place where "adopt the product's feature" would be actively harmful:
two secret stores means two answers to *"what is the credential"*.

## The four rules

1. **Name, never carry.** A view record carries the GSM path, the owning agent,
   the scope, the rotation time and the consuming agent. It carries **no value** —
   the schema has no value-bearing property and `additionalProperties: false`, and
   the primitive refuses a value-bearing *key* by name before it ever reads it
   (GR-6). A finding names the rule and the location, never the matched value.
   The projection **fails closed**: a declaration that carries a value is refused
   outright rather than silently dropped, so a carried value cannot ride along
   behind a clean-looking view.
2. **Secret Manager is the store of record.** Upstream's secret store is **not**
   adopted as our authority: `store` is a JSON-Schema `const` and the gate
   asserts that no second store is introduced. This catalog is a *view* over GSM,
   not a store beside it.
3. **Rotation is expressible.** Every secret records `last_rotated_at` (null when
   never rotated, so it is visible as such) and the `consumer` that reads it. A
   secret with **no consumer is reported**, never silently kept.
4. **A read requires an authenticated, scoped caller.** `read_secret` refuses an
   unauthenticated caller and a caller that does not hold the secret's scope — an
   **unscoped read is refused**, and the refusal is tested.

## Layout

| Path | What it is |
|---|---|
| [`model.py`](model.py) | The shapes: `SecretRef`, `SecretView`, `Caller`, and the typed refusals. |
| [`vault.py`](vault.py) | The deterministic projection, the findings, the read guard. |
| [`cli.py`](cli.py) | Read-only verbs (`validate`, `view`, `catalog`, `rotation`, `orphans`, `read`). |
| [`catalog/secrets.json`](catalog/secrets.json) | The value-free declaration catalog (names, scopes, rotation — no values). |
| [`schema/secret.schema.json`](schema/secret.schema.json) | The frozen reference/rotation view schema. |
| [`tests/`](tests) | The pytest suite. |

The catalog is the **declaration** document (a `store` plus `secrets` rows); the
**view** is its projection, which adds the derived `rotated` boolean so
`last_rotated_at` and `rotated` cannot disagree. `schema/secret.schema.json`
validates the view — the artifact consumers read.

## How it relates to the rest of the fleet

* `identity/` (authentication and scope resolution) and `guardrails/` (DLP,
  egress policy) remain the **enforcement points**. This adapter consumes an
  already-resolved caller identity and adds the primitive and the projection; it
  reads neither and edits neither.
* The frozen seam contracts live in `docs/contracts/paperclip/`, and the HTTP
  transport client over the seam is `integrations/paperclip/`. This package is a
  *family projection* on top of the same seam, not the client.
* Boundaries and ownership: `docs/decision-records/ADR-0012-hermes-paperclip-boundary.md`
  (*map the policy, do not couple the runtime*) and
  `docs/PAPERCLIP-ING-INTEGRATION.md`.

## Verify

```bash
bash scripts/check-paperclip-secrets.sh
python3 -m pytest -q paperclip/adapters/secrets/tests
```

The gate provokes its own failures (GR-12): a value carried instead of a path
reference, a second store introduced, an unscoped read, and an orphaned
(consumer-less) secret must each be refused and reported **by name** — never by
echoing a value.
