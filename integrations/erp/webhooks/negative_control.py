"""Negative controls for every refusal this lane can raise (issue #671).

Provokes each code in :data:`model.REFUSALS` against a fresh
:class:`bridge.ConversionBridge` and requires it to be refused by name (via a
``quarantined`` :class:`~model.BridgeResult` whose ``reason`` starts with the
code, or a directly-raised :class:`~model.Refused`). Mirrors the pattern
``integrations/erp/crm/negative_control.py`` documents: a validator that is
never shown to refuse is a formality, and coverage is computed against the
closed vocabulary rather than claimed.

Run directly with ``python3 integrations/erp/webhooks/negative_control.py``;
exits 0 only when every code in ``model.REFUSALS`` is provoked.

---knowledge---
module_id: integrations.erp.webhooks.negative_control
system: integrations
app: erp
solution_class: enterprise
patterns: []
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [provoke, run]
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import List, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from integrations.erp.tx.ledger import PostingPolicy  # noqa: E402
from integrations.erp.webhooks import auth as auth_module  # noqa: E402
from integrations.erp.webhooks.bridge import ConversionBridge  # noqa: E402
from integrations.erp.webhooks.flags import FLAG_ID  # noqa: E402
from integrations.erp.webhooks.model import REFUSALS  # noqa: E402

SECRET = "negative-control-secret"


def _payload(**overrides):
    payload = {
        "schemaVersion": 1,
        "eventId": "nc-evt",
        "hook": "erp.crm.opportunity-win",
        "tenant": "nc-tenant",
        "occurredAt": "2026-09-16T00:00:00Z",
        "customerId": "nc-cust",
        "customerName": "NC Co",
        "amount": 100.0,
        "currency": "USD",
        "sourceDocument": "opportunity/nc-1",
    }
    payload.update(overrides)
    return payload


def _sign(payload: dict, *, secret: str = SECRET) -> Tuple[bytes, str]:
    raw = json.dumps(payload).encode("utf-8")
    return raw, auth_module.sign(secret, raw)


def _full_policy() -> PostingPolicy:
    return PostingPolicy(accounts={"receivable": "AR", "income": "Sales"})


def provoke(code: str) -> str:
    """Provoke ``code`` and return the reason string it was refused under."""
    if code == "invalid-body":
        bridge = ConversionBridge(secret=SECRET, posting_policy=_full_policy(), flags={FLAG_ID: True})
        raw = json.dumps([1, 2, 3]).encode("utf-8")
        header = auth_module.sign(SECRET, raw)
        return bridge.handle(raw, header, [1, 2, 3]).reason

    if code == "schema-violation":
        bridge = ConversionBridge(secret=SECRET, posting_policy=_full_policy(), flags={FLAG_ID: True})
        payload = _payload()
        del payload["amount"]
        raw, header = _sign(payload)
        return bridge.handle(raw, header, payload).reason

    if code == "unknown-hook":
        bridge = ConversionBridge(secret=SECRET, posting_policy=_full_policy(), flags={FLAG_ID: True})
        payload = _payload(hook="salesforce.opportunity.won")
        raw, header = _sign(payload)
        return bridge.handle(raw, header, payload).reason

    if code == "auth-failed":
        bridge = ConversionBridge(secret=SECRET, posting_policy=_full_policy(), flags={FLAG_ID: True})
        payload = _payload()
        raw, _header = _sign(payload)
        return bridge.handle(raw, "sha256=" + "0" * 64, payload).reason

    if code == "unbalanced-posting":
        # GeneralLedger is a frozen dataclass, so this is provoked by handing
        # the bridge a stand-in ledger whose apply() returns an unbalanced
        # candidate, rather than mutating the real one in place.
        from integrations.erp.tx.ledger import Finding

        class _PoisonedLedger:
            entries: tuple = ()

            def apply(self, entries):
                return self

            def verify(self):
                return [Finding("unbalanced-posting", "provoked by negative control", ref="crm-conversion:nc-unbalanced")]

        bridge = ConversionBridge(secret=SECRET, posting_policy=_full_policy(), flags={FLAG_ID: True})
        bridge.ledger = _PoisonedLedger()  # type: ignore[assignment]
        payload = _payload(eventId="nc-unbalanced")
        raw, header = _sign(payload)
        return bridge.handle(raw, header, payload).reason

    if code == "missing-policy":
        incomplete = PostingPolicy(accounts={"receivable": "AR"})
        bridge = ConversionBridge(secret=SECRET, posting_policy=incomplete, flags={FLAG_ID: True})
        payload = _payload()
        raw, header = _sign(payload)
        return bridge.handle(raw, header, payload).reason

    if code == "quarantined":
        bridge = ConversionBridge(secret=SECRET, posting_policy=_full_policy(), flags={FLAG_ID: False})
        payload = _payload()
        raw, header = _sign(payload)
        return bridge.handle(raw, header, payload).reason

    raise AssertionError(f"no provocation wired for {code!r}")


def run(stream=None) -> int:
    stream = stream if stream is not None else sys.stdout
    provoked: List[str] = []
    failures: List[str] = []
    for code in REFUSALS:
        try:
            reason = provoke(code)
        except Exception as exc:  # pragma: no cover - reported below
            failures.append(f"{code}: provocation raised {type(exc).__name__}: {exc}")
            continue
        if not reason or not reason.startswith(code):
            failures.append(f"{code}: refused with reason {reason!r} (expected it to start with {code!r})")
            continue
        provoked.append(code)
        print(f"OK  {code}: {reason}", file=stream)

    missing = sorted(set(REFUSALS) - set(provoked))
    if missing:
        failures.append(f"never provoked: {', '.join(missing)}")

    for failure in failures:
        print(f"FAIL {failure}", file=stream)

    print(f"{len(provoked)}/{len(REFUSALS)} refusals provoked", file=stream)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(run())
