"""Approval as a signed record, not a chat word (issue #1272).

Distinct from ``model.py`` (issue #416): that module *projects* an approval
from an existing authority (claims, directives) and writes nothing. This
module is the authority itself for the ops-scope kinds — ``merge`` /
``delete`` / ``pause`` / ``flip`` — that today are granted by someone typing
"approved" in chat. A record here is ``{actor, scope:{kind,target}, ts,
expires, signature}``; nothing merges, deletes, pauses or flips without one
that verifies (fail closed).

Key handling mirrors ``guardrails/dlp/hmac_audit.py``: the signing key comes
from ``AO_APPROVALS_HMAC_KEY`` (declared in ``infra/env/registry.yaml``,
``secret: true``) or an explicit key injected by tests/CLI. No embedded or
fallback key ships in this module (GR-6) — a misconfigured deployment cannot
silently sign with a known key.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Tuple

_ENV_KEY = "AO_APPROVALS_HMAC_KEY"

KINDS: Tuple[str, ...] = ("merge", "delete", "pause", "flip")


class ApprovalKeyError(RuntimeError):
    """No HMAC key available (fail closed)."""


class ApprovalRefused(Exception):
    """A consumer's ``check`` was refused. ``code`` is the machine-readable name."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _key(explicit: Optional[bytes] = None) -> bytes:
    if explicit is not None:
        if not explicit:
            raise ApprovalKeyError("HMAC key is empty (fail closed)")
        return explicit
    raw = os.environ.get(_ENV_KEY)
    if not raw:
        raise ApprovalKeyError(f"no HMAC key: set {_ENV_KEY} or pass an explicit key (fail closed)")
    return raw.encode("utf-8")


def parse_scope(scope: str) -> Tuple[str, str]:
    """``"merge:pr#123"`` -> ``("merge", "pr#123")``. Refuses an unknown kind or bad shape."""
    if ":" not in scope:
        raise ApprovalRefused("scope-malformed", scope)
    kind, _, target = scope.partition(":")
    if kind not in KINDS or not target:
        raise ApprovalRefused("scope-malformed", scope)
    return kind, target


def _scope_id(kind: str, target: str) -> str:
    safe_target = "".join(c if c.isalnum() or c in "-_." else "_" for c in target)
    return f"{kind}__{safe_target}"


def _canonical(fields: dict) -> bytes:
    signing = {k: v for k, v in fields.items() if k != "signature"}
    return json.dumps(signing, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True)
class ApprovalRecord:
    actor: str
    kind: str
    target: str
    ts: float
    expires: float
    signature: str = ""
    used: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    def scope(self) -> str:
        return f"{self.kind}:{self.target}"

    def signing_fields(self) -> dict:
        return {"actor": self.actor, "kind": self.kind, "target": self.target, "ts": self.ts, "expires": self.expires}

    def signed(self, key: Optional[bytes] = None) -> "ApprovalRecord":
        sig = hmac.new(_key(key), _canonical(self.signing_fields()), hashlib.sha256).hexdigest()
        return ApprovalRecord(self.actor, self.kind, self.target, self.ts, self.expires, sig, self.used)

    def verify_signature(self, key: Optional[bytes] = None) -> bool:
        if not self.signature:
            return False
        expected = hmac.new(_key(key), _canonical(self.signing_fields()), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, self.signature)


def default_store(root: Optional[Path] = None) -> Path:
    base = root if root is not None else Path(__file__).resolve().parents[4]
    return base / ".fleet" / "approvals"


def grant(
    actor: str,
    scope: str,
    ttl_seconds: float,
    *,
    store: Optional[Path] = None,
    key: Optional[bytes] = None,
    now: Optional[float] = None,
) -> ApprovalRecord:
    """Create (or replace) the pending approval record for ``scope``. Writes the tree."""
    kind, target = parse_scope(scope)
    ts = now if now is not None else time.time()
    record = ApprovalRecord(actor=actor, kind=kind, target=target, ts=ts, expires=ts + ttl_seconds).signed(key)
    store_dir = store if store is not None else default_store()
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / f"{_scope_id(kind, target)}.json"
    path.write_text(json.dumps(record.to_dict(), indent=2, sort_keys=True) + "\n")
    return record


def _load(scope: str, store: Path) -> ApprovalRecord:
    kind, target = parse_scope(scope)
    path = store / f"{_scope_id(kind, target)}.json"
    if not path.exists():
        raise ApprovalRefused("approval-missing", scope)
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        raise ApprovalRefused("approval-malformed", f"{scope}: {exc}") from exc
    try:
        return ApprovalRecord(
            actor=raw["actor"],
            kind=raw["kind"],
            target=raw["target"],
            ts=raw["ts"],
            expires=raw["expires"],
            signature=raw.get("signature", ""),
            used=bool(raw.get("used", False)),
        )
    except KeyError as exc:
        raise ApprovalRefused("approval-malformed", f"{scope}: missing {exc}") from exc


def check(
    scope: str,
    *,
    store: Optional[Path] = None,
    key: Optional[bytes] = None,
    now: Optional[float] = None,
    consume: bool = True,
) -> ApprovalRecord:
    """Refuse (by name) or return the valid record for ``scope``.

    Refusals: ``approval-missing:<scope>``, ``approval-tampered:<scope>``,
    ``approval-expired:<scope>``, ``approval-used:<scope>`` (merge scope is
    single-use — a second ``check`` against the same granted record refuses).
    A valid record is marked used (for ``merge``) when ``consume`` is True,
    which is the default a real consumer wants; ``list``/inspection callers
    pass ``consume=False``.
    """
    store_dir = store if store is not None else default_store()
    record = _load(scope, store_dir)
    if not record.verify_signature(key):
        raise ApprovalRefused("approval-tampered", scope)
    when = now if now is not None else time.time()
    if when >= record.expires:
        raise ApprovalRefused("approval-expired", scope)
    if record.kind == "merge" and record.used:
        raise ApprovalRefused("approval-used", scope)
    if record.kind == "merge" and consume:
        used_record = ApprovalRecord(record.actor, record.kind, record.target, record.ts, record.expires, record.signature, True)
        path = store_dir / f"{_scope_id(record.kind, record.target)}.json"
        path.write_text(json.dumps(used_record.to_dict(), indent=2, sort_keys=True) + "\n")
    return record


def list_records(store: Optional[Path] = None) -> Tuple[ApprovalRecord, ...]:
    store_dir = store if store is not None else default_store()
    if not store_dir.exists():
        return ()
    records = []
    for path in sorted(store_dir.glob("*.json")):
        try:
            raw = json.loads(path.read_text())
            records.append(
                ApprovalRecord(
                    actor=raw["actor"],
                    kind=raw["kind"],
                    target=raw["target"],
                    ts=raw["ts"],
                    expires=raw["expires"],
                    signature=raw.get("signature", ""),
                    used=bool(raw.get("used", False)),
                )
            )
        except (json.JSONDecodeError, OSError, KeyError):
            continue
    return tuple(records)
