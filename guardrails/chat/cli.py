"""guardrails.chat.cli — command-line face of the chat guard (used by its gate).

Run from ``guardrails/`` so the package resolves the way its siblings do:

.. code-block:: console

    python3 -m chat controls
    python3 -m chat guard-turn --turn turn.json --grounding grounding.json
    python3 -m chat guard-turn --turn turn.json --enable data-egress-guard
    python3 -m chat inbound --output answer.json --grounding grounding.json
    python3 -m chat retrieval --grounding grounding.json

Every command prints one JSON document on stdout and speaks the guard-honesty
exit-code contract (``guardrails/honesty/tristate.py``):

===========  ==================================================================
exit code    meaning
===========  ==================================================================
0            the guard assessed the input and allowed it (WARN/LOG)
1            the guard assessed the input and refused it (BLOCK)
2            the guard could **not** assess the input (undecidable, fail-closed)
===========  ==================================================================

A JSON error document on stderr accompanies exit code 2, so a caller never has
to guess whether a refusal was a decision or a failure.


---knowledge---
module_id: guardrails.chat.cli
system: guardrails
app: chat
solution_class: pattern
patterns: [subcommand-table, tri-state-exit, deterministic-output]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [cmd_controls, cmd_retrieval, cmd_guard_turn, cmd_inbound, build_parser, main]
invariants: "every command prints one JSON document and maps its outcome onto the guard-honesty exit-code contract"
gotchas: "run from guardrails/ so the package resolves the way its siblings do"
related: ["#507", "#28"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .egress import OutboundTurn
from .inbound import InboundValidator
from .policy import ControlBinding, bound_ids
from .retrieval import RetrievalGuard
from .turn import ChatTurnGuard
from .verdict import DecisionLevel

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_CANNOT_ASSESS = 2


def _emit(document: Any, *, stream=None) -> None:
    print(json.dumps(document, indent=2, sort_keys=True), file=stream or sys.stdout)


def _read_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _binding(args: argparse.Namespace) -> ControlBinding:
    """The controls binding for this invocation (``--controls`` / ``--enable``)."""
    enabled = list(getattr(args, "enable", []) or [])
    if enabled:
        return ControlBinding.with_controls_enabled(
            enabled, path=Path(args.controls) if args.controls else None
        )
    if args.controls:
        return ControlBinding.from_path(Path(args.controls))
    return ControlBinding.default()


def _exit_for(outcome: Any) -> int:
    """Map a guard outcome to the exit-code contract (undecidable never passes)."""
    if not outcome.ran:
        return EXIT_CANNOT_ASSESS
    return EXIT_REFUSED if outcome.decision is DecisionLevel.BLOCK else EXIT_OK


def _cannot_assess(message: str, document: Dict[str, Any]) -> int:
    _emit({"error": message, **document}, stream=sys.stderr)
    return EXIT_CANNOT_ASSESS


def cmd_controls(args: argparse.Namespace) -> int:
    """Print the bound controls, their registration state and their state."""
    binding = _binding(args)
    document = {
        "bound": list(bound_ids()),
        "binding": binding.describe(),
    }
    if binding.undecidable:
        return _cannot_assess(binding.error, document)
    _emit(document)
    return EXIT_OK


def cmd_retrieval(args: argparse.Namespace) -> int:
    """Scan a grounding envelope's fragments before they can enter a prompt."""
    guard = RetrievalGuard()
    outcome = guard.guard(_read_json(args.grounding))
    _emit(outcome.to_dict())
    return _exit_for(outcome.outcome)


def cmd_guard_turn(args: argparse.Namespace) -> int:
    """Guard one outbound turn (retrieval, egress, policy binding)."""
    turn = OutboundTurn.from_mapping(_read_json(args.turn))
    envelope = _read_json(args.grounding) if args.grounding else None
    guard = ChatTurnGuard()
    outcome = guard.guard_turn(
        turn,
        envelope=envelope,
        turn_id=args.turn_id or "",
        controls=_binding(args),
    )
    _emit(outcome.to_dict())
    if not outcome.allowed and outcome.undecidable:
        return EXIT_CANNOT_ASSESS
    return EXIT_OK if outcome.allowed else EXIT_REFUSED


def cmd_inbound(args: argparse.Namespace) -> int:
    """Re-validate one model answer against the grounding that was supplied."""
    envelope = _read_json(args.grounding) if args.grounding else None
    outcome = InboundValidator().validate(_read_json(args.output), envelope=_envelope(envelope))
    _emit(outcome.to_dict())
    return _exit_for(outcome.outcome)


def _envelope(document: Any):
    if document is None:
        return None
    from .envelope import GroundingEnvelope

    return GroundingEnvelope.from_mapping(document)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="chat", description="chat-turn guardrails (issue #507)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_control_flags(target: argparse.ArgumentParser) -> None:
        target.add_argument(
            "--controls",
            default="",
            help="path to a controls registry (default: the shipped guardrails/policy/controls.yaml)",
        )
        target.add_argument(
            "--enable",
            action="append",
            default=[],
            metavar="CONTROL",
            help="flip one bound control ON for this invocation (repeatable)",
        )

    controls = subparsers.add_parser("controls", help="show the bound controls registry")
    add_control_flags(controls)
    controls.set_defaults(handler=cmd_controls)

    retrieval = subparsers.add_parser("retrieval", help="scan retrieved fragments")
    retrieval.add_argument("--grounding", required=True)
    retrieval.set_defaults(handler=cmd_retrieval)

    guard_turn = subparsers.add_parser("guard-turn", help="guard one outbound turn")
    guard_turn.add_argument("--turn", required=True)
    guard_turn.add_argument("--grounding", default="")
    guard_turn.add_argument("--turn-id", default="")
    add_control_flags(guard_turn)
    guard_turn.set_defaults(handler=cmd_guard_turn)

    inbound = subparsers.add_parser("inbound", help="re-validate a model answer")
    inbound.add_argument("--output", required=True)
    inbound.add_argument("--grounding", default="")
    inbound.set_defaults(handler=cmd_inbound)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError, TypeError) as exc:
        return _cannot_assess(f"{type(exc).__name__}: {exc}", {"input": getattr(args, "command", "")})
