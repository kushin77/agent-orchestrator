"""Live sync — the module-brief surface's `live_sync` evidence (issue #888).

See :mod:`.live` for the module that actually pulls tickets, heartbeats and
budgets through the paperclip seam, validates the result, and appends the
audit trail.

---knowledge---
module_id: integrations.paperclip.reporting.sync.__init__
system: integrations
app: paperclip
solution_class: pattern
patterns: []
derives_from: null
owner_sme: paperclip
tier: L1
interfaces: []
invariants: ""
gotchas: ""
related: ["#1910"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

from .live import SyncRecord, SyncRefusal, run_sync

__all__ = ["SyncRecord", "SyncRefusal", "run_sync"]
