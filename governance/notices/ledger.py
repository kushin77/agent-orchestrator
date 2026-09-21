"""Append-only, hash-chained notice ledger (issue #1269, EPIC #1268).

---knowledge---
module_id: governance.notices.ledger
system: governance
app: notices
solution_class: pattern
patterns: [append-only-ledger]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [LedgerIntegrityError, canonical_bytes, entry_hash, file_digest, ledger_path, read, append, verify]
invariants: ""
gotchas: ""
related: ["#1268", "#1269"]
do_not_duplicate: null
---knowledge---

WHY A LEDGER, WHEN THE ACKS ARE ALREADY FILES
--------------------------------------------
The failure this lane closes was not "the ack file was lost" -- it was that
**nothing recorded that a rule had been broadcast at all**. A record with a
digest is what turns "we sent it" into evidence, and it is the half a runtime
cannot retract: a record edited after the fact no longer matches the digest the
ledger recorded, and a ledger edited after the fact no longer chains.

The shape is the repository's own (``registry/packs/pack_events.py``):
one JSON object per line, ``hash = sha256(canonical JSON of every field except
hash)``, ``prevHash`` of record *n* equals ``hash`` of record *n-1*, and the
first record's ``prevHash`` is the fixed all-zero genesis hash. Reordering,
editing or deleting a line breaks the chain.

LIMITS, STATED RATHER THAN IMPLIED
----------------------------------
* The ledger records **writes** (a publish, an ack). ``evaluate`` is read-only and
  appends nothing, so a gate run leaves no trace of its own.
* ``verify`` checks the chain always, and the recorded digest for every record
  that is still on disk. A record that is *gone* is not a finding: the fleet's
  notices directory is runtime state and is legitimately cleaned, and the ledger
  is the trace that outlives it. The refusal is for a record that **changed**.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from governance.notices.finding import Finding

GENESIS_HASH = "0" * 64
#: The closed set of events the ledger carries. A record with any other kind is
#: refused when read, so the log cannot drift into an ad-hoc journal.
EVENT_KINDS = ("publish", "ack")
LEDGER_RELPATH = "notices/ledger.jsonl"

MALFORMED = "ledger-malformed"
CHAIN_BROKEN = "ledger-chain-broken"
DIGEST_MISMATCH = "ledger-digest-mismatch"

#: Every refusal this module can report; held to governance/notices/controls.yaml
#: in both directions by `cli.py controls` (see notice_records.REFUSAL_CODES).
REFUSAL_CODES = (MALFORMED, CHAIN_BROKEN, DIGEST_MISMATCH)


class LedgerIntegrityError(RuntimeError):
    """The ledger cannot be read as a chain (malformed line, unknown event kind)."""


def canonical_bytes(document: Mapping[str, Any]) -> bytes:
    """The one canonical rendering a digest is taken over."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def entry_hash(entry: Mapping[str, Any]) -> str:
    """The chain hash of one entry: everything except the hash field, canonically."""
    body = {key: value for key, value in entry.items() if key != "hash"}
    return hashlib.sha256(canonical_bytes(body)).hexdigest()


def file_digest(path: Path) -> str:
    """The digest of a record file's bytes (what the ledger remembers)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ledger_path(fleet_dir: Path | str) -> Path:
    return Path(fleet_dir) / LEDGER_RELPATH


def read(fleet_dir: Path | str) -> list[dict]:
    """Every entry, in order. Raises when the log is not a readable chain."""
    path = ledger_path(fleet_dir)
    if not path.is_file():
        return []
    entries: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise LedgerIntegrityError("%s cannot be read: %s" % (path, exc)) from exc
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError as exc:
            raise LedgerIntegrityError("%s:%d is not JSON: %s" % (path, number, exc)) from exc
        if not isinstance(entry, dict):
            raise LedgerIntegrityError("%s:%d is not an object" % (path, number))
        if str(entry.get("event") or "") not in EVENT_KINDS:
            raise LedgerIntegrityError(
                "%s:%d carries event '%s', outside %s"
                % (path, number, entry.get("event"), "|".join(EVENT_KINDS))
            )
        entries.append(entry)
    return entries


def append(
    fleet_dir: Path | str,
    *,
    event: str,
    notice: str,
    runtime: str = "",
    record: str = "",
    digest: str = "",
    at: str,
) -> dict:
    """Append one entry, chained to the last. Refuses an event outside the set."""
    if event not in EVENT_KINDS:
        raise LedgerIntegrityError(
            "refusing event '%s': the ledger carries %s" % (event, "|".join(EVENT_KINDS))
        )
    entries = read(fleet_dir)
    previous = entries[-1] if entries else None
    entry = {
        "seq": (int(previous["seq"]) + 1) if previous else 1,
        "event": event,
        "notice": notice,
        "runtime": runtime,
        "record": record,
        "digest": digest,
        "at": at,
        "prevHash": str(previous["hash"]) if previous else GENESIS_HASH,
    }
    entry["hash"] = entry_hash(entry)
    path = ledger_path(fleet_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True, ensure_ascii=True) + "\n")
    return entry


def verify(fleet_dir: Path | str) -> list[Finding]:
    """The chain, and every recorded digest a record on disk can be checked against."""
    findings: list[Finding] = []
    try:
        entries = read(fleet_dir)
    except LedgerIntegrityError as exc:
        return [Finding(MALFORMED, str(exc))]
    expected_prev = GENESIS_HASH
    for index, entry in enumerate(entries, start=1):
        subject = "seq=%s" % entry.get("seq", "?")
        if str(entry.get("prevHash") or "") != expected_prev:
            findings.append(Finding(
                CHAIN_BROKEN, subject,
                "prevHash %s does not chain to the previous entry's %s"
                % (entry.get("prevHash"), expected_prev),
            ))
        if entry_hash(entry) != str(entry.get("hash") or ""):
            findings.append(Finding(
                CHAIN_BROKEN, subject, "the entry's own hash does not match its contents",
            ))
        expected_prev = str(entry.get("hash") or "")
        if index != int(entry.get("seq") or 0):
            findings.append(Finding(
                CHAIN_BROKEN, subject, "the sequence number is not this entry's position",
            ))
        record = str(entry.get("record") or "")
        if not record:
            continue
        path = Path(fleet_dir) / record
        if not path.is_file():
            continue  # runtime state may legitimately be cleaned; the ledger is the trace
        if file_digest(path) != str(entry.get("digest") or ""):
            findings.append(Finding(
                DIGEST_MISMATCH, "%s:%s" % (entry.get("notice"), record),
                "the record on disk no longer matches the digest the ledger recorded for "
                "event '%s'" % entry.get("event"),
            ))
    return findings
