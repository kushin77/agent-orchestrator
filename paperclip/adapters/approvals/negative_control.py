#!/usr/bin/env python3
"""Negative controls for the approvals adapter (issue #416, GR-12).

A check that cannot fail is a formality. This driver builds the fixture tree,
projects it, then **provokes** each refusal the adapter claims and requires the
offender to be named. If any provocation is *not* refused — or is refused with
the wrong code — the driver exits non-zero, so the gate goes red.

Provocations:

1. ``no-authority``             — a kind with no authoritative surface;
2. ``projection-without-record`` — a grant that the authority does not record,
   and a grant whose cited record has been removed;
3. ``double-approval``          — one item granted twice;
4. ``unauthorised-decider``     — a decision by an actor the fleet never
   empowered for that kind;
5. ``pending != granted``       — a request with no decision stays ``pending``
   and can never be promoted by editing the projection.

Run as ``python3 paperclip/adapters/approvals/negative_control.py``.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Tuple

if __package__ in (None, ""):  # executed as a script, not imported as a package
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from paperclip.adapters.approvals import fixtures, mapping, verify  # noqa: E402
from paperclip.adapters.approvals.model import (  # noqa: E402
    STATE_GRANTED,
    STATE_PENDING,
    Approval,
    ApprovalRefused,
    Finding,
    Projection,
    authority_for,
)


def _codes(findings: List[Finding]) -> Dict[str, List[Finding]]:
    grouped: Dict[str, List[Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.code, []).append(finding)
    return grouped


def _expect(grouped: Dict[str, List[Finding]], code: str, needle: str) -> Tuple[bool, str]:
    hits = grouped.get(code, [])
    if not hits:
        return False, f"expected a {code!r} refusal, got {sorted(grouped) or 'none'}"
    joined = " | ".join(f.detail for f in hits)
    if needle and needle not in joined:
        return False, f"a {code!r} refusal was raised but does not name {needle!r}: {joined}"
    return True, f"{code} refused by name: {hits[0].detail}"


def _project(root: Path) -> Projection:
    return mapping.project(root)


def _new_tree(base: Path, name: str) -> Path:
    root = base / name
    fixtures.build_tree(root)
    return root


def run() -> int:
    base = Path(tempfile.mkdtemp(prefix="ao416-neg-", dir=tempfile.gettempdir()))
    results: List[Tuple[str, bool, str]] = []

    # --- positive control: the clean tree is granted/denied/pending as built ---
    clean = _new_tree(base, "clean")
    projection = _project(clean)
    findings = verify.verify(clean, projection)
    states = {(a.kind, a.subject): a.state for a in projection.approvals}
    ok_positive = (
        not findings
        and states.get(("hire", "issue:416")) == STATE_GRANTED
        and states.get(("top-up", "budget:agent/paperclip")) == STATE_GRANTED
        and states.get(("override", "issue:999")) == STATE_GRANTED
        and states.get(("top-up", "budget:agent/secrets")) == STATE_PENDING
    )
    results.append(
        (
            "positive: clean tree projects and verifies",
            ok_positive,
            f"{len(projection.approvals)} approval(s), states={sorted(states.items())}, findings={len(findings)}",
        )
    )

    # --- provocation 1: a kind with no authority behind it -------------------
    root = _new_tree(base, "no-authority")
    fixtures.write_record(
        root,
        ".fleet/brain/inbox/req-transfer.json",
        {
            "from": "operator",
            "to": "brain",
            "type": "directive",
            "id": "order-transfer-1",
            "task": {"issue": 1},
            "approval": {"kind": "transfer", "subject": "issue:1", "requested_by": "operator"},
        },
    )
    grouped = _codes(_project(root).findings)
    ok, detail = _expect(grouped, "no-authority", "transfer")
    if ok:
        try:
            authority_for("transfer")
            ok, detail = False, "authority_for('transfer') did not refuse"
        except ApprovalRefused as exc:
            ok, detail = True, f"no-authority refused by name: {exc}"
    results.append((f"1. a kind with no authority ({'transfer'})", ok, detail))

    # --- provocation 2: a projection with no authoritative record ------------
    # 2a: an approval claiming `granted` with no decision_ref at all.
    clean_projection = _project(clean)
    granted = next(a for a in clean_projection.approvals if a.state == STATE_GRANTED)
    hollow = granted.to_dict()
    hollow["decision_ref"] = ""
    hollow_approval = Approval(**hollow)
    index = mapping.decision_index(clean)
    grouped = _codes(verify.verify_approval(hollow_approval, index))
    ok, detail = _expect(grouped, "projection-without-record", granted.id)
    results.append(("2a. a grant with no decision record", ok, detail))

    # 2b: the authority record is removed while a projection still cites it —
    # the projection was built when the record existed, so the only thing that
    # changed is the authority. The projection must fail, not stand.
    root = _new_tree(base, "record-absent")
    stale_projection = _project(root)
    cited = next(a for a in stale_projection.approvals if a.state == STATE_GRANTED)
    fixtures.remove_record(root, cited.decision_ref)
    grouped = _codes(verify.verify(root, stale_projection))
    ok, detail = _expect(grouped, "record-absent", Path(cited.decision_ref).name)
    results.append(("2b. a grant whose authoritative record was removed", ok, detail))

    # --- provocation 3: one item granted twice ------------------------------
    root = _new_tree(base, "double-approval")
    fixtures.write_record(
        root,
        ".board/claims/claim-416-again.json",
        {"event": "claim", "issue": 416, "agent": "ao-session-other", "at": "2026-09-14T00:05:00Z", "lane": "approvals"},
    )
    grouped = _codes(verify.verify(root))
    ok, detail = _expect(grouped, "double-approval", "granted 2 times")
    results.append(("3. an item approved twice", ok, detail))

    # --- provocation 4: a decider with no authority for the kind -------------
    root = _new_tree(base, "unauthorised-decider")
    fixtures.write_record(
        root,
        ".fleet/sent/directive-topup-badactor.json",
        {
            "from": "brain",
            "to": "sister",
            "type": "directive",
            "id": "brain-directive-topup-bad",
            "task": {"issue": 418},
            "body": "a top-up decided by an actor the fleet never empowered",
            "approval": {"kind": "top-up", "subject": "budget:agent/routines", "decision": "grant", "actor": "sister"},
        },
    )
    grouped = _codes(verify.verify(root))
    ok, detail = _expect(grouped, "unauthorised-decider", "sister")
    results.append(("4. a decision by an unauthorised actor (sister -> top-up)", ok, detail))

    # --- provocation 5: pending is not granted, and is never defaulted --------
    pending_ok = states.get(("top-up", "budget:agent/secrets")) == STATE_PENDING
    pending_approval = next(a for a in _project(clean).approvals if a.state == STATE_PENDING)
    promoted = Approval(**{**pending_approval.to_dict(), "state": STATE_GRANTED})
    grouped = _codes(verify.verify_approval(promoted, mapping.decision_index(clean)))
    ok5, detail5 = _expect(grouped, "projection-without-record", pending_approval.id)
    # Autonomy is granted, never default: with no request and no decision there
    # is no approval at all (nothing to default to `granted`).
    empty = base / "empty"
    empty.mkdir()
    no_default = _project(empty).approvals == ()
    ok = pending_ok and ok5 and no_default
    results.append(
        (
            "5. a pending approval stays pending (never default-granted)",
            ok,
            f"request-with-no-decision stays {STATE_PENDING}; promotion refused ({detail5}); "
            f"empty tree projects 0 approvals = {no_default}",
        )
    )

    # --- transcript ----------------------------------------------------------
    failed = 0
    print("== approvals negative controls ==")
    for name, ok, detail in results:
        print(f"  {'PROVOKED' if ok else 'NOT-PROVOKED'}  {name}")
        print(f"      {detail}")
        if not ok:
            failed += 1
    print(f"negative controls: {len(results) - failed} of {len(results)} provoked")
    print(f"scratch: {base}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run())
