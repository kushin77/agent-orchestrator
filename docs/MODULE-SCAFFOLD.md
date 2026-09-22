# Module scaffold — the de facto shape of a new internal module

Issue #1916 (child of EPIC #1908). Writes down a convention that was already
in use but not documented anywhere.

## Evidence

A spot-check of three recently-added internal modules shows the same
consistent, unwritten shape:

| Module | README | Entry module | `tests/` |
|---|---|---|---|
| `governance/lane-record/` | `README.md` | `lane_record.py` + `cli.py` | `tests/` (`conftest.py`, `test_lane_record.py`) |
| `governance/futureproof/` | `README.md` | `e2e.py` | `tests/` (`test_futureproof.py`) |
| `gateway/sme-routing/` | `README.md` | `router.py` + `cli.py` | `tests/` (6 test files + `conftest.py`) |

`find . -iname '*template*'` under a module path returns no match — there is
no internal scaffold template today. `control-plane/fleet-template/` and
`control-plane/sdk/template/` are narrow, purpose-built templates (a fleet
pilot template and an SDK consumer-repo template, respectively), not a
general "new internal module" starting point. This document, not a new
template directory, is the fix — copy the skeleton below and adapt it.

## The convention

A new internal module (under `governance/`, `gateway/`, `integrations/`, or
similar) is a directory containing:

1. **`README.md`** — what the module is, why it exists (issue/EPIC
   reference), and a layout table of its files. See the three modules above
   for the pattern.
2. **An entry module** — the module's main code, named for what it does
   (`cli.py`, `router.py`, `lane_record.py`, `e2e.py`). No fixed filename is
   required; name it for the module's job. A CLI entry point is commonly
   `cli.py`.
3. **`tests/`** — a subdirectory holding the module's tests
   (`test_*.py`, optionally `conftest.py`).

That's the whole convention. There is no required manifest, schema file, or
registration step for an internal module to exist and be worked on.

## When a manifest is required (and when it isn't)

A `module.json` (`cmr.module/v1`) manifest is **not** required for an
internal module under this repository. It only applies to the peer-repo CMR
admission contract documented in `docs/MODULE-ADMISSION.md`: a *separate
repository* that wants to be declared as this repo's sub-module publishes its
own `module.json` and is admitted into this repo's root `module.json`
`submodules` register. That contract governs cross-repository membership, not
internal directory scaffolding, and does not apply to modules under
`governance/`, `gateway/`, or similar living inside this repo.

The `module.json` files under `gateway/catalog/modules/*/` are a different,
unrelated use — per-runner catalog entries for the model gateway, not module
scaffold manifests.

## Copy-paste skeleton

```
governance/my-new-module/        # or gateway/, integrations/, etc.
├── README.md                    # what/why, issue reference, layout table
├── cli.py                       # entry point — name for the job it does
└── tests/
    └── test_my_new_module.py
```

Minimal `README.md` to start from:

```markdown
# governance/my-new-module — <one-line purpose>

<Why this exists: issue/EPIC reference, the problem it solves.>

## Layout

| Path         | What it is          |
|--------------|----------------------|
| `cli.py`     | <entry point>        |
| `tests/`     | Tests for this module |
```

This is not gate-checked; it documents the existing practice so new modules
follow it without having to reverse-engineer it from spot-checking other
directories.
