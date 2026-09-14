"""The module registry's declared acceptance policy — read, never duplicated (issue #591).

The registry refuses in several places (a duplicated module id, a mandatory
consumer asset with no seed, drift between the hub's two surfaces, a declared
target that landed unregistered, module source carried in-tree) and refuses
membership by name for anything the hub catalog does not carry. Until this module
existed, *which* of those conditions was fatal lived in code and in prose, so the
policy could not be reviewed and the code carried a second copy of it.

This module reads the declaration from ``controls.yaml`` (a sibling of this file)
and gives the registry exactly four things from it:

* **the closed refusal vocabulary** — :meth:`Policy.condition_for` resolves a
  refusal code to the declared condition that governs it, and
  :meth:`Policy.judge` stamps the refusal with that condition's id and
  disposition. A code the artifact does not declare is
  :class:`PolicyUnavailable` (CANNOT-ASSESS), never a silent pass: a refusal
  nobody declared is a refusal nobody reviewed.
* **the fail-closed disposition rule** — a condition that files a *refusal code*
  under ``recorded`` is refused while the policy is read. A refusal that does not
  fail the gate is a formality (AO-GR-4 / GR-12), and a policy file that could
  turn a refusal off would make every other condition decorative. ``recorded``
  belongs to the judgments — the state a name resolves to, a membership refused.
* **the authority on membership** — ``authority.membership`` must be the hub
  catalog and ``authority.claim_effect`` must be ``none``. A policy that would
  let a claim confer membership is refused by name (condition
  ``membership-not-inferred-from-a-claim``), because refusing claims *is* the
  ``kushin77/CMR#952`` drift guard the registry exists to hold.
* **the judged states** — every state a name can resolve to, plus the membership
  refusal, must be declared with its disposition, so :mod:`audit` can record
  every judgment without hard-coding the vocabulary a second time.

Exit-code contract (repository convention): an unreadable, inconsistent, or
weakening policy raises :class:`PolicyUnavailable`, which is a
:class:`~governance.modules.model.CannotAssess` — the CLI maps it to exit 2.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from governance.modules.model import (
    NOT_A_MODULE,
    REFUSAL_CODES,
    STATES,
    CannotAssess,
    Refusal,
)

#: The schema tag the declared policy must carry.
SCHEMA = "ao.module-registry/controls-v1"

#: The packaged declaration — read by default, so no caller's working directory
#: decides which policy judged a registry.
DEFAULT_CONTROLS = Path(__file__).resolve().parent / "controls.yaml"

#: The two dispositions, and only two. `fatal` fails the gate; `recorded` is a
#: judgment kept in the audit trail.
DISPOSITION_FATAL = "fatal"
DISPOSITION_RECORDED = "recorded"
DISPOSITIONS: Tuple[str, ...] = (DISPOSITION_FATAL, DISPOSITION_RECORDED)

#: How a condition is enforced: by the refusal codes it carries, or by this
#: reader while the policy is loaded.
ENFORCED_BY_CODE = "code"
ENFORCED_BY_READER = "reader"
ENFORCEMENTS: Tuple[str, ...] = (ENFORCED_BY_CODE, ENFORCED_BY_READER)

#: The only authority that confers membership, and the only effect a claim may
#: have (the `kushin77/CMR#952` guard).
MEMBERSHIP_AUTHORITY = "hub-catalog"
CLAIM_EFFECT_NONE = "none"

#: Every subject the registry judges — the three states, plus the membership
#: refusal, which is deliberately not a fourth state.
JUDGED_SUBJECTS: Tuple[str, ...] = tuple(STATES) + (NOT_A_MODULE,)


class PolicyUnavailable(CannotAssess):
    """The declared acceptance policy is missing, malformed, or inconsistent.

    Always a :class:`CannotAssess`: the registry cannot be judged by a policy it
    cannot read, and an unreadable policy is never a pass.
    """


@dataclass(frozen=True)
class Condition:
    """One declared acceptance condition, with the refusal codes it governs."""

    id: str
    disposition: str
    enforced_by: str
    statement: str
    codes: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "disposition": self.disposition,
            "enforced_by": self.enforced_by,
            "codes": list(self.codes),
        }


@dataclass(frozen=True)
class Judgment:
    """One declared judgment: a subject the registry resolves, and its weight."""

    subject: str
    disposition: str
    statement: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "subject": self.subject,
            "disposition": self.disposition,
            "statement": self.statement,
        }


@dataclass(frozen=True)
class Policy:
    """The declared acceptance policy, validated at load time."""

    path: Path
    schema: str
    authority: Mapping[str, str]
    dispositions: Tuple[str, ...]
    conditions: Tuple[Condition, ...]
    judgments: Tuple[Judgment, ...]

    # -- the refusal vocabulary ------------------------------------------------
    def condition_for(self, code: str) -> Optional[Condition]:
        """The declared condition governing a refusal code, or ``None``."""
        for condition in self.conditions:
            if code in condition.codes:
                return condition
        return None

    def declares(self, code: str) -> bool:
        return self.condition_for(code) is not None

    def disposition(self, code: str) -> str:
        condition = self.condition_for(code)
        if condition is None:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares no condition for the "
                "refusal code {} — declare it under `conditions:` before the "
                "registry can be judged".format(self.path, code)
            )
        return condition.disposition

    def refusal_codes(self) -> Tuple[str, ...]:
        """Every code the policy declares, sorted (the closed vocabulary)."""
        codes = {code for condition in self.conditions for code in condition.codes}
        return tuple(sorted(codes))

    def fatal_codes(self) -> Tuple[str, ...]:
        return tuple(
            sorted(code for code in self.refusal_codes() if self.disposition(code) == DISPOSITION_FATAL)
        )

    def judge(self, refusal: Refusal) -> Refusal:
        """Stamp a refusal with the declared condition and disposition.

        This is the registry's one call into the policy: a refusal the policy
        cannot judge raises instead of travelling as an unjudged finding.
        """
        condition = self.condition_for(refusal.code)
        if condition is None:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares no condition for the "
                "refusal code {} — a refusal nobody declared cannot be judged, so "
                "the registry cannot be assessed (CANNOT-ASSESS, never a pass)".format(
                    self.path, refusal.code
                )
            )
        return Refusal(
            code=refusal.code,
            subject=refusal.subject,
            detail=refusal.detail,
            source=refusal.source,
            disposition=condition.disposition,
            condition=condition.id,
        )

    # -- the judged states -----------------------------------------------------
    def judged(self, subject: str) -> Judgment:
        for judgment in self.judgments:
            if judgment.subject == subject:
                return judgment
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares no judgment for the state "
            "{!r} — every judged state must be declared so the audit trail can "
            "record it".format(self.path, subject)
        )

    def judged_disposition(self, subject: str) -> str:
        return self.judged(subject).disposition

    def is_fatal(self, disposition: str) -> bool:
        return disposition == DISPOSITION_FATAL

    # -- the declaration as data ----------------------------------------------
    def as_document(self, reference_path: str = "") -> Dict[str, Any]:
        """The declaration, as the registry document carries it."""
        return {
            "schema": self.schema,
            "path": reference_path,
            "authority": dict(self.authority),
            "dispositions": list(self.dispositions),
            "conditions": [condition.as_dict() for condition in self.conditions],
            "judgments": [judgment.as_dict() for judgment in self.judgments],
            "refusal_codes": list(self.refusal_codes()),
        }


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #
def _read_yaml(path: Path) -> Any:
    try:
        import yaml  # noqa: PLC0415 - optional dependency, resolved on demand
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PolicyUnavailable(
            "the declared acceptance policy {} cannot be read: PyYAML is not "
            "installed ({})".format(path, exc)
        ) from exc
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyUnavailable(
            "the declared acceptance policy {} is unreadable: {}".format(path, exc)
        ) from exc
    try:
        return yaml.safe_load(text)
    except Exception as exc:  # yaml.YAMLError and friends
        raise PolicyUnavailable(
            "the declared acceptance policy {} is not valid YAML: {}".format(path, exc)
        ) from exc


def _mapping(value: Any, what: str, path: Path) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares {} as a non-mapping".format(path, what)
        )
    return value


def _statement(raw: Mapping[str, Any], what: str, path: Path) -> str:
    statement = str(raw.get("statement") or "").strip()
    if not statement:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares {} without a `statement` — a "
            "condition nobody can read is a condition nobody can review".format(path, what)
        )
    return statement


def _codes(raw: Mapping[str, Any], condition_id: str, path: Path) -> Tuple[str, ...]:
    codes = raw.get("codes") or []
    if not isinstance(codes, (list, tuple)):
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares `codes` of condition {} as a "
            "non-list".format(path, condition_id)
        )
    out: List[str] = []
    for code in codes:
        name = str(code)
        if name in out:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares the refusal code {} twice "
                "in condition {}".format(path, name, condition_id)
            )
        out.append(name)
    return tuple(out)


def load(path: Optional[Path] = None) -> Policy:
    """Read and validate the declared acceptance policy.

    Raises :class:`PolicyUnavailable` (a ``CannotAssess``) for anything that
    would leave the registry judged by a policy that is missing, malformed,
    incomplete, or weaker than the no-false-green floor.
    """
    path = Path(path) if path else DEFAULT_CONTROLS
    raw = _read_yaml(path)
    if raw is None:
        raise PolicyUnavailable("the declared acceptance policy {} is empty".format(path))
    raw = _mapping(raw, "the document", path)

    schema = str(raw.get("schema") or "")
    if schema != SCHEMA:
        raise PolicyUnavailable(
            "the declared acceptance policy {} carries schema {!r}, expected {!r}".format(
                path, schema, SCHEMA
            )
        )

    authority_raw = _mapping(raw.get("authority"), "`authority`", path)
    membership = str(authority_raw.get("membership") or "")
    if membership != MEMBERSHIP_AUTHORITY:
        raise PolicyUnavailable(
            "the declared acceptance policy {} would let {!r} confer membership: the "
            "hub catalog is the authority (kushin77/CMR#952)".format(path, membership or "nothing")
        )
    claim_effect = str(authority_raw.get("claim_effect") or "")
    if claim_effect != CLAIM_EFFECT_NONE:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares the condition "
            "membership-not-inferred-from-a-claim and then sets `claim_effect` to "
            "{!r}: a claim would confer membership".format(path, claim_effect or "nothing")
        )
    authority = {
        "membership": membership,
        "claim_effect": claim_effect,
        "citation": str(authority_raw.get("citation") or ""),
    }

    dispositions_raw = raw.get("dispositions") or []
    if not isinstance(dispositions_raw, (list, tuple)):
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares `dispositions` as a non-list".format(path)
        )
    dispositions = tuple(str(item) for item in dispositions_raw)
    if dispositions != DISPOSITIONS:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares dispositions {!r}, expected "
            "{!r} — the vocabulary is closed".format(path, list(dispositions), list(DISPOSITIONS))
        )

    conditions_raw = raw.get("conditions") or []
    if not isinstance(conditions_raw, (list, tuple)) or not conditions_raw:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares no conditions".format(path)
        )
    conditions: List[Condition] = []
    seen_ids: List[str] = []
    for item in conditions_raw:
        entry = _mapping(item, "an entry of `conditions`", path)
        condition_id = str(entry.get("id") or "")
        if not condition_id:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares a condition without an "
                "`id`".format(path)
            )
        if condition_id in seen_ids:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares the condition {} "
                "twice".format(path, condition_id)
            )
        seen_ids.append(condition_id)
        disposition = str(entry.get("disposition") or "")
        if disposition not in DISPOSITIONS:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares condition {} with "
                "disposition {!r}, which is not one of {}".format(
                    path, condition_id, disposition, ", ".join(DISPOSITIONS)
                )
            )
        enforced_by = str(entry.get("enforced_by") or "")
        if enforced_by not in ENFORCEMENTS:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares condition {} with "
                "enforced_by {!r}, expected {} or {}".format(
                    path, condition_id, enforced_by, ENFORCED_BY_CODE, ENFORCED_BY_READER
                )
            )
        codes = _codes(entry, condition_id, path)
        if not codes and enforced_by == ENFORCED_BY_CODE:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares condition {} as enforced "
                "by a code but carries no `codes`".format(path, condition_id)
            )
        if codes and enforced_by == ENFORCED_BY_READER:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares condition {} as enforced "
                "by the reader but carries refusal codes — say which it is".format(
                    path, condition_id
                )
            )
        if codes and disposition != DISPOSITION_FATAL:
            # The fail-closed rule. A refusal code the policy files as `recorded`
            # is a refusal the gate would not fail on, which is exactly the
            # formality this artifact exists to prevent.
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares condition {} as "
                "enforced by code but files refusal code(s) {} as {!r}: a refusal is "
                "always {} (a gate that can be turned off by editing this file is a "
                "gate that cannot fail)".format(
                    path, condition_id, ", ".join(codes), disposition, DISPOSITION_FATAL
                )
            )
        conditions.append(
            Condition(
                id=condition_id,
                disposition=disposition,
                enforced_by=enforced_by,
                statement=_statement(entry, "condition {}".format(condition_id), path),
                codes=codes,
            )
        )

    declared_codes = [code for condition in conditions for code in condition.codes]
    for code in declared_codes:
        if declared_codes.count(code) > 1:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares the refusal code {} in "
                "more than one condition".format(path, code)
            )
    missing = sorted(set(REFUSAL_CODES) - set(declared_codes))
    if missing:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares no condition for the refusal "
            "code(s): {} — the registry can emit them, so the policy must judge "
            "them".format(path, ", ".join(missing))
        )
    unknown = sorted(set(declared_codes) - set(REFUSAL_CODES))
    if unknown:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares refusal code(s) the registry "
            "cannot emit: {}".format(path, ", ".join(unknown))
        )

    judgments_raw = raw.get("judgments") or []
    if not isinstance(judgments_raw, (list, tuple)) or not judgments_raw:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares no judgments".format(path)
        )
    judgments: List[Judgment] = []
    for item in judgments_raw:
        entry = _mapping(item, "an entry of `judgments`", path)
        subject = str(entry.get("subject") or "")
        if not subject:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares a judgment without a "
                "`subject`".format(path)
            )
        disposition = str(entry.get("disposition") or "")
        if disposition not in DISPOSITIONS:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares judgment {} with "
                "disposition {!r}, which is not one of {}".format(
                    path, subject, disposition, ", ".join(DISPOSITIONS)
                )
            )
        if disposition != DISPOSITION_RECORDED:
            raise PolicyUnavailable(
                "the declared acceptance policy {} declares the judged state {} as "
                "{!r}: a judged state is evidence recorded in the audit trail, never "
                "fatal — {!r} belongs to the refusal codes".format(
                    path, subject, disposition, DISPOSITION_FATAL
                )
            )
        judgments.append(
            Judgment(
                subject=subject,
                disposition=disposition,
                statement=_statement(entry, "judgment {}".format(subject), path),
            )
        )
    judged = [judgment.subject for judgment in judgments]
    if len(set(judged)) != len(judged):
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares a judged state more than "
            "once".format(path)
        )
    undecided = [subject for subject in JUDGED_SUBJECTS if subject not in judged]
    if undecided:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares no judgment for the judged "
            "state(s): {} — every state a name can resolve to is recorded, so every "
            "one must be declared".format(path, ", ".join(undecided))
        )
    unexpected = sorted(set(judged) - set(JUDGED_SUBJECTS))
    if unexpected:
        raise PolicyUnavailable(
            "the declared acceptance policy {} declares judged state(s) the registry "
            "cannot resolve: {}".format(path, ", ".join(unexpected))
        )

    return Policy(
        path=path,
        schema=schema,
        authority=authority,
        dispositions=dispositions,
        conditions=tuple(conditions),
        judgments=tuple(judgments),
    )
