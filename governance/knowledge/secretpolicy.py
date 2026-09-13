"""Secret policy for indexed material (issue #139).

`make verify` already greps the tree for secrets. The indexer needs its own gate
for a different reason: an asset that is *registered as institutional knowledge*
must not carry credentials, because the catalogue is what people and agents trust
and copy from. A leaked key inside an indexed asset propagates; one in an
unindexed file at least stays where it landed.

The scan reports the rule and line number and deliberately does **not** echo the
matched value — a finding must never become the leak. Patterns mirror
`scripts/check-secrets.sh`; anchoring is loose on purpose, so a false positive is
preferred to a miss.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator, List, Pattern, Sequence, Tuple

# (name, compiled pattern) — deliberately loose; a false positive beats a miss.
_PATTERNS: Tuple[Tuple[str, Pattern[str]], ...] = (
    ("openai-style-key", re.compile(r"sk-[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}")),
    ("aws-access-key-id", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack-token", re.compile(r"xox[abpsr]-[A-Za-z0-9-]{10,}")),
    (
        "credential-assignment",
        re.compile(
            r"(?i)(api[_-]?key|secret|password|passwd|token)"
            r"[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9_\-/+]{20,}"
        ),
    ),
)

# Substrings that mark a value as a documented placeholder rather than a secret.
# Without this the indexer would flag the very examples that teach people what a
# credential assignment looks like.
_ALLOWLIST: Tuple[str, ...] = (
    "sk-...",
    "<token>",
    "<api-key>",
    "changeme",
    "example",
    "placeholder",
    "redacted",
    "xxx",
    "your-",
    "dummy",
)

MAX_LINE_LENGTH = 4000  # a longer line is treated as opaque, not scanned wholesale


@dataclass(frozen=True)
class SecretFinding:
    """A suspected credential in an indexed asset. The value is redacted."""

    rule: str
    line: int
    path: str

    def as_dict(self) -> dict:
        return {"rule": self.rule, "line": self.line, "path": self.path}

    def describe(self) -> str:
        return "%s at %s:%d (value redacted)" % (self.rule, self.path, self.line)


def _is_allowlisted(text: str) -> bool:
    lowered = text.lower()
    return any(marker.lower() in lowered for marker in _ALLOWLIST)


def scan_text(text: str, path: str = "<memory>") -> List[SecretFinding]:
    """Scan ``text`` for credential-shaped content."""
    findings: List[SecretFinding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if len(line) > MAX_LINE_LENGTH:
            continue
        for rule, pattern in _PATTERNS:
            match = pattern.search(line)
            if match is None:
                continue
            if _is_allowlisted(match.group(0)) or _is_allowlisted(line):
                continue
            findings.append(SecretFinding(rule=rule, line=lineno, path=path))
            break  # one finding per line is enough to act on
    return findings


def scan_file(path, reader=None) -> List[SecretFinding]:
    """Scan a file, tolerating binary and unreadable content.

    ``reader`` is an injection seam for tests (it receives the path and returns
    text), so no test has to write a credential-shaped literal to disk.
    """
    if reader is not None:
        return scan_text(reader(path), str(path))
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return scan_text(handle.read(), str(path))
    except OSError:
        return []
