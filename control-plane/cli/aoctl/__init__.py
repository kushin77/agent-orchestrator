"""aoctl — the remote command center CLI (issue #556, RC-5 of EPIC #551).

WHY this package exists. The fleet can be commanded only from its own keyboard:
``docs/REMOTE-CONTROL-GAP-ANALYSIS.md`` §2.6 measures eighteen verbs in
``fleet/control.py``, thirteen in ``fleet/channel.py`` and twenty-two across the
governance CLIs, and **zero** reachable from off the host. RC-3 (issue #554)
shipped the server half — ``POST /api/control/<family>/<action>`` on the
**existing** console app, flag-gated OFF, authenticated by the operator's console
session. This package is the client half: a thin operator CLI that speaks
**only** that API.

Five properties, each of which the module that owns it states in full:

``vocabulary``
    The CLI's verbs are a table of **declared command ids** consumed from RC-2's
    registry (``control-plane/control/verbs.yaml``), never a hand-copied verb
    list. A verb the registry does not declare, or declares ``exposed: false``,
    is refused **locally** — the CLI does not ask the plane to say no.
``refusals``
    Every failure mode is a **named reason**, and the exit code is this repo's
    tri-state: ``0`` the command was answered with a receipt · ``1`` a named
    refusal · ``2`` CANNOT-ASSESS — no verdict could be obtained (the plane is
    unreachable, or the plane itself could not assess the command). A command
    that cannot be delivered is **never** reported as delivered.
``plane``
    One transport, the boundary's own: any call **imports**
    ``integrations/paperclip/client.py`` (ADR-0016, consumed by ADR-0025 D5)
    rather than re-implementing one, and the offline ``FixtureTransport`` is
    what keeps the gate off the network.
``receipt``
    One receipt per action. On a replay the plane hands the **original** receipt
    back after the marker ``portal/server/control_audit.py`` declares, and the
    CLI parses that marker rather than restating it.
``cli``
    ``--dry-run`` prints the request it would send and sends nothing.

The CLI is not a dashboard (ADR-0022's refusal, consumed by ADR-0025 §6.2):
text and structured JSON only — no frame, no filter bar, no timeline.
"""

from __future__ import annotations

__all__ = ["__version__"]

#: The CLI's own version. The *contracts* it speaks have their own: RC-2's
#: registry schema and RC-3's route family.
__version__ = "0.1.0"
