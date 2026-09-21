"""registry/sync — live hermes head-agent registration sync (issue #889, lane L10).

---knowledge---
module_id: registry.sync
system: registry
app: sync
solution_class: class
patterns: [package-contract, public-surface]
derives_from: null
owner_sme: sync-sme
tier: L0
interfaces: [register_head_agent, HeadRegistrationError]
invariants: "the package re-exports the head-agent registration entry point and nothing else"
gotchas: ""
related: ["#889"]
do_not_duplicate: null
---knowledge---

Reads the real seed profile (``registry/profiles/seeds/hermes.1.0.0.yaml``)
and persona card (``registry/personas/cards/hermes.yaml``) off disk, validates
each against its schema + the live catalog (``registry/profiles/validate.py``,
``registry/personas/registry.py``), and — only on success — appends a
``register`` event to the append-only event log
(``registry/events/event_log.py``). A persona whose head-of-org fields
(``id``/``name``) are missing is refused BY NAME before anything is appended
(no-false-green).
"""

from __future__ import annotations

from sync.live import HeadRegistrationError, register_head_agent

__all__ = ["register_head_agent", "HeadRegistrationError"]
