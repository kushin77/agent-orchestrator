"""C-suite instruction-layer derivation (issue #643, workbook-12).

The five C-suite personas are already declared twice on ``master``:

* workbook-1 **PersonaCards** — ``registry/personas/cards/{ceo,cto,coo,cfo,cmo}.yaml``
  (tier, budget cap, heartbeat, reporting line, constraint set, role guardrails);
* workbook-8 **prompt modules** — ``registry/prompts/modules/{role}-primary.v1.yaml``
  (``enforcement.policyId`` pointing at the workbook-6 mechanical rule).

Neither is an instruction-layer entry, so the five seats are absent from the
model-agnostic instruction layer: a harness reading ``AGENTS.md`` / ``CLAUDE.md``
/ ``.cursorrules`` / ``copilot-instructions.md`` has no C-suite instruction set
to render.  This module closes that gap by **DERIVING** — never hand-writing —
one canonical instruction source per seat from those two declarations.

Why derive rather than author: a hand-written second copy of the tier ladder,
the budget caps, the reporting lines or the constraint sets is exactly the drift
the instruction layer exists to prevent.  Here the canonical sources are a pure
function of the workbook artifacts; if a card or module changes, regenerate and
the mirrors change with it.  The registry and prompt-module lanes stay
read-only consumers from this lane's point of view (GR-3: one lane owns a file).

Derivation (deterministic — same inputs, same bytes):

1. five **governed** layers, ordered most-governing first:
   * ``platform`` — the org-chart edge (CEO roots at the board, every other seat
     reports to the CEO) + the session-identity binding;
   * ``csuite-role`` — the seat's tier, budget cap, heartbeat cadence and the
     versioned prompt module it renders from (workbook-1 + workbook-8);
   * ``workbook-mechanical`` — the seat's mechanical enforcement rule
     (workbook-5/6: policy id, action, gate attribute, decision);
   * ``repo-governance`` — the seat's ``constraintSet`` verbatim, in declaration
     order (workbook-1);
   * ``seat-expectations`` — the seat's role guardrails verbatim, in declaration
     order (workbook-1);
2. rule ids are namespaced by their source (``platform--`` / ``seat--`` /
   ``workbook--`` / ``constraint--`` / ``guardrail--``) so they are unique
   set-wide without inventing new vocabulary; the canonical contract admits only
   ``[a-z0-9-]``, so the separator is ``--``.

A produced canonical source must still validate against the ONE canonical
contract (``aoi.model.validate_canonical``) — this module adds no second scheme.
"""

from __future__ import annotations

import json
import os

import yaml

#: The five C-suite seats, in org-chart order (the CEO roots the org chart).
ROLES: tuple[str, ...] = ("ceo", "cto", "coo", "cfo", "cmo")

#: ``control-plane/instructions`` — the directory that holds the ``aoi`` package.
_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: Repository root (``control-plane/instructions`` -> ``control-plane`` -> root).
REPO_ROOT = os.path.dirname(os.path.dirname(_PKG_DIR))

#: Consumer (read-only) locations, relative to the repository root.
CARDS_REL = os.path.join("registry", "personas", "cards")
MODULES_REL = os.path.join("registry", "prompts", "modules")
ORG_CHART_REL = os.path.join("registry", "personas", "org-chart.yaml")

#: Version of the canonical source-format this deriver emits (same contract as
#: any other canonical source; the instruction layer has exactly one).
CANONICAL_VERSION = "1.0.0"


class DerivationError(ValueError):
    """Raised when a workbook artifact cannot be derived from (missing/mis-shaped)."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DerivationError(message)


def _load_yaml(path: str) -> dict:
    if not os.path.isfile(path):
        raise DerivationError(f"workbook artifact not found: {path}")
    try:
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:  # pragma: no cover - defensive
        raise DerivationError(f"cannot read workbook artifact {path}: {exc}") from exc
    _require(isinstance(data, dict), f"workbook artifact must be a mapping: {path}")
    return data


def card_path(role: str, repo_root: str = REPO_ROOT) -> str:
    """Path of the workbook-1 PersonaCard for ``role``."""
    return os.path.join(repo_root, CARDS_REL, f"{role}.yaml")


def module_path(role: str, repo_root: str = REPO_ROOT) -> str:
    """Path of the workbook-8 prompt module for ``role`` (``<role>-primary.v1.yaml``)."""
    module_version = "v1"
    return os.path.join(repo_root, MODULES_REL, f"{role}-primary.{module_version}.yaml")


def load_card(role: str, repo_root: str = REPO_ROOT) -> dict:
    """Load + shape-check one workbook-1 PersonaCard (read-only consumer)."""
    card = _load_yaml(card_path(role, repo_root))
    for field in ("id", "name", "systemPromptRef", "defaultModelTier",
                  "monthlyBudgetCapUsd", "heartbeatSchedule", "reportsTo"):
        _require(field in card, f"card {role}: missing field {field!r}")
    _require(card["id"] == role, f"card {role}: id {card['id']!r} != {role!r}")
    for field in ("constraintSet", "guardrails"):
        _require(isinstance(card.get(field), list) and card[field],
                 f"card {role}: {field} must be a non-empty list")
    return card


def load_module(role: str, repo_root: str = REPO_ROOT) -> dict:
    """Load + shape-check one workbook-8 prompt module (read-only consumer)."""
    module = _load_yaml(module_path(role, repo_root))
    enforcement = module.get("enforcement")
    _require(isinstance(enforcement, dict), f"module {role}: enforcement must be a mapping")
    for field in ("policyId", "action", "attribute", "rule"):
        _require(isinstance(enforcement.get(field), str) and enforcement[field],
                 f"module {role}: enforcement.{field} must be a non-empty string")
    return module


def load_org_chart(repo_root: str = REPO_ROOT) -> dict:
    """Load the org-chart declaration (the reporting edge is declared there too)."""
    return _load_yaml(os.path.join(repo_root, ORG_CHART_REL))


def prompt_module_version(card: dict) -> str:
    """The prompt-module version from ``systemPromptRef`` (``ceo/primary@v1`` -> ``v1``)."""
    ref = str(card.get("systemPromptRef", ""))
    _require("@" in ref, f"card {card.get('id')!r}: systemPromptRef must be <role>/<name>@<version>")
    return ref.rsplit("@", 1)[1]


def _money(value: object) -> str:
    """Format a USD cap deterministically (``300`` -> ``$300.00``)."""
    return f"${float(value):.2f}"


def derive_canonical(role: str, repo_root: str = REPO_ROOT) -> dict:
    """Derive the canonical instruction source for one C-suite seat.

    Pure function of the workbook artifacts on disk: the same checkout always
    yields the same document (byte-stable canonical YAML, byte-stable mirrors).
    """
    _require(role in ROLES, f"unknown C-suite role: {role!r} (expected one of {list(ROLES)})")
    card = load_card(role, repo_root)
    module = load_module(role, repo_root)
    enforcement = module["enforcement"]

    layer_tier = card["defaultModelTier"]
    layer_cap = _money(card["monthlyBudgetCapUsd"])
    layer_beat = card["heartbeatSchedule"]
    layer_reports_to = card["reportsTo"]
    layer_module = module.get("taskType", f"{role}-primary")
    layer_module_version = prompt_module_version(card)
    layer_policy = enforcement["policyId"]
    layer_action = enforcement["action"]
    layer_attribute = enforcement["attribute"]

    platform_rules: list[dict] = []
    if layer_reports_to == "board":
        platform_rules.append({
            "id": "platform--org-root",
            "text": (
                f"The {layer_tier} seat {role} is the single root of the agent org chart: it reports "
                "to the board (a principal, never an agent) and every other C-suite seat reports up "
                "to it. Board escalation, never board impersonation."
            ),
        })
        platform_rules.append({
            "id": "platform--goal-decomposition",
            "text": (
                "Goals are decomposed into board tickets before they are executed; every goal "
                "becomes an issue with a verification command, never ad-hoc work."
            ),
        })
    else:
        platform_rules.append({
            "id": "platform--reporting-line",
            "text": (
                f"The {role} seat reports to {layer_reports_to} along the declared org-chart edge "
                f"{role} -> {layer_reports_to}; it never acts outside that line and never claims "
                "another seat's lane."
            ),
        })
    platform_rules.append({
        "id": "platform--session-identity",
        "text": (
            "Work happens under a minted session identity bound to exactly one issue and one "
            "lane; the identity is never shared with, or inherited from, another session."
        ),
    })

    role_rules = [
        {
            "id": "seat--tier",
            "text": (
                f"The {role} seat runs at model tier {layer_tier} and renders from the versioned "
                f"prompt module {layer_module}@{layer_module_version}; the tier is raised only on "
                "observed difficulty and never lowered below the seat's floor."
            ),
        },
        {
            "id": "seat--budget-cap",
            "text": (
                f"The {role} seat has a monthly budget cap of {layer_cap}, enforced by the FinOps "
                "guardrail rather than by the seat; the cap is never silently raised, and an "
                "approaching cap raises an alert instead of being absorbed."
            ),
        },
        {
            "id": "seat--heartbeat",
            "text": (
                f"The {role} seat runs on the {layer_beat} heartbeat cadence and reports on every "
                "pass; a seat with unfinished work reports it rather than parking it."
            ),
        },
        {
            "id": "seat--prompt-module",
            "text": (
                f"The {role} seat's prompt module is the published declaration {layer_module}@"
                f"{layer_module_version}; the instruction layer derives from it and never forks a "
                "private copy of its text."
            ),
        },
    ]

    mechanical_rules = [
        {
            "id": "workbook--policy-id",
            "text": (
                f"The {role} seat's mechanical enforcement rule is the named policy id "
                f"{layer_policy} (workbook-6); the policy ships behind a default-OFF control and is "
                "activated only by a reviewed act."
            ),
        },
        {
            "id": "workbook--action",
            "text": (
                f"The {layer_policy} policy covers the action {layer_action} and decides on the "
                f"published attribute {layer_attribute}: a mechanical rule compares a value a "
                "producer publishes against a constant, never a reviewer's opinion."
            ),
        },
        {
            "id": "workbook--gate-attribute",
            "text": (
                f"The gate attribute {layer_attribute} is the only input to the decision; an absent "
                f"attribute is evaluated fail-closed rather than passing silently."
            ),
        },
    ]

    constraint_rules = [
        {
            "id": f"constraint--{name}",
            "text": {
                "issue-first": "Work is tracked in an issue before it is done; every change carries a reference to the issue it serves.",
                "stay-in-lane": "The seat makes the smallest focused change inside its own lane and touches no other lane's files.",
                "no-unrelated-edits": "No unrelated edits ride along with the change; the diff contains only what the issue requires.",
                "verify-before-done": "A task is done only when its verification gate is green and the actual output is reported; never an unverified claim.",
                "evidence-on-pr": "Every pull request carries its verification evidence, and the AI assistance and runtime are declared.",
                "no-direct-push": "Never push directly to a protected branch; changes land through a reviewable pull request with a green gate.",
                "no-secrets": "Credentials and tokens come from the environment or a secret manager; nothing is hardcoded, committed, or echoed to logs.",
                "no-unverified-merge": "No work is merged without green verification evidence first; failing work is never merged.",
                "no-adhoc-iac": "Infrastructure is declared as code and applied through the pipeline; never an ad-hoc apply and never a console click.",
                "no-debug-leftovers": "No unfinished markers, commented-out code blocks, or debug output are left behind in the change.",
            }.get(name, f"The {name} constraint declared by the {role} PersonaCard is honoured verbatim."),
        }
        for name in card["constraintSet"]
    ]

    expectation_rules = [
        {
            "id": f"guardrail--{index}",
            "text": str(text),
        }
        for index, text in enumerate(card["guardrails"], start=1)
    ]

    layers = [
        {
            "id": "platform",
            "label": "Platform governed layer (org-chart edge + session identity)",
            "managed": True,
            "rules": platform_rules,
        },
        {
            "id": "csuite-role",
            "label": f"C-suite seat layer ({role}: workbook-1 card + workbook-8 prompt module)",
            "managed": True,
            "rules": role_rules,
        },
        {
            "id": "workbook-mechanical",
            "label": f"Workbook mechanical enforcement layer ({layer_policy})",
            "managed": True,
            "rules": mechanical_rules,
        },
        {
            "id": "repo-governance",
            "label": f"Declared constraint set ({role} PersonaCard constraintSet)",
            "managed": True,
            "rules": constraint_rules,
        },
        {
            "id": "seat-expectations",
            "label": f"Declared seat expectations ({role} PersonaCard guardrails)",
            "managed": True,
            "rules": expectation_rules,
        },
    ]

    return {
        "schema": "ao.instructions.canonical/v1",
        "id": f"csuite-{role}",
        "version": CANONICAL_VERSION,
        "title": f"{card['name']} instruction set (C-suite seat {role})",
        "summary": (
            f"Canonical instruction source for the {card['name']} seat, derived from the workbook-1 "
            f"PersonaCard (tier {layer_tier}, {layer_cap} monthly cap, {layer_beat} heartbeat, "
            f"reports to {layer_reports_to}) and the workbook-8 prompt module {layer_module}@"
            f"{layer_module_version} with its workbook-6 mechanical rule {layer_policy}. Rendered "
            "deterministically into every per-tool mirror; never hand-forked."
        ),
        "layers": layers,
    }


def dump_canonical(canonical: dict) -> str:
    """Serialise a canonical source deterministically (stable YAML, no timestamps)."""
    header = (
        "# Canonical instruction source — C-suite seat (issue #643, workbook-12).\n"
        "#\n"
        "# DERIVED, never hand-written: this document is generated from the\n"
        "# workbook-1 PersonaCard (registry/personas/cards/) and the workbook-8\n"
        "# prompt module (registry/prompts/modules/) by csuite/derive.py.  Edit the\n"
        "# card or the module and regenerate; a hand edit here is drift.\n"
        "#\n"
        "#   python3 -m csuite.derive --role <role> --write\n"
        "#\n"
        "# Mirrors are generated from this file — never hand-forked.\n"
    )
    body = yaml.safe_dump(canonical, sort_keys=False, allow_unicode=True, width=1000)
    return header + body


def canonical_path(role: str, instructions_dir: str | None = None) -> str:
    """Path of the committed canonical source for ``role``."""
    base = instructions_dir or _PKG_DIR
    return os.path.join(base, "csuite", "canonical", f"{role}.yaml")


def rendered_dir(role: str, instructions_dir: str | None = None) -> str:
    """Path of the committed rendered mirror set for ``role``."""
    base = instructions_dir or _PKG_DIR
    return os.path.join(base, "csuite", "rendered", role)


def consumer_state_path(role: str, instructions_dir: str | None = None) -> str:
    """Path of the derived, pinned consumer state for ``role``."""
    base = instructions_dir or _PKG_DIR
    return os.path.join(base, "csuite", "consumer", f"{role}.json")


def write_all(repo_root: str = REPO_ROOT,
              instructions_dir: str | None = None) -> list[str]:
    """Derive + write every C-suite canonical source, mirror set, manifest state.

    Writes (deterministically, in ``ROLES`` order):

    * ``csuite/canonical/<role>.yaml``                 — the canonical source;
    * ``csuite/rendered/<role>/<4 mirrors>``           — the generated mirrors;
    * ``csuite/rendered/<role>/distribution-manifest.json`` — sha256 manifest;
    * ``csuite/consumer/<role>.json``                  — the pinned consumer state.

    Returns the list of absolute paths written.
    """
    from aoi.model import validate_canonical
    from aoi.render import MIRROR_TARGETS, distribution_manifest, render_all
    from aoi.versioning import CONSUMER_SCHEMA

    written: list[str] = []
    for role in ROLES:
        canonical = derive_canonical(role, repo_root)
        validate_canonical(canonical)

        path = canonical_path(role, instructions_dir)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(dump_canonical(canonical))
        written.append(path)

        files = render_all(canonical, None)
        out = rendered_dir(role, instructions_dir)
        os.makedirs(out, exist_ok=True)
        for name in MIRROR_TARGETS:
            mirror_path = os.path.join(out, name)
            with open(mirror_path, "w", encoding="utf-8") as handle:
                handle.write(files[name])
            written.append(mirror_path)

        manifest = distribution_manifest(canonical, None)
        manifest_path = os.path.join(out, "distribution-manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle, indent=2, sort_keys=True)
            handle.write("\n")
        written.append(manifest_path)

        state = {
            "schema": CONSUMER_SCHEMA,
            "consumer": f"kushin77/agent-orchestrator#643 ({role})",
            "canonical": manifest["canonical"],
            "mirrors": manifest["mirrors"],
        }
        state_path = consumer_state_path(role, instructions_dir)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
        written.append(state_path)
    return written


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="csuite-derive",
        description="derive C-suite instruction-layer canonical sources + mirrors (issue #643)",
    )
    parser.add_argument("--role", choices=ROLES, help="one role (default: all five)")
    parser.add_argument("--write", action="store_true",
                        help="write canonical sources, mirrors and consumer states")
    parser.add_argument("--repo-root", default=REPO_ROOT,
                        help="repository root holding registry/ (default: this checkout)")
    args = parser.parse_args(argv)

    if not args.write:
        roles = (args.role,) if args.role else ROLES
        for role in roles:
            canonical = derive_canonical(role, args.repo_root)
            print(dump_canonical(canonical))
        return 0
    written = write_all(args.repo_root)
    print(f"derive: wrote {len(written)} file(s) for {len(ROLES)} C-suite seats")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(_main())
