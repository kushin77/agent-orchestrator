# `infra/env` — the declared environment-variable surface (issue #944)

This directory is the **one place** where an environment variable this repository
needs is declared. Before it existed there was no declared environment surface at
all — no `.env.example`, no `ENVIRONMENT.md` — so variables lived in prose and in
code, a new one could be introduced with no record, and no single place let a
reader enumerate what a deployment requires.

| Artifact | What it is |
|---|---|
| [`registry.yaml`](registry.yaml) | the declaration: every variable, its purpose, whether it is a secret, what supplies it, and who reads it |
| [`surface.py`](surface.py) | the reader and the parity checker (`check`, `describe`, `scan-file`, `scan-secret`) |
| [`../../scripts/check-env-surface.sh`](../../scripts/check-env-surface.sh) | the gate: every refusal provoked by name, run by `make verify` |

The shape mirrors the flag registry, which is the repo's existing answer to the
same class of problem: `infra/feature-flags/registry.yaml` is held to
`infra/terraform/variables.tf` by `scripts/check-feature-flags.py`. The difference
is the direction of the drift — a flag registry drifts against Terraform, an
environment registry drifts against the code that reads it.

## What the gate refuses, by name

| Finding | The defect it names |
|---|---|
| `declaration-schema` | a blank purpose, a duplicate name, a `secret` that is not a boolean, a supplier outside the closed vocabulary, or no recorded reader |
| `undeclared-env-read` | code reads a variable the declaration neither declares nor exempts — **the defect this surface exists for** |
| `declared-env-unread` | the declaration names a variable nothing reads: a stale entry |
| `exemption-without-reason` | an exemption with no reason, which is indistinguishable from an omission |
| `reader-path-missing` | a recorded reader that is not in the tree, or that never names the variable |
| `indirect-read-not-recorded` | a read the scanner cannot follow (`os.environ.get(name)`), recorded with its **site count** so a new one raises it and a removed one leaves a stale record |
| `secret-literal-in-tree` | a `secret: true` variable carried as a literal value (GR-6) |

## The scanner is deliberately not a grep

The console reads its environment through module-level constants:

```python
JWKS_ENV = "PORTAL_AUTH_GATE_JWKS"
inline = (os.environ.get(JWKS_ENV) or "").strip()
```

A textual `os.environ["..."]` scan therefore finds **nothing** in `portal/` while
the console reads four variables — a false green of exactly the kind this issue is
about. `surface.py` resolves module-level string constants with an AST
(`infra/env/surface.py`, `_module_constants`), and the gate proves the difference
by planting a file that uses the idiom and requiring it to be **accepted**. A read
whose name genuinely cannot be resolved statically is reported as
`indirect-read-not-recorded` rather than silently dropped, so the unmeasurable part
of the surface stays visible and bounded.

## Adding a variable

1. Add it to `registry.yaml` with a purpose, `secret`, `required_by` and its
   readers. `required_by` is closed: `deploy`, `local-run`, `ops`, `tooling`.
2. If it carries secret material, `secret: true` — and never a literal value in
   the tree, in any file (GR-6). A reference or a placeholder is not a leak.
3. If it is read but deliberately not supplied (inherited process environment, a
   harness seam), add it to `exempt` **with the reason**. An exemption without a
   reason is refused.
4. If the read is dynamic (`os.environ.get(name)`), record the file and its site
   count in `indirect_reads` with the reason.
5. Run `bash scripts/check-env-surface.sh`.

## Why `infra/` is a declared surface root

`governance/conformance/surfaces.yaml` declares its surface roots, and a root that
exists must be declared with a class **or waived by name with a reason** — "no
surface root goes unclassified by omission". `infra/` was neither: not being
listed, it was never considered by the class gate at all. It is now listed and
waived with its reason recorded, because `infra/` is the IaC and declaration layer
rather than a product surface, and the artifacts under it are held by their own
gates — this one included.
