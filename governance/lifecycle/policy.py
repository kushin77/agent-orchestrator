"""The lifecycle package's declared acceptance policy — read, never duplicated (issue #885).

Two judgments this module reads from ``controls.yaml`` (a sibling of this file)
so they are declared once and enforced from one place:

* **the closed invariant vocabulary** — :func:`load` cross-checks the declared
  ``closure_invariants.codes`` against ``model.INVARIANTS`` in both directions.
  A code the model can emit that this file does not declare, or a code this
  file declares that the model does not carry, is a policy defect
  (:class:`PolicyUnavailable`, a ``CannotAssess``) — the same no-false-green
  posture ``governance/modules/policy.py`` established for the module registry
  (issue #591): a rule nobody declared is a rule nobody reviewed.
* **the retire preconditions** — :meth:`Policy.check_retire` is the one place
  that decides whether a retirement's reason and citation are substantial
  enough to record, so ``directive.retire`` reads the threshold instead of
  carrying its own copy of it.

Exit-code contract (repository convention): an unreadable, malformed, or
vocabulary-drifted policy raises :class:`PolicyUnavailable`, mapped by callers
to CANNOT-ASSESS (rc 2) — never a silent pass.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

SCHEMA = "ao.lifecycle/controls-v1"

#: The packaged declaration — read by default, so no caller's working directory
#: decides which policy governs a lifecycle run.
DEFAULT_CONTROLS = Path(__file__).resolve().parent / "controls.yaml"

#: Overrides the controls file this package reads, for the gate's own
#: mutation-provocation fixtures (issue #885): a scratch copy with one value
#: changed, pointed at through the environment rather than a code path that
#: only exists for the test — the real ``load``/``load_for_model`` are what run.
CONTROLS_ENV = "AO_LIFECYCLE_CONTROLS"


def default_controls_path() -> Path:
    override = os.environ.get(CONTROLS_ENV, "").strip()
    return Path(override) if override else DEFAULT_CONTROLS


class PolicyUnavailable(RuntimeError):
    """The declared acceptance policy is missing, malformed, or drifted from the code it governs.

    Deliberately a plain ``RuntimeError`` rather than importing
    ``governance.modules.model.CannotAssess``: ``governance/lifecycle`` does not
    depend on ``governance/modules``, and the lifecycle CLI's own exit-code
    contract (``cli.py`` ``EXIT_CANNOT_ASSESS``) already maps any
    ``RuntimeError`` raised out of a command to CANNOT-ASSESS.
    """


@dataclass(frozen=True)
class RetirePolicy:
    min_reason_length: int
    require_superseded_by: bool


@dataclass(frozen=True)
class QuarantinePolicy:
    require_tracked_by: bool
    stale_disposition: str


@dataclass(frozen=True)
class InvariantDecl:
    code: str
    subject_kind: str


@dataclass(frozen=True)
class Policy:
    path: Path
    codes: Tuple[InvariantDecl, ...]
    retire: RetirePolicy
    quarantine: QuarantinePolicy

    def code_names(self) -> Tuple[str, ...]:
        return tuple(entry.code for entry in self.codes)

    def subject_kind(self, code: str) -> str:
        for entry in self.codes:
            if entry.code == code:
                return entry.subject_kind
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares no closure invariant {!r}".format(self.path, code)
        )

    def check_retire(self, *, reason: str, superseded_by: Tuple[int, ...]) -> None:
        """Refuse a retirement request that does not clear the declared thresholds.

        Raises :class:`PolicyUnavailable`, distinct from
        ``directive.DirectiveRefused`` — a caller that wants the closed refusal
        vocabulary catches both, but a mutation test that lowers or removes a
        threshold here must be able to tell "the policy let it through" from
        "the code refused it anyway".
        """
        reason = (reason or "").strip()
        if len(reason) < self.retire.min_reason_length:
            raise PolicyUnavailable(
                "the retirement reason {!r} is {} character(s); the declared acceptance policy {} "
                "requires at least {}".format(reason, len(reason), self.path, self.retire.min_reason_length)
            )
        if self.retire.require_superseded_by and not superseded_by:
            raise PolicyUnavailable(
                "the declared acceptance policy {} requires at least one superseded_by issue; none was "
                "given".format(self.path)
            )


def _read_yaml(path: Path) -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, resolved on demand
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PolicyUnavailable(
            "the declared acceptance policy {} cannot be read: PyYAML is not installed ({})".format(path, exc)
        ) from exc
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyUnavailable("the declared acceptance policy {} is unreadable: {}".format(path, exc)) from exc
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError and friends
        raise PolicyUnavailable("the declared acceptance policy {} is not valid YAML: {}".format(path, exc)) from exc


def _mapping(value: Any, what: str, path: Path) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyUnavailable("the declared acceptance policy {} declares {} as a non-mapping".format(path, what))
    return value


def load(path: Path | None = None, *, model_codes: Mapping[str, str] | None = None) -> Policy:
    """Read and validate the declared policy.

    ``model_codes`` is ``{code: subject_kind}`` from ``model.INVARIANTS`` (or a
    fixture of it in a test). When given, the vocabulary is cross-checked in
    both directions; the caller that governs the real package (``model.py``)
    always passes it, so a code drifting out of sync between the two files is
    caught the moment the module is imported — the whole reason this exists.
    """
    path = Path(path) if path else default_controls_path()
    raw = _read_yaml(path)
    if raw is None:
        raise PolicyUnavailable("the declared acceptance policy {} is empty".format(path))
    raw = _mapping(raw, "the document", path)

    schema = str(raw.get("schema") or "")
    if schema != SCHEMA:
        raise PolicyUnavailable(
            "the declared acceptance policy {} carries schema {!r}, expected {!r}".format(path, schema, SCHEMA)
        )

    invariants_raw = _mapping(raw.get("closure_invariants"), "`closure_invariants`", path)
    codes_raw = invariants_raw.get("codes") or []
    if not isinstance(codes_raw, list) or not codes_raw:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares no closure_invariants.codes".format(path)
        )
    codes: list[InvariantDecl] = []
    seen: set[str] = set()
    for entry in codes_raw:
        entry = _mapping(entry, "an entry of closure_invariants.codes", path)
        code = str(entry.get("code") or "")
        kind = str(entry.get("subject_kind") or "")
        if not code:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares a closure invariant without a `code`".format(path)
            )
        if code in seen:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares the closure invariant {} twice".format(path, code)
            )
        if kind not in {"item", "epic", "baseline"}:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares closure invariant {} with subject_kind {!r}, "
                "expected item, epic or baseline".format(path, code, kind)
            )
        seen.add(code)
        codes.append(InvariantDecl(code=code, subject_kind=kind))

    if model_codes is not None:
        declared = {entry.code: entry.subject_kind for entry in codes}
        missing = sorted(set(model_codes) - set(declared))
        extra = sorted(set(declared) - set(model_codes))
        if missing:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares no closure invariant for: {} — model.py can emit "
                "them, so the policy must declare them (no-false-green)".format(path, ", ".join(missing))
            )
        if extra:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares closure invariant(s) model.py cannot emit: "
                "{}".format(path, ", ".join(extra))
            )
        drifted = sorted(code for code in declared if declared[code] != model_codes[code])
        if drifted:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares a different subject_kind than model.py for: "
                "{}".format(path, ", ".join(drifted))
            )

    retire_raw = _mapping(raw.get("retire"), "`retire`", path)
    try:
        min_len = int(retire_raw.get("min_reason_length"))
    except (TypeError, ValueError) as exc:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares retire.min_reason_length as a non-integer".format(path)
        ) from exc
    if min_len < 1:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares retire.min_reason_length < 1, which would accept an "
            "empty reason".format(path)
        )
    retire = RetirePolicy(
        min_reason_length=min_len,
        require_superseded_by=bool(retire_raw.get("require_superseded_by", True)),
    )

    quarantine_raw = _mapping(raw.get("quarantine"), "`quarantine`", path)
    stale_disposition = str(quarantine_raw.get("stale_disposition") or "")
    if stale_disposition != "reported":
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares quarantine.stale_disposition {!r}, expected "
            "'reported' — a quarantine that goes stale silently is a quarantine that never shrinks".format(
                path, stale_disposition
            )
        )
    quarantine = QuarantinePolicy(
        require_tracked_by=bool(quarantine_raw.get("require_tracked_by", True)),
        stale_disposition=stale_disposition,
    )

    return Policy(path=path, codes=tuple(codes), retire=retire, quarantine=quarantine)


def load_for_model() -> Policy:
    """Load the packaged policy, cross-checked against the real ``model.INVARIANTS``.

    The one call site production code uses. Kept separate from :func:`load` so
    a test can validate a mutated controls file against a *fixture* vocabulary
    without importing ``model`` at all.
    """
    from governance.lifecycle.model import INVARIANTS  # noqa: PLC0415 - avoids a cycle

    model_codes = {inv.code: inv.subject_kind for inv in INVARIANTS}
    return load(model_codes=model_codes)
