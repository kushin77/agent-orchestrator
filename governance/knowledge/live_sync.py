"""Live CMR pin sync for the knowledge indexer (issue #887, lane L8/#878).

The knowledge indexer (:mod:`indexer`) catalogues *static* sources — files
already in the tree. This module is the one place that consumes the *live*
state of the CMR standards pin so the indexer (and its gate) can answer:
"which CMR standards bundle is this repo's knowledge actually built from,
right now, and has the pin drifted from the hub it claims to follow?"

It does not duplicate the pin schema or the drift algorithm — both already
live in ``vendor/CMR/sync``:

* ``vendor/CMR/sync/cmr-pin.schema.json`` (schema) and
  ``vendor/CMR/sync/validate-cmr-pin.py`` (shape validator) are imported
  directly (by path) and driven, never re-implemented;
* the drift comparison itself (``cmr-pin.yaml`` ``bundle_ref`` vs.
  ``git -C vendor/CMR rev-parse HEAD``) is the same comparison
  ``scripts/check-cmr-pin.sh`` performs at the repo-root gate. It is small
  enough (one string compare) that "importing" it would mean importing a
  shell script; this module re-derives it directly against live git state
  and refuses it BY NAME (``CMR_PIN_DRIFT``) exactly as that gate does, so
  the two never disagree on what "drift" means.

Nothing here is a stub: :func:`pinned_bundle` and :func:`check_drift` read
real files and real git state, and raise/refuse on bad input (see
``governance/knowledge/tests/test_live_sync.py`` for the negative control).
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
PIN_PATH = REPO_ROOT / "cmr-pin.yaml"
VENDOR_CMR = REPO_ROOT / "vendor" / "CMR"
VALIDATOR_PATH = VENDOR_CMR / "sync" / "validate-cmr-pin.py"
SCHEMA_PATH = VENDOR_CMR / "sync" / "cmr-pin.schema.json"
MANIFEST_PATH = VENDOR_CMR / "controller" / "standards-manifest.txt"

CODE_PIN_DRIFT = "CMR_PIN_DRIFT"
CODE_PIN_MISSING = "CMR_PIN_MISSING"
CODE_VENDOR_UNAVAILABLE = "CMR_VENDOR_UNAVAILABLE"
CODE_VALIDATOR_UNAVAILABLE = "CMR_VALIDATOR_UNAVAILABLE"
CODE_SCHEMA_INVALID = "CMR_PIN_SCHEMA_INVALID"


class PinSyncError(RuntimeError):
    """Raised by name (``.code``) when the live pin cannot be trusted."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PinnedBundle:
    """The CMR standards bundle this repo's knowledge is pinned to, live."""

    schema: str
    bundle_ref: str
    manifest_sha: str
    bundle_digest: str
    delivered_at: str
    delivered_by: str
    live_head: str
    drift: bool

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "bundle_ref": self.bundle_ref,
            "manifest_sha": self.manifest_sha,
            "bundle_digest": self.bundle_digest,
            "delivered_at": self.delivered_at,
            "delivered_by": self.delivered_by,
            "live_head": self.live_head,
            "drift": self.drift,
        }


def _load_yaml(path: Path) -> Dict[str, Any]:
    import yaml

    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise PinSyncError(CODE_SCHEMA_INVALID, f"{path} does not contain a YAML mapping")
    return data


def _import_validator():
    """Import the hub's own validator module by path (never re-implemented)."""
    if not VALIDATOR_PATH.is_file():
        raise PinSyncError(
            CODE_VALIDATOR_UNAVAILABLE,
            f"{VALIDATOR_PATH} not present (vendor/CMR submodule not initialized?)",
        )
    spec = importlib.util.spec_from_file_location("_cmr_validate_pin", VALIDATOR_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise PinSyncError(CODE_VALIDATOR_UNAVAILABLE, f"could not load {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def validate_pin_shape(pin_path: Path = PIN_PATH) -> None:
    """Validate ``cmr-pin.yaml`` shape via the hub's own validator, in-process.

    Raises :class:`PinSyncError` (``CMR_PIN_SCHEMA_INVALID``) on any shape
    failure. Drives the real ``main()`` the shell gate calls, rather than
    re-checking fields here — a second implementation is a second place to
    drift out of sync with the schema.
    """
    if not pin_path.is_file():
        raise PinSyncError(CODE_PIN_MISSING, f"{pin_path} not found")
    module = _import_validator()
    rc = module.main(["validate-cmr-pin.py", str(pin_path)])
    if rc != 0:
        raise PinSyncError(CODE_SCHEMA_INVALID, f"{pin_path} failed schema validation (rc={rc})")


def live_vendor_head() -> str:
    """``git -C vendor/CMR rev-parse HEAD`` — the submodule's actual commit."""
    if not VENDOR_CMR.is_dir():
        raise PinSyncError(CODE_VENDOR_UNAVAILABLE, f"{VENDOR_CMR} not checked out")
    try:
        out = subprocess.run(
            ["git", "-C", str(VENDOR_CMR), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise PinSyncError(CODE_VENDOR_UNAVAILABLE, f"could not resolve vendor/CMR HEAD: {exc}") from exc
    head = out.stdout.strip()
    if not head:
        raise PinSyncError(CODE_VENDOR_UNAVAILABLE, "vendor/CMR HEAD resolved empty")
    return head


def pinned_bundle(pin_path: Path = PIN_PATH, *, require_no_drift: bool = True) -> PinnedBundle:
    """The pinned CMR bundle this repo's knowledge is built from, live.

    Reads ``cmr-pin.yaml`` (validated against the hub's schema) and the live
    ``vendor/CMR`` submodule HEAD, and refuses ``CMR_PIN_DRIFT`` when the two
    disagree, unless ``require_no_drift=False`` (used by callers that want to
    *report* drift rather than raise on it, e.g. the knowledge coverage
    report).
    """
    validate_pin_shape(pin_path)
    data = _load_yaml(pin_path)
    live_head = live_vendor_head()
    bundle_ref = str(data.get("bundle_ref", ""))
    drift = bundle_ref != live_head

    if drift and require_no_drift:
        raise PinSyncError(
            CODE_PIN_DRIFT,
            f"{pin_path} bundle_ref={bundle_ref} != vendor/CMR HEAD={live_head}",
        )

    return PinnedBundle(
        schema=str(data.get("schema", "")),
        bundle_ref=bundle_ref,
        manifest_sha=str(data.get("manifest_sha", "")),
        bundle_digest=str(data.get("bundle_digest", "")),
        delivered_at=str(data.get("delivered_at", "")),
        delivered_by=str(data.get("delivered_by", "")),
        live_head=live_head,
        drift=drift,
    )


def check_drift(pin_path: Path = PIN_PATH) -> Optional[PinSyncError]:
    """Return the drift error if the pin has drifted, else ``None``.

    Never raises for drift itself — callers that want the exception should
    call :func:`pinned_bundle` with the default ``require_no_drift=True``.
    """
    try:
        pinned_bundle(pin_path, require_no_drift=True)
    except PinSyncError as exc:
        if exc.code == CODE_PIN_DRIFT:
            return exc
        raise
    return None


def _main(argv) -> int:
    """CLI: print the live pinned bundle as JSON, or fail loudly.

    Exit 0 OK / 1 drift or invalid / 2 cannot-assess (vendor/CMR missing).
    """
    import json

    pin_path = Path(argv[1]) if len(argv) > 1 else PIN_PATH
    try:
        bundle = pinned_bundle(pin_path, require_no_drift=False)
    except PinSyncError as exc:
        if exc.code in (CODE_VENDOR_UNAVAILABLE, CODE_VALIDATOR_UNAVAILABLE):
            print(f"CANNOT-ASSESS {exc.code}: {exc.message}", file=sys.stderr)
            return 2
        print(f"NOT-OK {exc.code}: {exc.message}", file=sys.stderr)
        return 1

    print(json.dumps(bundle.as_dict(), indent=2, sort_keys=True))
    if bundle.drift:
        print(f"NOT-OK {CODE_PIN_DRIFT}: bundle_ref != live vendor/CMR HEAD", file=sys.stderr)
        return 1
    print("OK live_sync: cmr-pin matches live vendor/CMR HEAD, no drift")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
