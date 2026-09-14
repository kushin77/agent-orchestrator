"""Paperclip-ing parity adapters (EPIC #410) over the frozen integration seams.

One subpackage per upstream capability family. Every adapter obeys the EPIC's
one rule: **projection, not authority** — a fleet ledger stays the writer and
the adapter derives and maps onto the frozen ticket contract (#400,
ADR-0014). No adapter introduces a second store.

The seam contracts are frozen under ``docs/contracts/paperclip/`` (ADR-0013);
each adapter only *derives* the upstream shape — the fleet stays the writer of
its own runtime (ADR-0012).

Families: ``approvals``, ``heartbeat``, ``budget``, ``secrets``, ``routines``,
``skills`` — each owned by its own lane.
"""
