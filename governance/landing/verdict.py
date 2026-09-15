#!/usr/bin/env python3
"""The one seam that consumes ``governance/merge`` (#764).

The merge rule lives in ``governance/merge`` and only there: ``model.merge_verdict``
is the single rule ("this lane may be merged"), and
``engine.MergeGovernanceEngine`` drives the state machine that enforces it
(verify-gate green + independent SME reviewer + no-self-merge, with the owner
autonomous-merge carve-out). This module **loads those modules and hands the
decision to them**. It does not restate the rule: there is no second
``merge_verdict`` here, no local re-implementation of "green + reviewer +
carve-out", and no fallback that decides on its own. If ``governance/merge``
cannot be loaded, the landing driver raises :class:`MergeSeamError` and refuses —
a landing that cannot consult the verdict does not get to invent one.

Why a loader at all: ``governance/merge`` ships (issue #43) as standalone
top-level modules — ``engine.py`` does ``from gate import …`` — exactly as its
own test bootstrap imports them. So its directory is put on ``sys.path`` for the
duration of the import, the modules are imported, and the path and the previous
``sys.modules`` entries are restored. Every imported module is then checked to
have resolved **inside** ``governance/merge``: a bare ``import engine`` that
silently resolved to the repository's own ``engine/`` package would be a
consumption of the wrong code, and a control that consumes the wrong module
proves nothing.
"""

from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

#: ``governance/merge`` sits next to ``governance/landing``.
MERGE_DIR = Path(__file__).resolve().parents[1] / "merge"

_MERGE_NAMES = ("gate", "model", "reviewer", "engine")
_CACHE: dict = {}


class MergeSeamError(RuntimeError):
    """``governance/merge`` could not be loaded — refuse, never decide locally."""


@dataclass(frozen=True)
class MergeVerdict:
    """``governance/merge``'s answer, carried verbatim to the caller."""

    mergeable: bool
    state: str
    reasons: Tuple[str, ...]
    reviewer_id: Optional[str]
    verify_commit: Optional[str]
    audit: Tuple[dict, ...]
    source: str

    def as_dict(self) -> dict:
        return {
            "mergeable": self.mergeable,
            "state": self.state,
            "reasons": list(self.reasons),
            "reviewer_id": self.reviewer_id,
            "verify_commit": self.verify_commit,
            "audit": list(self.audit),
            "source": self.source,
        }


def merge_modules() -> dict:
    """Load (once) the ``governance/merge`` modules this seam consumes."""
    if _CACHE:
        return _CACHE
    directory = MERGE_DIR
    if not (directory / "engine.py").is_file():
        raise MergeSeamError(f"governance/merge is not importable from {directory}")
    saved_path = list(sys.path)
    saved_modules = {name: sys.modules.get(name) for name in _MERGE_NAMES}
    try:
        sys.path.insert(0, str(directory))
        for name in _MERGE_NAMES:
            sys.modules.pop(name, None)
        modules = {name: importlib.import_module(name) for name in _MERGE_NAMES}
    except ImportError as exc:  # pragma: no cover - a missing tree, not a code path
        raise MergeSeamError(f"governance/merge could not be imported: {exc}") from exc
    finally:
        sys.path[:] = saved_path
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
    for name, module in modules.items():
        resolved = Path(str(getattr(module, "__file__", ""))).resolve()
        if directory not in resolved.parents and resolved.parent != directory:
            raise MergeSeamError(
                f"the consumed {name!r} resolved to {resolved}, outside {directory} — "
                "refusing to consume the wrong module"
            )
    _CACHE.update(modules)
    return _CACHE


def gate_outcome(rc: int, commit: Optional[str], evidence: str = "") -> object:
    """A ``governance/merge`` ``gate.VerifyOutcome`` from a raw gate exit code."""
    return merge_modules()["gate"].outcome_from_exit_code(rc, commit, evidence)


def describes_green(outcome: object) -> bool:
    """``gate.VerifyOutcome.is_green`` — green requires OK **and** a commit."""
    return bool(outcome.is_green)


def decide(
    *,
    number: int,
    title: str,
    author: str,
    subject: str,
    branch: str,
    outcome: object,
    owner_carve_out: bool = True,
    author_tenant: str = "platform",
) -> MergeVerdict:
    """Ask ``governance/merge`` whether this lane may merge.

    ``outcome`` is a ``governance/merge`` ``gate.VerifyOutcome`` (built by
    :func:`gate_outcome` from the attestation). The engine is given it as its
    injected gate check, and the merger is the author — the fleet's
    self-merge posture, which only the owner autonomous-merge carve-out permits.
    """
    modules = merge_modules()
    engine = modules["engine"].MergeGovernanceEngine(
        gate_check=lambda _pr, _commit=None: outcome,
        owner_carve_out=owner_carve_out,
    )
    result = engine.run(
        number=number,
        title=title,
        author=author,
        author_tenant=author_tenant,
        subject=subject,
        branch=branch,
        merger=author,
        commit=outcome.commit or "",
    )
    reasons: Tuple[str, ...] = (result.block_reason,) if result.block_reason else ()
    return MergeVerdict(
        mergeable=bool(result.mergeable),
        state=str(result.state.value),
        reasons=reasons,
        reviewer_id=result.reviewer_id,
        verify_commit=result.verify_commit,
        audit=tuple(entry.as_dict() for entry in result.audit),
        source=str(Path(modules["model"].__file__).resolve()),
    )
