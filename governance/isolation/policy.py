"""The lane-isolation surface's declared control policy — read, never duplicated (issue #885).

`identity.py` and `speculative.py` used to hard-code the thresholds that
decide isolation (the repo slug a "Refs" trailer points at, the branch and
worktree naming prefixes, the reserved signature domain, the session-id
length, and the default landing branch a speculative-base claim is re-checked
against). This module reads those values from `controls.yaml` (a sibling of
this file) instead, and gives the rest of the package one place to look them
up — `IsolationControls.load()` — so a value baked into code can never drift
from the declaration that documents it.

The refusal-code vocabulary this surface's Violation-producing modules can
emit is declared the same way: closed, and checked against
`governance.isolation.violation` at load time so an emitted code nobody
declared is CANNOT-ASSESS rather than a silent pass (AGENTS.md GR-12/GR-29).

Exit-code contract (repository convention): an unreadable, malformed, or
incomplete declaration raises :class:`PolicyUnavailable`. Callers in this
package treat that as CANNOT-ASSESS, never as a default silently taking over.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

SCHEMA = "ao.isolation/controls-v1"

#: The packaged declaration — read by default, so no caller's working
#: directory decides which policy governs this surface.
DEFAULT_CONTROLS = Path(__file__).resolve().parent / "controls.yaml"


class PolicyUnavailable(ValueError):
    """The declared control policy is missing, malformed, or incomplete."""


@dataclass(frozen=True)
class IsolationControls:
    """The lane-isolation surface's declared thresholds, validated at load time."""

    path: Path
    repo_slug_default: str
    branch_prefix: str
    worktree_prefix: str
    identity_domain: str
    session_id_len: int
    commit_trailer_template: str
    speculative_default_base: str
    refusal_codes: Tuple[str, ...]

    def as_document(self) -> Dict[str, Any]:
        return {
            "schema": SCHEMA,
            "path": str(self.path),
            "identity": {
                "repo_slug_default": self.repo_slug_default,
                "branch_prefix": self.branch_prefix,
                "worktree_prefix": self.worktree_prefix,
                "identity_domain": self.identity_domain,
                "session_id_len": self.session_id_len,
                "commit_trailer_template": self.commit_trailer_template,
            },
            "speculative": {"default_base": self.speculative_default_base},
            "refusal_codes": list(self.refusal_codes),
        }


def _read_yaml(path: Path) -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, resolved on demand
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PolicyUnavailable(
            f"the declared control policy {path} cannot be read: PyYAML is not installed ({exc})"
        ) from exc
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyUnavailable(f"the declared control policy {path} is unreadable: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError and friends
        raise PolicyUnavailable(f"the declared control policy {path} is not valid YAML: {exc}") from exc


def _mapping(value: Any, what: str, path: Path) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyUnavailable(f"the declared control policy {path} declares {what} as a non-mapping")
    return value


def _str(mapping: Mapping[str, Any], key: str, what: str, path: Path) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PolicyUnavailable(
            f"the declared control policy {path} declares {what} without a non-empty string `{key}`"
        )
    return value


def _positive_int(mapping: Mapping[str, Any], key: str, what: str, path: Path) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PolicyUnavailable(
            f"the declared control policy {path} declares {what}.{key} as {value!r}, "
            "expected a positive integer"
        )
    return value


def load(path: Optional[Path] = None) -> IsolationControls:
    """Read and validate the declared control policy.

    Raises :class:`PolicyUnavailable` for anything that would leave the
    surface governed by a policy it cannot read or that omits a required
    threshold or refusal code.
    """
    # Imported here (not at module scope) to avoid a hard import cycle:
    # violation.py has no dependency on this module, but keeping the check
    # local documents exactly why it is being consulted.
    from .violation import KNOWN_CODES

    path = Path(path) if path else DEFAULT_CONTROLS
    raw = _read_yaml(path)
    if raw is None:
        raise PolicyUnavailable(f"the declared control policy {path} is empty")
    raw = _mapping(raw, "the document", path)

    schema = str(raw.get("schema") or "")
    if schema != SCHEMA:
        raise PolicyUnavailable(
            f"the declared control policy {path} carries schema {schema!r}, expected {SCHEMA!r}"
        )

    identity = _mapping(raw.get("identity"), "`identity`", path)
    speculative = _mapping(raw.get("speculative"), "`speculative`", path)

    commit_trailer_template = _str(identity, "commit_trailer_template", "`identity`", path)
    if "{slug}" not in commit_trailer_template or "{issue}" not in commit_trailer_template:
        raise PolicyUnavailable(
            f"the declared control policy {path} declares identity.commit_trailer_template "
            f"{commit_trailer_template!r} without both {{slug}} and {{issue}} placeholders"
        )

    codes_raw = raw.get("refusal_codes")
    if not isinstance(codes_raw, (list, tuple)) or not codes_raw:
        raise PolicyUnavailable(f"the declared control policy {path} declares no `refusal_codes`")
    codes = tuple(str(code) for code in codes_raw)
    if len(set(codes)) != len(codes):
        raise PolicyUnavailable(f"the declared control policy {path} declares a duplicate refusal code")
    missing = sorted(set(KNOWN_CODES) - set(codes))
    if missing:
        raise PolicyUnavailable(
            f"the declared control policy {path} declares no entry for the refusal code(s) "
            f"this surface can emit: {', '.join(missing)}"
        )
    unknown = sorted(set(codes) - set(KNOWN_CODES))
    if unknown:
        raise PolicyUnavailable(
            f"the declared control policy {path} declares refusal code(s) this surface never "
            f"emits: {', '.join(unknown)}"
        )

    return IsolationControls(
        path=path,
        repo_slug_default=_str(identity, "repo_slug_default", "`identity`", path),
        branch_prefix=_str(identity, "branch_prefix", "`identity`", path),
        worktree_prefix=_str(identity, "worktree_prefix", "`identity`", path),
        identity_domain=_str(identity, "identity_domain", "`identity`", path),
        session_id_len=_positive_int(identity, "session_id_len", "`identity`", path),
        commit_trailer_template=commit_trailer_template,
        speculative_default_base=_str(speculative, "default_base", "`speculative`", path),
        refusal_codes=tuple(sorted(codes)),
    )
