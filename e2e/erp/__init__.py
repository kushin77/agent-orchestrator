"""e2e/erp — the ERP module's end-to-end proof (ERP-10, issue #655).

The capstone of the ERP epic (EPIC #645): the sibling lanes each proved their own
half — ERP-01 the declaration, ERP-02 the document model, ERP-03 the transactional
spine, ERP-04 procurement/manufacturing, ERP-05 the CRM families, ERP-06 the served
API, ERP-07 the console frame, ERP-08 the tenant scoping, ERP-09 the metering. What
none of them could prove is the thing the epic is *for*: that a tenant can switch the
module on and run a **selling cycle through all of them at once**, offline and with no
keys.

Layout:

* ``gate.py``            the offline console session (a local key, the JWKS the console
                         verifies against, the cookie it accepts) — no network, no real
                         identity provider.
* ``golden_path.py``     the stage-by-stage tenant journey: provision (ERP-01/07) ->
                         the sales cycle (ERP-03 over ERP-02, indexer-fed) -> the ERP-08
                         decisions on the cycle's own documents -> the ERP-09 metering of
                         the cycle's own operations.
* ``negative_controls.py`` one control per acceptance refusal — the flag OFF, a
                         cross-tenant read, a budget-exhausted tenant — each driven
                         through the module that owns the refusal, plus one control that
                         composes all six sibling lanes' own negative-control drivers.
* ``cli.py``             the lane's own tri-state check (0 OK / 1 NOT-OK / 2
                         CANNOT-ASSESS), run by ``scripts/check-erp-e2e.sh``.
* ``tests/``             the suite registered in ``scripts/pytest-suites.txt``.

Offline by construction: no sockets, no network, no credentials. Every hop is read
through the ERP module's own public surfaces, and no pillar file is edited.
"""

from __future__ import annotations

from e2e._paths import ensure_sys_paths  # noqa: F401

ensure_sys_paths()
