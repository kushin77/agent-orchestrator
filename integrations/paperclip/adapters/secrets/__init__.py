"""Per-agent secret vault primitive — GSM-backed reference/rotation view (#417).

The primitive **names** a secret (its GSM path, the agent and scope it belongs
to) and **never carries the value**. Secret Manager remains the store of record;
this adapter is a reference/rotation view over it. See :mod:`model` for the
shapes and :mod:`vault` for the projection, the refusals and the read guard.

---knowledge---
module_id: integrations.paperclip.adapters.secrets.__init__
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

from . import model, vault

__all__ = ["model", "vault"]
