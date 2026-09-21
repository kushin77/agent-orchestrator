"""AgentPack registry + catalog package (issue #40).

---knowledge---
module_id: registry.packs
system: registry
app: packs
solution_class: class
patterns: [package-contract, public-surface]
derives_from: null
owner_sme: platform-sme
tier: L0
interfaces: [the packs public API re-exports: PackRegistry, Installer, sign_pack, verify_pack, PackEventLog]
invariants: "the package re-exports the pack surface; the schemas stay files rather than becoming a second declaration"
gotchas: ""
related: ["#40"]
do_not_duplicate: null
---knowledge---

Public API re-exports. Importable when ``registry/`` is on ``sys.path``::

    import sys; sys.path.insert(0, "registry")
    import packs
"""

from packs import attestation
from packs.attestation import PackAttestationError, sign_pack, verify_pack
from packs.pack_events import (
    EVENT_KINDS,
    PackEventIntegrityError,
    PackEventLog,
    open_pack_event_log,
)
from packs.registry import (
    PackAlreadyExistsError,
    PackLifecycleError,
    PackNotFoundError,
    PackPublishError,
    PackRegistry,
    PackRegistryError,
)
from packs.installer import (
    ContentDriftError,
    Installer,
    PackInstallError,
    PackSignatureError,
    SyncPlanAction,
    UpgradeRollbackError,
)

__all__ = [
    "attestation",
    "PackAttestationError",
    "sign_pack",
    "verify_pack",
    "EVENT_KINDS",
    "PackEventIntegrityError",
    "PackEventLog",
    "open_pack_event_log",
    "PackAlreadyExistsError",
    "PackLifecycleError",
    "PackNotFoundError",
    "PackPublishError",
    "PackRegistry",
    "PackRegistryError",
    "ContentDriftError",
    "Installer",
    "PackInstallError",
    "PackSignatureError",
    "SyncPlanAction",
    "UpgradeRollbackError",
]
