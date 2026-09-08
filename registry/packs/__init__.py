"""AgentPack registry + catalog package (issue #40).

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
