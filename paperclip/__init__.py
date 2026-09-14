"""Paperclip-ing parity adapters (EPIC #410).

One subpackage per upstream capability family. Every adapter obeys the EPIC's
one rule: **projection, not authority** — a fleet ledger stays the writer and
the adapter derives and maps onto the frozen ticket contract (#400,
ADR-0014). No adapter introduces a second store.

Families: ``approvals`` (this tree), heartbeat, budget, secrets, routines,
skills — each owned by its own lane.
"""
