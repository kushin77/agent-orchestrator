# `control-plane/functions/` — the cockpit function registry

One declaration of every cockpit **function** — the panels, views and commands
the terminal cockpit renders and the operator types (issue **#565**, RC-10 of
EPIC **#551**; decided by `ADR-0026` D9).

| File | What it is |
|---|---|
| `functions.yaml` | **the registry** — every function, once: the operator-typed mnemonic, its title, the endpoint(s) it binds to, its parameters, the scope a caller needs, its effect class, the stream it subscribes to when live, and the roles it is designed for |
| `schema/functions.schema.json` | the shape, including the closed sets this registry owns (roles, kinds, parameter types) and the fields whose closed sets it **consumes** |
| `cli.py` | `validate` — schema checks **plus** the cross-reference against RC-2's vocabulary and the feature-flag surface registry, in both directions; `render` — the headless renderer |
| `tests/test_functions.py` | the unit tests: the closed sets, the refusals, the cross-references, and the proof that a role cannot permit |
| `tests/fixtures/cockpit.json` | the cockpit's own calls, as fixtures — every declared function renders against them with no live plane |

## Why it exists

ADR-0026 D9 fixed the requirement: **adding a domain — FinOps, SLO, audit,
tickets, paperclip — must be a declaration, not a fork of the client.** Before
this file, a new panel arrived as bespoke client code with its own permission
story, which is how the "one function set" of ADR-0026 D8 dissolves.

So the registry is the extensibility contract, and it is deliberately **ordered
before the client**: RC-10 (#565) decides the closed function set, and RC-11
(#566) builds the cockpit against it. The cockpit renders what is declared here
and types what is declared here; it carries no catalogue of its own.

## A function, and its fields

| field | meaning |
|---|---|
| `id` | the operator-typed **mnemonic** — short, uppercase, unambiguous, unique (`FLT`, `FKL`, `BCL`) |
| `title` | the human name the cockpit renders |
| `kind` | `panel` (a read projection) · `view` (a live subscription) · `command` (an RC-2 control verb) |
| `endpoints` | the API route(s) it binds to: RC-3's route for an RC-2 verb, or a route the surface's own declaration names |
| `parameters` | this function's own closed parameter set: name, type, `required`, and a default when it is optional |
| `scope` | the `identity/rbac` capability a **caller** needs, and the tenant scope it is evaluated at |
| `effect_class` | RC-2's blast-radius vocabulary — `read` \| `hold` \| `stop` \| `irreversible` |
| `audit` | the action recorded on the existing rails: `null` for a read, required for every other class |
| `stream` | the surface whose push channel a `view` subscribes to; `null` otherwise |
| `roles` | the roles this function is **designed for** — a recommendation, never a permission |

## The closed sets are consumed, not restated

That is the load-bearing rule, and the gate enforces it rather than asking:

- **Effect classes, capabilities and audit actions** come from RC-2's vocabulary
  (`control-plane/control/verbs.yaml`). A command whose `effect_class`,
  `scope.capability` or `audit` differs from what RC-2 declares for its verb is
  refused **by name** — so this registry cannot mint a capability or invent a
  class, and a verb RC-2 withdraws cannot linger here.
- **Routes** come from their owners. A `command` must bind exactly RC-3's
  `POST /api/control/<family>/<action>`; a `panel` or `view` must bind a route
  that the surface's *own declaration* names in
  `infra/feature-flags/registry.yaml`. A route nobody declares is refused by name.
- **Permissions** are cited, never minted. A panel's capability must appear in
  the file that requires it (`identity/rbac/model.py`, `portal/server/app.py`,
  `portal/server/fleet_authz.py`); otherwise it is refused. Where a route is
  served to any authenticated principal and checks no capability, the function
  declares `capability: null` **plus** `why_no_capability` — the same discipline
  RC-2 applies to a withheld verb: a function is never silently capability-less.

## Role suitability is a lens, and it can only narrow

**Permission stays with `identity/rbac` (ADR-0026 D8/D9).** A function may be
*recommended* for a role; it is never *permitted* by one, and this registry holds
no permit, deny or role table — there is no field for one, and the schema's
`additionalProperties: false` is what stops one being added silently.

The proof is a property of the renderer, not a promise:
`cli.render(document, fixtures, role=...)` filters rows and rewrites nothing, so
for every role the rendered set is a subset of the declared set and every row's
`scope` and `audit` are byte-identical to the declared ones
(`test_role_suitability_can_only_narrow_and_never_rewrite_a_scope`). Because the
capability travels through the lens unchanged, **there is no code path by which
a role lens changes what a caller must hold** — and if someone tried to use this
registry as an authorisation source, the attempt has nothing to call: the
decision is `identity/rbac`'s `guard`, evaluated per request by RC-3
(`portal/server/control_api.py` `_require_capability`), which reads the verb's
own capability and refuses a caller that lacks it whatever the cockpit rendered.

## Nothing is silently dropped

The registry covers the whole plane, in both directions:

- every **exposed** RC-2 verb is declared by exactly one `command` function;
- every surface the feature-flag registry declares is declared by a `panel` or
  `view` — or named in `undeclared` with a reason;
- a local verb or a surface that no gate could see would arrive as a bespoke
  panel, so the gate fails **naming** it and a later lane must decide.

Four surfaces are excused by name today: `remote_control` (the transport the
commands already travel over), `chat` (ADR-0023's own surface — RC-2 declares no
chat verb, so there is no control effect class to reuse and inventing one would
be the second vocabulary this registry exists to avoid), `fleet_health_export`
and `telemetry_exposition` (push exporters whose own declarations ship no HTTP
route for a client to read).

## Running it

```bash
bash scripts/check-control-functions.sh          # the gate: registry + headless render + suite + 9 refused-by-name controls
python3 control-plane/functions/cli.py validate
python3 control-plane/functions/cli.py render --fixtures control-plane/functions/tests/fixtures/cockpit.json --role Analyst
python3 -m pytest control-plane/functions -q
```

## Not wired yet — deliberately, and disclosed

This lane does not touch `Makefile`, `scripts/verify.sh` or
`scripts/pytest-suites.txt`: **#559 is this EPIC's single writer** for the shared
build files. Until #559 wires it, the gate runs only when invoked directly, so
this lane records a `script … uninvoked` entry in
`scripts/gate-coverage-baseline.txt` (tracker #559) rather than leaving the gate
uncovered and unnamed — the gate-coverage rule is that an unwired artifact must
be *explicit*, never grandfathered. #559 must remove that entry when it adds
`check-control-functions` to `verify.sh`'s `checks=()` array and
`control-plane/functions` to the pytest manifest; the baseline is checked in both
directions, so the stale entry fails by name until it is removed.
