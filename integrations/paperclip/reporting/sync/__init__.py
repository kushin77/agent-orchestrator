"""Live sync — the module-brief surface's `live_sync` evidence (issue #888).

See :mod:`.live` for the module that actually pulls tickets, heartbeats and
budgets through the paperclip seam, validates the result, and appends the
audit trail.
"""

from __future__ import annotations

from .live import SyncRecord, SyncRefusal, run_sync

__all__ = ["SyncRecord", "SyncRefusal", "run_sync"]
