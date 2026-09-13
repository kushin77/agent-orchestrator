#!/usr/bin/env python3
"""FinOps model chooser — tier + thinking-effort enforcement (M26, issue #164).

The brain issues a directive carrying a FinOps block (``model.tier`` +
``model.thinking``). This module turns that block into a **spawn record** and
refuses everything else:

* an unknown tier or an unknown thinking level is refused;
* a **subagent never chooses its own tier** — the record carries the brain's
  choice, and any request that diverges from the directive (up *or* down) is
  refused, because escalation requires a new brain directive;
* a recorded spawn that no longer matches its directive is refused.

The vocabulary is harvested, not invented (`governance/finops/policy.json`
carries the provenance, GR-10):

* tiers ``flash | pro | auditor`` — the ``tiers{}`` keys of
  ``kushin77/capital-underwriting config/leaderboard/tier-policy.json``;
* thinking effort ``low | medium | high`` — ``role_effort()`` in
  ``kushin77/leaderboard lib/fleet-roster.sh``;
* ``none`` — the fleet's thinking-off state (``LB_THINKING_DEEPSEEK=disabled``),
  which is also the standing directive's existing value.

Exit codes follow the repo tri-state: 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS
(a missing or unparseable policy/input is CANNOT-ASSESS, never a pass). The
module is stdlib-only and offline; the dispatcher imports :func:`choose` before
spawning (#163) and ``scripts/check-finops-chooser.sh`` drives the CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

POLICY_PATH = Path(__file__).resolve().parent / "policy.json"

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

CHOSEN_BY_BRAIN = "brain"
ROLE_SISTER = "sister"
ROLE_SUBAGENT = "subagent"

# Refusal findings. Every refusal names exactly one of these.
FINDING_BAD_DIRECTIVE = "FINOPS-BAD-DIRECTIVE"
FINDING_MISSING_MODEL_BLOCK = "FINOPS-MISSING-MODEL-BLOCK"
FINDING_UNKNOWN_TIER = "FINOPS-UNKNOWN-TIER"
FINDING_UNKNOWN_THINKING = "FINOPS-UNKNOWN-THINKING"
FINDING_ROLE_NOT_ALLOWED = "FINOPS-ROLE-NOT-ALLOWED"
FINDING_SELF_ESCALATION = "FINOPS-SELF-ESCALATION"
FINDING_SPAWN_MALFORMED = "FINOPS-SPAWN-MALFORMED"
FINDING_SPAWN_TAMPERED = "FINOPS-SPAWN-TAMPERED"

SPAWN_FIELDS = (
    "role",
    "tier",
    "thinking",
    "model",
    "chosen_by",
    "directive_id",
    "directive_fingerprint",
    "vocabulary_version",
)


class PolicyUnavailable(Exception):
    """The FinOps policy could not be read — no verdict is possible (rc 2)."""


class InputUnreadable(Exception):
    """An input file could not be read or parsed — no verdict is possible (rc 2)."""


@dataclass(frozen=True)
class Finding:
    """One named refusal."""

    code: str
    detail: str

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


@dataclass(frozen=True)
class SpawnRecord:
    """What the fleet will actually run, as chosen by the brain's directive."""

    role: str
    tier: str
    thinking: str
    model: str
    chosen_by: str
    directive_id: str
    directive_fingerprint: str
    vocabulary_version: str
    issue: int | None = None
    budget_hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def load_policy(path: Path | str = POLICY_PATH) -> dict:
    """Read and shape-check the harvested vocabulary policy."""
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise PolicyUnavailable(f"cannot read {target}: {exc}") from exc
    try:
        policy = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PolicyUnavailable(f"{target} is not valid JSON: {exc.msg}") from exc
    vocabulary = policy.get("vocabulary")
    if not isinstance(vocabulary, dict):
        raise PolicyUnavailable(f"{target} has no vocabulary object")
    tiers = vocabulary.get("tiers")
    thinking = vocabulary.get("thinking_levels")
    if not tiers or not thinking:
        raise PolicyUnavailable(f"{target} declares no tiers/thinking_levels")
    for key in ("role_allowlist", "tier_models", "tier_rank", "thinking_rank"):
        if not isinstance(policy.get(key), dict):
            raise PolicyUnavailable(f"{target} has no {key} object")
    return policy


def vocabulary(policy: dict) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return ``(tiers, thinking_levels)`` in the policy's declared order."""
    return (
        tuple(policy["vocabulary"]["tiers"]),
        tuple(policy["vocabulary"]["thinking_levels"]),
    )


def render_vocabulary(policy: dict) -> str:
    """The two canonical vocabulary lines (also pinned in the gate and the README)."""
    tiers, thinking = vocabulary(policy)
    return f"tiers: {', '.join(tiers)}\nthinking: {', '.join(thinking)}\n"


def role_kind(role: str) -> str:
    """Map a channel role to its allowlist key: ``sister``, ``subagent`` or ``""``."""
    if role == ROLE_SISTER:
        return ROLE_SISTER
    if role.startswith("subagent"):
        return ROLE_SUBAGENT
    return ""


def model_fingerprint(model: dict) -> str:
    """Stable digest of the brain's FinOps block — the spawn must carry it back."""
    canonical = json.dumps(model, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _directive_block(directive: object, tiers: tuple[str, ...], thinking: tuple[str, ...]) -> tuple[dict | None, list[Finding]]:
    """Validate the directive's own FinOps block against the harvested vocabulary."""
    if not isinstance(directive, dict):
        return None, [Finding(FINDING_BAD_DIRECTIVE, "a directive must be a JSON object")]
    if directive.get("type") != "directive":
        return None, [Finding(FINDING_BAD_DIRECTIVE, f"type is {directive.get('type')!r}, not 'directive'")]
    model = directive.get("model")
    if not isinstance(model, dict):
        return None, [Finding(FINDING_MISSING_MODEL_BLOCK, "the directive carries no model block to enforce")]
    findings: list[Finding] = []
    tier = model.get("tier")
    level = model.get("thinking")
    if tier not in tiers:
        findings.append(Finding(FINDING_UNKNOWN_TIER, f"tier {tier!r} is outside the harvested vocabulary ({', '.join(tiers)})"))
    if level not in thinking:
        findings.append(Finding(FINDING_UNKNOWN_THINKING, f"thinking {level!r} is outside the harvested vocabulary ({', '.join(thinking)})"))
    if findings:
        return None, findings
    return model, []


def choose(
    policy: dict,
    directive: object,
    role: str,
    *,
    issue: int | None = None,
    requested_tier: str | None = None,
    requested_thinking: str | None = None,
) -> tuple[SpawnRecord | None, list[Finding]]:
    """Choose what ``role`` runs, or refuse with named findings.

    The tier and thinking effort come from the brain's directive. ``requested_*``
    is the agent's own wish; it is checked and **never honoured** — any divergence
    is a self-override and is refused.
    """
    tiers, thinking_levels = vocabulary(policy)
    model, findings = _directive_block(directive, tiers, thinking_levels)
    if model is None:
        return None, findings

    tier = model["tier"]
    level = model["thinking"]

    kind = role_kind(role)
    if not kind:
        return None, [Finding(FINDING_ROLE_NOT_ALLOWED, f"role {role!r} is not a fleet role (sister|subagent-<name>)")]
    allowlist = policy["role_allowlist"].get(kind, {})
    if tier not in allowlist.get("tiers", ()):
        return None, [
            Finding(
                FINDING_ROLE_NOT_ALLOWED,
                f"the directive puts {kind} {role!r} at tier {tier!r}; that role is allowlisted to {', '.join(allowlist.get('tiers', ())) or 'nothing'}",
            )
        ]
    if level not in allowlist.get("thinking_levels", ()):
        return None, [
            Finding(
                FINDING_ROLE_NOT_ALLOWED,
                f"the directive puts {kind} {role!r} at thinking {level!r}; that role is allowlisted to {', '.join(allowlist.get('thinking_levels', ())) or 'nothing'}",
            )
        ]

    # The agent's own request is an override attempt, never a decision.
    if requested_tier is not None:
        if requested_tier not in tiers:
            return None, [Finding(FINDING_UNKNOWN_TIER, f"requested tier {requested_tier!r} is outside the harvested vocabulary ({', '.join(tiers)})")]
        if requested_tier != tier:
            direction = "escalation" if policy["tier_rank"][requested_tier] > policy["tier_rank"][tier] else "downgrade"
            return None, [
                Finding(
                    FINDING_SELF_ESCALATION,
                    f"role {role!r} requested tier {requested_tier!r} but the brain's directive says {tier!r} "
                    f"({direction} is a self-override; a tier changes only in a new brain directive)",
                )
            ]
    if requested_thinking is not None:
        if requested_thinking not in thinking_levels:
            return None, [
                Finding(
                    FINDING_UNKNOWN_THINKING,
                    f"requested thinking {requested_thinking!r} is outside the harvested vocabulary ({', '.join(thinking_levels)})",
                )
            ]
        if requested_thinking != level:
            direction = "escalation" if policy["thinking_rank"][requested_thinking] > policy["thinking_rank"][level] else "downgrade"
            return None, [
                Finding(
                    FINDING_SELF_ESCALATION,
                    f"role {role!r} requested thinking {requested_thinking!r} but the brain's directive says {level!r} "
                    f"({direction} is a self-override; thinking effort changes only in a new brain directive)",
                )
            ]

    record = SpawnRecord(
        role=role,
        tier=tier,
        thinking=level,
        model=policy["tier_models"].get(tier, ""),
        chosen_by=CHOSEN_BY_BRAIN,
        directive_id=str(directive.get("id", "")),
        directive_fingerprint=model_fingerprint(model),
        vocabulary_version=str(policy.get("policy_version", "")),
        issue=issue if issue is not None else _directive_issue(directive),
        budget_hint=str(model.get("budget_hint", "")),
    )
    return record, []


def _directive_issue(directive: dict) -> int | None:
    task = directive.get("task")
    if isinstance(task, dict) and isinstance(task.get("issue"), int):
        return task["issue"]
    return None


def verify_spawn(policy: dict, directive: object, spawn: object) -> list[Finding]:
    """Re-verify a recorded spawn against the directive that authorised it."""
    tiers, thinking_levels = vocabulary(policy)
    model, findings = _directive_block(directive, tiers, thinking_levels)
    if model is None:
        return findings
    if not isinstance(spawn, dict):
        return [Finding(FINDING_SPAWN_MALFORMED, "a spawn record must be a JSON object")]
    missing = [field for field in SPAWN_FIELDS if spawn.get(field) in (None, "")]
    if missing:
        return [Finding(FINDING_SPAWN_MALFORMED, f"spawn record is missing {', '.join(missing)}")]

    problems: list[Finding] = []
    if spawn["chosen_by"] != CHOSEN_BY_BRAIN:
        problems.append(
            Finding(
                FINDING_SPAWN_TAMPERED,
                f"spawn was chosen by {spawn['chosen_by']!r}; only the brain may choose a tier",
            )
        )
    if spawn["tier"] != model["tier"]:
        problems.append(
            Finding(
                FINDING_SPAWN_TAMPERED,
                f"spawn tier {spawn['tier']!r} does not match the directive's {model['tier']!r}",
            )
        )
    if spawn["thinking"] != model["thinking"]:
        problems.append(
            Finding(
                FINDING_SPAWN_TAMPERED,
                f"spawn thinking {spawn['thinking']!r} does not match the directive's {model['thinking']!r}",
            )
        )
    if spawn["directive_fingerprint"] != model_fingerprint(model):
        problems.append(
            Finding(
                FINDING_SPAWN_TAMPERED,
                "spawn fingerprint does not match the directive's FinOps block (the block was rewritten after the spawn)",
            )
        )
    expected_model = policy["tier_models"].get(model["tier"], "")
    if expected_model and spawn["model"] != expected_model:
        problems.append(
            Finding(
                FINDING_SPAWN_TAMPERED,
                f"spawn model {spawn['model']!r} is not the harvested model for tier {model['tier']!r} ({expected_model})",
            )
        )
    if spawn["tier"] not in tiers or spawn["thinking"] not in thinking_levels:
        problems.append(Finding(FINDING_SPAWN_TAMPERED, "spawn vocabulary is outside the harvested policy"))
    return problems


# --- CLI --------------------------------------------------------------------


def _read_json(path: Path | str, what: str) -> object:
    target = Path(path)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputUnreadable(f"cannot read {what} {target}: {exc}") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InputUnreadable(f"{what} {target} is not valid JSON: {exc.msg}") from exc


def _refuse(action: str, findings: list[Finding]) -> int:
    print(f"chooser {action}: REFUSED ({len(findings)} finding(s))", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return EXIT_NOT_OK


def cmd_vocabulary(args: argparse.Namespace, policy: dict) -> int:
    if args.json:
        tiers, thinking = vocabulary(policy)
        print(json.dumps({"tiers": list(tiers), "thinking_levels": list(thinking)}, indent=2))
        return EXIT_OK
    sys.stdout.write(render_vocabulary(policy))
    return EXIT_OK


def cmd_choose(args: argparse.Namespace, policy: dict) -> int:
    directive = _read_json(args.directive, "directive")
    record, findings = choose(
        policy,
        directive,
        args.role,
        issue=args.issue,
        requested_tier=args.request_tier,
        requested_thinking=args.request_thinking,
    )
    if record is None:
        return _refuse("choose", findings)
    if args.write:
        target = Path(args.write)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(record.to_dict(), indent=2))
    else:
        print(
            f"chooser choose: OK — {record.role} at {record.tier}/{record.thinking} "
            f"({record.model}), chosen by {record.chosen_by} (directive {record.directive_id or 'unnamed'})"
        )
    return EXIT_OK


def cmd_verify_spawn(args: argparse.Namespace, policy: dict) -> int:
    directive = _read_json(args.directive, "directive")
    spawn = _read_json(args.spawn, "spawn")
    findings = verify_spawn(policy, directive, spawn)
    if findings:
        return _refuse("verify-spawn", findings)
    print(f"chooser verify-spawn: OK — {spawn.get('role')} matches the brain's directive ({spawn.get('tier')}/{spawn.get('thinking')})")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chooser", description="FinOps tier/thinking chooser for the session fleet (issue #164)")
    sub = parser.add_subparsers(dest="command", required=True)

    vocab = sub.add_parser("vocabulary", help="print the harvested tier/thinking vocabulary")
    vocab.add_argument("--json", action="store_true")

    choose_cmd = sub.add_parser("choose", help="derive a spawn record from a brain directive (refuses overrides)")
    choose_cmd.add_argument("--directive", required=True)
    choose_cmd.add_argument("--role", required=True, help="sister or subagent-<name>")
    choose_cmd.add_argument("--issue", type=int, default=None)
    choose_cmd.add_argument("--request-tier", default=None, help="the agent's own wish; refused when it differs from the directive")
    choose_cmd.add_argument("--request-thinking", default=None)
    choose_cmd.add_argument("--write", default=None, help="persist the spawn record here")
    choose_cmd.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify-spawn", help="re-verify a recorded spawn against its directive")
    verify.add_argument("--directive", required=True)
    verify.add_argument("--spawn", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = load_policy()
    except PolicyUnavailable as exc:
        print(f"chooser: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    handlers = {
        "vocabulary": cmd_vocabulary,
        "choose": cmd_choose,
        "verify-spawn": cmd_verify_spawn,
    }
    try:
        return handlers[args.command](args, policy)
    except InputUnreadable as exc:
        print(f"chooser: CANNOT-ASSESS — {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    raise SystemExit(main())
