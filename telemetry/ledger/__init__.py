"""Public API of telemetry/ledger (issue #31).

---knowledge---
module_id: telemetry.ledger.__init__
system: telemetry
app: ledger
solution_class: pattern
patterns: [public-api-surface, consumed-never-redefined]
derives_from: null
owner_sme: security-sme
tier: L0
interfaces: [open_ledger, DictKeystore, KeyMaterial, verify_ledger]
invariants: "the public surface re-exports the package contract; the ledger-style contract itself lives in the directory README"
gotchas: ""
related: ["#31", "#1510"]
do_not_duplicate: null
---knowledge---


The per-tenant tamper-evident audit ledger: append-only hash-chained records
with AES-256-GCM encrypted payloads at rest, per-tenant isolation, a tri-state
verify API and a CLI. See ``README.md`` in this directory for the contract.

Typical use (file-backed, keys injected via a keystore):

    from ledger import open_ledger, DictKeystore, KeyMaterial, verify_ledger

    keystore = DictKeystore({"acme": KeyMaterial(key=b"0" * 32, key_id="acme:k1")})
    store = open_ledger("/var/lib/audit", keystore=keystore)

    store.append("acme", actor="agent:worker-1", action="model.call",
                 resource="gateway/proxy", payload={"prompt": "..."})
    verdict = verify_ledger(store, "acme")   # LedgerVerdict(OK)
"""

from .errors import (
    CipherUnavailableError,
    DecryptionError,
    KeyUnavailableError,
    LedgerCorruptError,
    LedgerCryptoError,
    LedgerError,
    LedgerIntegrityError,
    LedgerValidationError,
    RepairRefusedError,
)
from .keystore import (
    DictKeystore,
    EnvKeystore,
    FileKeystore,
    Keystore,
    KeyMaterial,
)
from .schema import GENESIS_HASH, now_utc
from .store import LedgerStore, LedgerVerdict, open_ledger
from .verify import (
    all_ok,
    read_payload,
    verdict_exit_code,
    verify_all,
    verify_ledger,
)

__all__ = [
    # store
    "LedgerStore",
    "LedgerVerdict",
    "open_ledger",
    # keystore
    "Keystore",
    "DictKeystore",
    "EnvKeystore",
    "FileKeystore",
    "KeyMaterial",
    # verify
    "verify_ledger",
    "verify_all",
    "read_payload",
    "verdict_exit_code",
    "all_ok",
    # schema helpers
    "GENESIS_HASH",
    "now_utc",
    # errors
    "LedgerError",
    "LedgerValidationError",
    "LedgerCorruptError",
    "LedgerIntegrityError",
    "KeyUnavailableError",
    "LedgerCryptoError",
    "CipherUnavailableError",
    "DecryptionError",
    "RepairRefusedError",
]
