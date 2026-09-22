"""identity/onboarding.config — declared env-var reads for the onboarding CLI.

Single source for ``$AO_ONBOARDING_STORE`` (issue #1915: route plain-config
env reads through a declared flags/config module instead of an inline
``os.environ.get`` in the CLI body).

---knowledge---
module_id: identity.onboarding.config
system: identity
app: onboarding
solution_class: template
patterns: [read-only-optional-input]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [env_store_path]
invariants: "returns None when AO_ONBOARDING_STORE is unset; never invents a default path"
gotchas: ""
related: ["#1915"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import os

ENV_STORE_PATH = "AO_ONBOARDING_STORE"


def env_store_path() -> str | None:
    """Return the ``--store`` override from ``$AO_ONBOARDING_STORE``, if set."""
    return os.environ.get(ENV_STORE_PATH)
