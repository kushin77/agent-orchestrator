# `integrations/erp/api/` — the ERP module's REST surface

The ERP module's **REST + OpenAPI lane** (issue [#651](https://github.com/kushin77/agent-orchestrator/issues/651),
EPIC [#645](https://github.com/kushin77/agent-orchestrator/issues/645), ERP-06): an
OpenAPI-declared API over the indexer-fed document model of
[`integrations/erp/core/`](../core/), so the portal (ERP-07, #652) and
external consumers integrate through **one contract**. The assembly precedent is
[`integrations/paperclip/api/`](../../paperclip/api/README.md).

## What the lane answers

| Question | Answer | Where |
|---|---|---|
| What is the contract? | The `openapi.json` the gate re-emits from ERP-02, byte for byte | [`openapi.py`](openapi.py), [`openapi.json`](openapi.json) |
| Which routes exist? | Seven, derived from the model's kind vocabulary and each workflow's declared moves | [`routes.py`](routes.py) |
| Who may do what? | Whatever `integrations/erp/auth` decides — asked once, from one place | [`surface.py`](surface.py) |
| What is stored? | Only documents ERP-02 validated, in a tenant-keyed store | [`store.py`](store.py) |
| What can go wrong? | A closed boundary vocabulary, plus the model's refusals carried unchanged | [`errors.py`](errors.py) |
| Is it up? | Three real dependency readings, never a green lie | [`health.py`](health.py) |

## The four acceptance criteria, and how each is measured

1. **`openapi.json` is generated from ERP-02 schemas — never hand-maintained.**
   Every component *is* the schema file, with exactly two edits: each `$ref` is
   rewritten to point at the component its target file became, and `$id` is dropped
   (its job — naming the file — is done by `x-erp-schema-sources`). OpenAPI 3.1's
   Schema Object is JSON Schema 2020-12, so ERP-02 needs no translation at all: the
   document carries the model's own constraints, titles, descriptions and harvest
   records. Measured three ways — the components are re-derived and compared, every
   `$ref` in the document is resolved, and the **committed artifact is compared to a
   fresh emission** by the gate (`cmp`), so "never hand-maintained" is a diff rather
   than a promise.
2. **CRUD over the document families works offline with deterministic fixtures.**
   `read`/`create`/`replace`/`delete`/`advance` are exercised over **every kind the
   model declares**, from a fixed corpus with one fixed date, through the same model
   the API enforces; `cli.py check` re-validates the whole corpus on every run and
   `cli.py demo` runs the golden path twice and compares the transcripts.
3. **Auth/scope is delegated to ERP-08; no back door.** `Surface.authorize_request`
   is the module's **only** call into `integrations/erp/auth` — counted in the source
   by the gate — the request's tenant is always `principal.tenant`, no parameter of
   `handle` or `authorize_request` can name a tenant or a scope team, and no route in
   the document declares a tenant parameter. The gate proves the property *and* its
   consequence: it replaces the authorizer with one that always allows and requires
   the control driver to turn red.
4. **Negative controls.** An invalid payload is refused **with the model's own code
   and the house envelope** (`schema_violation`, `unbalanced_posting`,
   `invalid_transfer`, …), an unknown route 404s (`not_found`), and
   [`negative_control.py`](negative_control.py) provokes **every** refusal the
   surface can deliver — 8 boundary codes, 7 model refusals shown to arrive
   unchanged, and 5 ERP-08 reasons — with coverage computed against each closed
   vocabulary. The ones it cannot deliver are declared *with the mechanism that
   makes them unreachable* (a load-time failure, a store invariant, a parameter that
   does not exist), so a new refusal cannot ship unproven and cannot hide.

## The order of the checks is the design

```
route  →  kind  →  caller  →  authorization  →  body  →  model
404/405   404       401        403 / 503        400      400 / 409 / 422
```

* **The kind is resolved before the caller.** A kind is part of the public contract,
  so "there is no such kind" answers a question about the surface, not about a
  tenant's data — and it keeps an unknown kind from reaching the auth layer, which
  would have refused it as an `unknown-kind` *permission* decision (403), a wrong
  answer to the right question.
* **Authorization precedes the body.** A caller who may not act never receives a
  verdict about the document they may not act on: that is the difference between
  authorization and a validation oracle.
* **Existence is reported only after permission.** A read *peeks* at the document
  (never *refuses*) so the decision can project its fields, and only an allowed
  caller is told the document is there. A document in another tenant is simply not
  in the store's address space — the keys are `(tenant, kind, id)`.
* **A refused body is the model's refusal**, re-raised unchanged: same code, same
  status. `negative_control` measures that pass-through, because "one contract, not
  two dialects" is a claim that has to be demonstrated.

## The seven routes

| Method | Path | Action asked of ERP-08 |
|---|---|---|
| `GET` | `/v1/erp/documents/{kind}` | `read` |
| `POST` | `/v1/erp/documents/{kind}` | `create` |
| `GET` | `/v1/erp/documents/{kind}/{documentId}` | `read` |
| `PUT` | `/v1/erp/documents/{kind}/{documentId}` | `write` |
| `DELETE` | `/v1/erp/documents/{kind}/{documentId}` | `delete` |
| `POST` | `/v1/erp/documents/{kind}/{documentId}/transitions/{action}` | the permission the move requires |
| `GET` | `/v1/erp/openapi.json`, `/v1/erp/health` | none — neither carries tenant data |

`{kind}` is an enum derived from `DocumentModel.document_kinds()`, so a family added
to ERP-02 appears in the API and in the document with no edit to this lane.

**A workflow move is not a permission, and the two vocabularies are not collapsed.**
ERP-02's workflows name *moves* (`submit`, `cancel`, `complete`); ERP-08's `ACTIONS`
name *permissions* (`submit`, `cancel`, `write`, …). `submit` and `cancel` are words
in both; `complete` — the fulfilment step this model's order lifecycle declares —
is not a permission at all, and asking the auth layer to grant it would be an
`unknown-action` refusal **for every principal alive**: a route nobody could use.
So `routes.ACTION_PERMISSION` places each move once, `routes.problems` refuses an
action ERP-02 declares that it does not place, and the mapping is in the document
(`x-erp-action-permissions`) so a client can see it.

**A transition's reply reports the move, not the document.** A move carries no
caller-supplied fields, so there is nothing for the field *write* policy to judge;
and handing the stored document to the decision as if it were a payload would let a
write-denied field *elsewhere* in the document refuse a legitimate move. The reply
is the document's identity, the state it reached and the action (`TransitionResponse`)
— a caller that wants the fields reads the document, through the read route, where
the projection is applied.

## Field policy: by omission on read, refused on write

A field whose policy denies `read` is **absent** from the response and named in
`redacted` — absent and empty are different claims, and only one of them is true. A
payload carrying a field whose policy denies `write` is **refused**, naming the
rule. The field policy is a function of `(kind, field, roles)` and not of the
document, so one decision covers a collection and each item is projected to the
visible set — the auth layer's own answer, not a second implementation of its rule.

## Identity (GR-6): there is no credential surface here

The surface carries no credential and declares **no security scheme**, because it
has none to declare: the adapter that mounts it supplies a
`Principal` — reference-based (a tenant id, a subject id and role names, with no
field a token could occupy). The document says so in `x-erp-identity`, marks the two
routes that carry no tenant data with an empty `security`, and `validate_document`
refuses a document that grows a security scheme. The scope **team** `identity/rbac`
requires for its node is a declared constant of the surface (`SCOPE_TEAM`), never a
value from a request: the node's org comes from the principal and its team from
here, so a client can influence neither.

## What this lane consumes, and what it owns

* **`integrations/erp/core/`** (ERP-02) — the schemas, workflows and the model that
  validates every write. Consumed, never restated: this lane owns no schema.
* **`integrations/erp/auth/`** (ERP-08) — the decision. Consumed through one call
  site; this lane owns no role, no permission and no policy *file*. (Its shipped
  `catalog/roles.json` governs the doctype surface *that* lane declares locally,
  which is a different kind vocabulary from ERP-02's — `party`/`item`/`gl-posting`
  are kinds here and not there — so the declarations this lane is exercised with are
  **derived from the model** in [`fixtures.py`](fixtures.py), through the auth
  lane's own loaders. When the indexer-fed catalogue lands for ERP-08, the source of
  those declarations changes and none of this code does.)
* **`identity/cpapi/router.py`** — the router's template compiler and the house
  envelope, **imported** rather than reimplemented. The `Route` dataclass is local
  because the control plane's pins `GET`/`POST` and this surface needs `PUT` and
  `DELETE`.
* **Not this lane's**: the portal mount (ERP-07, #652), the enterprise e2e (ERP-10,
  #655), the tenant transaction spine (ERP-03, #649).

## Running it

```bash
python3 -m pytest integrations/erp/api/tests -q        # the suite (177 tests)
bash scripts/check-erp-api.sh                          # the gate: suite + check + artifact + 2 mutants
python3 -m integrations.erp.api.cli check              # the document, the artifact, the declarations, the controls
python3 -m integrations.erp.api.cli emit --out integrations/erp/api/openapi.json
python3 -m integrations.erp.api.cli routes             # the route table and the declarations, as JSON
python3 -m integrations.erp.api.cli demo               # the golden path, twice, with a determinism verdict
python3 -m integrations.erp.api.negative_control       # provoke every refusal, by name
```

The CLI follows the repository's tri-state contract: `0` OK, `1` NOT-OK,
`2` CANNOT-ASSESS. A model or declaration that will not load is **never** a pass,
and it is not a failure either — the lane cannot report on a model it could not read.

## Layout

| Path | Role |
|---|---|
| [`openapi.py`](openapi.py) | the document: derivation, serialization, emission and the drift validator |
| [`openapi.json`](openapi.json) | the committed contract — generated, and compared to a fresh emission by the gate |
| [`routes.py`](routes.py) | the route table, the move→permission mapping, and the model-agreement check |
| [`surface.py`](surface.py) | the transport-free dispatcher and the one authorization call site |
| [`store.py`](store.py) | the tenant-keyed store; every write validated by ERP-02 |
| [`errors.py`](errors.py) | the boundary vocabulary, the auth mapping, the model's statuses read from its factories |
| [`health.py`](health.py) | three real dependency readings and the check that they are not lies |
| [`fixtures.py`](fixtures.py) | the offline corpus and the declarations derived from the model |
| [`provenance.py`](provenance.py) | the GR-10 harvest record and its enforcement |
| [`cli.py`](cli.py) | `emit` / `check` / `routes` / `demo` / `controls`, tri-state |
| [`negative_control.py`](negative_control.py) | one provocation per refusal, coverage computed in both directions |
| [`catalog/provenance.json`](catalog/provenance.json) | what this lane cannibalized, and what it built instead |
| [`tests/`](tests) | the suite: document, surface, delegation, errors, controls, provenance, health, CLI |

## Harvest provenance (GR-10)

Upstream `frappe/erpnext` is **GPL-3.0** and a **pattern source only**; its
declaration lives in [`../module.yaml`](../module.yaml) and
[`../core/provenance.json`](../core/provenance.json), and this lane *points at* it
rather than restating the licence — one declaration, no second copy to drift. What
this lane read from its own repository — the paperclip API's emitted-document
pattern, the control-plane SDK's client-contract pattern — and what it built instead
is in [`catalog/provenance.json`](catalog/provenance.json), and
[`provenance.py`](provenance.py) refuses a record that claims a copy, or that cannot
say what was built instead.
