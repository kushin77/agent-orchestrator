"""Chat prompt modules — versioned, frozen and schema-declared (issue #509).

---knowledge---
module_id: registry.chat.prompt_modules
system: registry
app: chat
solution_class: enterprise
patterns: [versioned-module, frozen-published-version, content-digest, schema-constrained-output]
derives_from: registry/prompts/registry.py
owner_sme: platform-sme
tier: L1
interfaces: [ChatPromptRegistry, ResolvedChatModule, module_digest, validate_module, canonical_yaml, main]
invariants: "a published version is immutable; improving a chat prompt means publishing a NEW version"
gotchas: ""
related: ["#509"]
do_not_duplicate: null
---knowledge---

Same shape as the control-plane prompt library (``registry/prompts/``): a module
is a YAML definition naming its task type, version, prompt bodies and the JSON
Schema its output must satisfy. Publishing freezes a version with a content
digest and appends it to ``manifest.yaml``; a published version is immutable, so
improving a chat prompt means publishing a *new* version.

Chat adds one non-negotiable requirement. A chat module's output schema
**must** require the citations envelope (:mod:`registry.chat.envelope`):
:meth:`ChatPromptRegistry.check_module_contract` refuses a module whose schema
merely permits ``citations``, and the ``contract`` CLI fails by name. That is
what makes "grounded answers name their sources" a checked contract instead of a
promise in a README.

Usage (from the repo root):

    python3 -m registry.chat.prompt_modules status
    python3 -m registry.chat.prompt_modules resolve chat-answer
    python3 -m registry.chat.prompt_modules contract
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import jsonschema
import yaml

if __package__ in (None, ""):  # bare-script run: make the package importable
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from registry.chat import envelope
else:
    from . import envelope

PKG_DIR = Path(__file__).resolve().parent
MODULES_DIR = PKG_DIR / "modules"
MODULE_SCHEMA_PATH = PKG_DIR / "prompt-module.schema.json"
MANIFEST_PATH = PKG_DIR / "manifest.yaml"

#: The manifest's schema marker (mirrors the prompt library's convention).
MANIFEST_SCHEMA = "chat-prompt-manifest/v1"

#: The grounding policies a module may declare. ``answer-without-citations`` is
#: expressible — that is how a regressing candidate version is written — but no
#: published module may carry it: the fixture case that requires citations fails
#: by name and :mod:`registry.chat.regression` refuses the promotion.
GROUNDING_POLICIES = (
    "require-citations",
    "refuse-without-citations",
    "answer-without-citations",
)

_PLACEHOLDER_RE = re.compile(r"\{\{[^}]+\}\}")


class ChatPromptError(Exception):
    """Base error for the chat prompt registry."""


class UnknownTaskTypeError(ChatPromptError):
    """Raised when a taskType is not registered (a turn may not run ad hoc)."""


class VersionNotFoundError(ChatPromptError):
    """Raised when a specific ``(taskType, version)`` does not exist."""


class VersionAlreadyPublishedError(ChatPromptError):
    """Raised when a version is published twice — published versions are frozen."""


class IntegrityError(ChatPromptError):
    """Raised when a published module's content no longer matches its digest."""


class RenderError(ChatPromptError):
    """Raised when a frozen body cannot be rendered (unfilled placeholder)."""


def canonical_yaml(data: Mapping[str, Any]) -> str:
    """Deterministic serialization used for content digests."""
    return yaml.safe_dump(dict(data), sort_keys=True)


def module_digest(module: Mapping[str, Any]) -> str:
    """The content digest a published ``(taskType, version)`` is frozen at."""
    raw = canonical_yaml(module)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_module(
    module: Mapping[str, Any], schema_path: Optional[Path] = None
) -> None:
    """Validate a module definition against ``prompt-module.schema.json``."""
    path = Path(schema_path) if schema_path is not None else MODULE_SCHEMA_PATH
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ChatPromptError(f"cannot read module schema {path}: {exc}") from exc
    try:
        jsonschema.validate(instance=dict(module), schema=schema)
    except jsonschema.ValidationError as exc:
        raise ChatPromptError(f"module definition is invalid: {exc.message}") from exc


@dataclass(frozen=True)
class ResolvedChatModule:
    """A frozen chat prompt module, ready for a runtime call."""

    task_type: str
    version: str
    module: Mapping[str, Any]
    modules_dir: Path
    output_schema_path: Path
    bodies: Mapping[str, Path]

    @property
    def prompt_id(self) -> str:
        return f"{self.task_type}@{self.version}"

    @property
    def grounding_policy(self) -> str:
        return str(self.module["groundingPolicy"])

    @property
    def model_tier_hint(self) -> str:
        return str(self.module["modelTierHint"])

    def body_text(self, part: str) -> str:
        path = self.bodies.get(part)
        if path is None:
            return ""
        return path.read_text(encoding="utf-8")

    def output_schema(self) -> Dict[str, Any]:
        return json.loads(self.output_schema_path.read_text(encoding="utf-8"))


class ChatPromptRegistry:
    """File-backed registry of the chat prompt modules and their versions."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root is not None else PKG_DIR
        self.modules_dir = self.root / "modules"
        self.schema_path = self.root / "prompt-module.schema.json"
        self.manifest_path = self.root / "manifest.yaml"
        self._registered: Dict[Tuple[str, str], Mapping[str, Any]] = {}
        self._manifest = self._load_manifest()

    # ------------------------------------------------------------- manifest
    def _load_manifest(self) -> Dict[str, Any]:
        if not self.manifest_path.exists():
            return {"schema": MANIFEST_SCHEMA, "taskTypes": {}}
        data = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema") != MANIFEST_SCHEMA:
            raise ChatPromptError(f"manifest {self.manifest_path} is malformed")
        data.setdefault("taskTypes", {})
        return data

    def _save_manifest(self) -> None:
        self.manifest_path.write_text(
            yaml.safe_dump(self._manifest, sort_keys=True), encoding="utf-8"
        )

    def _published_versions(self, task_type: str) -> List[str]:
        entry = self._manifest["taskTypes"].get(task_type)
        if not entry:
            return []
        return [str(item["version"]) for item in entry.get("published", [])]

    def _published_digest(self, task_type: str, version: str) -> Optional[str]:
        entry = self._manifest["taskTypes"].get(task_type)
        if not entry:
            return None
        for item in entry.get("published", []):
            if item["version"] == version:
                return str(item["digest"])
        return None

    # ------------------------------------------------------------- catalogue
    def module_files(self) -> List[Path]:
        """Every module definition on disk, in deterministic filename order."""
        if not self.modules_dir.is_dir():
            return []
        return sorted(
            path for path in self.modules_dir.glob("*.yaml") if path.is_file()
        )

    def get(self, task_type: str, version: str) -> Optional[Dict[str, Any]]:
        """The module definition for an exact ``(taskType, version)``."""
        key = (task_type, version)
        if key in self._registered:
            return dict(self._registered[key])
        path = self.modules_dir / f"{task_type}.{version}.yaml"
        if not path.exists():
            return None
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None

    def register(
        self,
        module: Optional[Mapping[str, Any]] = None,
        *,
        path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Validate and register a module for later publishing."""
        if path is not None:
            module = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(module, Mapping):
            raise ChatPromptError("register requires a module mapping or a path")
        validate_module(module, self.schema_path)
        self._registered[(str(module["taskType"]), str(module["version"]))] = module
        return dict(module)

    # -------------------------------------------------------------- publish
    def publish(self, task_type: str, version: str) -> str:
        """Freeze and pin a version, making it resolvable.

        Publishing an already-published ``(taskType, version)`` is refused: a
        published version is immutable, so an improvement is a new version.
        """
        key = (task_type, version)
        module = self._registered.get(key)
        if module is None:
            module = self.get(task_type, version)
            if module is None:
                raise VersionNotFoundError(
                    f"no module definition for {task_type} {version}"
                )
        validate_module(module, self.schema_path)
        if version in self._published_versions(task_type):
            raise VersionAlreadyPublishedError(
                f"{task_type}@{version} is already published and immutable"
            )
        digest = module_digest(module)
        entry = self._manifest["taskTypes"].setdefault(
            task_type, {"pinned": None, "published": []}
        )
        entry["published"].append({"version": version, "digest": digest})
        entry["pinned"] = version
        self._save_manifest()
        return digest

    def pin(self, task_type: str, version: str) -> None:
        """Point runtime resolution at an already-published version (rollback)."""
        if version not in self._published_versions(task_type):
            raise VersionNotFoundError(
                f"cannot pin {task_type}@{version}: not a published version"
            )
        entry = self._manifest["taskTypes"].setdefault(task_type, {"published": []})
        entry["pinned"] = version
        self._save_manifest()

    # -------------------------------------------------------------- resolve
    def resolve(self, task_type: str, version: Optional[str] = None) -> ResolvedChatModule:
        """Resolve a module, verifying its digest and every referenced file.

        With no ``version`` the pinned published version is resolved; with an
        explicit ``version`` that version is resolved (which is how the
        regression gate evaluates a candidate that is not pinned yet).
        """
        entry = self._manifest["taskTypes"].get(task_type)
        if version is None:
            if entry is None:
                raise UnknownTaskTypeError(
                    f"unregistered taskType {task_type!r}: every chat turn resolves "
                    "a registered, published chat prompt module"
                )
            pinned = entry.get("pinned")
            if pinned is None:
                raise ChatPromptError(f"taskType {task_type!r} has no published version")
            version = str(pinned)
            frozen = self._published_digest(task_type, version)
            if frozen is None:
                raise ChatPromptError(
                    f"taskType {task_type!r} is pinned to unpublished version {version!r}"
                )
        else:
            frozen = self._published_digest(task_type, version)

        module = self.get(task_type, version)
        if module is None:
            raise VersionNotFoundError(
                f"no module definition for {task_type} {version}"
            )
        validate_module(module, self.schema_path)
        if frozen is not None and module_digest(module) != frozen:
            raise IntegrityError(
                f"{task_type}@{version} content changed after publish (digest "
                "mismatch): a published version is immutable, publish a new one"
            )
        bodies: Dict[str, Path] = {}
        for part, ref in dict(module.get("bodyRefs", {})).items():
            bodies[str(part)] = (self.modules_dir / str(ref)).resolve()
        resolved = ResolvedChatModule(
            task_type=task_type,
            version=str(version),
            module=module,
            modules_dir=self.modules_dir,
            output_schema_path=(self.modules_dir / str(module["outputSchema"])).resolve(),
            bodies=bodies,
        )
        self._check_refs(resolved)
        return resolved

    def _check_refs(self, resolved: ResolvedChatModule) -> None:
        for part, path in resolved.bodies.items():
            if not path.is_file():
                raise ChatPromptError(
                    f"{resolved.prompt_id} bodyRef {part!r} not found: {path}"
                )
        if not resolved.output_schema_path.is_file():
            raise ChatPromptError(
                f"{resolved.prompt_id} outputSchema not found: "
                f"{resolved.output_schema_path}"
            )
        try:
            resolved.output_schema()
        except json.JSONDecodeError as exc:
            raise ChatPromptError(
                f"{resolved.prompt_id} outputSchema is not valid JSON: {exc}"
            ) from exc

    # --------------------------------------------------------------- render
    def render_prompt(
        self, task_type: str, variables: Optional[Mapping[str, Any]] = None
    ) -> Dict[str, Any]:
        """Render the frozen bodies, refusing a leftover ``{{...}}`` placeholder."""
        resolved = self.resolve(task_type)
        values = {str(key): str(value) for key, value in dict(variables or {}).items()}
        rendered: Dict[str, str] = {}
        for part in resolved.bodies:
            text = resolved.body_text(part)
            for key, value in values.items():
                text = text.replace("{{" + key + "}}", value)
            leftover = sorted(set(_PLACEHOLDER_RE.findall(text)))
            if leftover:
                raise RenderError(
                    f"{resolved.prompt_id} body {part!r} has unfilled placeholders: "
                    f"{leftover}"
                )
            rendered[part] = text
        return {
            "promptId": resolved.prompt_id,
            "taskType": resolved.task_type,
            "version": resolved.version,
            "modelTierHint": resolved.model_tier_hint,
            "groundingPolicy": resolved.grounding_policy,
            "outputSchema": str(resolved.output_schema_path),
            "bodies": rendered,
        }

    # ------------------------------------------------------------- contract
    def check_module_contract(self, resolved: ResolvedChatModule) -> List[str]:
        """Violations of the chat module contract — empty means compliant.

        The contract is what keeps a grounded answer honest: the module's output
        schema must *require* the citations envelope, a citing policy must
        demand at least one citation, and a module that would answer without
        citations is not promotable at all.
        """
        violations: List[str] = []
        schema = resolved.output_schema()
        if not envelope.schema_requires_envelope(schema):
            violations.append(
                f"{resolved.prompt_id}: output schema does not require the "
                "citations envelope (an uncited answer would validate)"
            )
        policy = resolved.grounding_policy
        if policy == "answer-without-citations":
            violations.append(
                f"{resolved.prompt_id}: groundingPolicy 'answer-without-citations' "
                "is not promotable — it regresses the grounded fixture case"
            )
        if policy == "require-citations" and envelope.citation_floor(schema) < 1:
            violations.append(
                f"{resolved.prompt_id}: groundingPolicy requires citations but the "
                "schema's citation floor is 0"
            )
        return violations

    def contract_report(self) -> Dict[str, List[str]]:
        """The contract verdict for every module file on disk, by filename."""
        report: Dict[str, List[str]] = {}
        for path in self.module_files():
            try:
                module = yaml.safe_load(path.read_text(encoding="utf-8"))
                validate_module(module, self.schema_path)
                resolved = self.resolve(
                    str(module["taskType"]), str(module["version"])
                )
                report[path.name] = self.check_module_contract(resolved)
            except ChatPromptError as exc:
                report[path.name] = [f"{path.name}: {exc}"]
        return report


def _print_resolved(resolved: ResolvedChatModule) -> None:
    print(f"promptId:        {resolved.prompt_id}")
    print(f"description:     {resolved.module.get('description', '').strip()}")
    print(f"modelTierHint:   {resolved.model_tier_hint}")
    print(f"groundingPolicy: {resolved.grounding_policy}")
    print(f"outputSchema:    {resolved.output_schema_path.relative_to(resolved.modules_dir.parent)}")
    for part, path in resolved.bodies.items():
        print(f"bodyRef[{part}]:    {path.relative_to(resolved.modules_dir.parent)}")


def _dispatch(registry: ChatPromptRegistry, args: argparse.Namespace) -> int:
    if args.command == "status":
        for task_type in sorted(registry._manifest["taskTypes"]):
            entry = registry._manifest["taskTypes"][task_type]
            published = ",".join(
                str(item["version"]) for item in entry.get("published", [])
            )
            print(f"{task_type}  pinned={entry.get('pinned')}  published={published}")
        for name, violations in sorted(registry.contract_report().items()):
            state = "OK" if not violations else "FAIL"
            print(f"  {state}    {name}")
        return 0
    if args.command == "resolve":
        resolved = registry.resolve(args.taskType)
        _print_resolved(resolved)
        return 0
    if args.command == "render":
        variables: Dict[str, str] = {}
        for item in args.var:
            key, _, value = item.partition("=")
            variables[key] = value
        rendered = registry.render_prompt(args.taskType, variables)
        print(f"promptId:     {rendered['promptId']}")
        print(f"outputSchema: {rendered['outputSchema']}")
        for part, text in rendered["bodies"].items():
            print(f"--- {part} ---")
            print(text)
        return 0
    if args.command == "publish":
        digest = registry.publish(args.taskType, args.version)
        print(f"published {args.taskType}@{args.version} (digest {digest})")
        return 0
    if args.command == "pin":
        registry.pin(args.taskType, args.version)
        print(f"pinned {args.taskType} -> {args.version}")
        return 0
    if args.command == "contract":
        failures = 0
        for name, violations in sorted(registry.contract_report().items()):
            if violations:
                failures += len(violations)
                print(f"FAIL  {name}")
                for violation in violations:
                    print(f"VIOLATION {violation}")
            else:
                print(f"  OK    {name} requires the citations envelope")
        if failures:
            print(f"contract: FAIL ({failures} violation(s))")
            return 1
        print("contract: PASS (every module's schema requires the citations envelope)")
        return 0
    return 2


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chat-prompt-modules",
        description="Versioned chat prompt modules + the citations-envelope contract",
    )
    parser.add_argument("--root", default=None, help="package root override")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="published versions and per-module contract verdict")
    p_resolve = sub.add_parser("resolve", help="resolve the pinned version of a taskType")
    p_resolve.add_argument("taskType")
    p_render = sub.add_parser("render", help="render the frozen bodies of a taskType")
    p_render.add_argument("taskType")
    p_render.add_argument("--var", action="append", default=[], metavar="k=v")
    p_publish = sub.add_parser("publish", help="publish (freeze + pin) a version")
    p_publish.add_argument("taskType")
    p_publish.add_argument("version")
    p_pin = sub.add_parser("pin", help="re-pin resolution to a published version")
    p_pin.add_argument("taskType")
    p_pin.add_argument("version")
    sub.add_parser("contract", help="assert every module's schema requires the envelope")
    args = parser.parse_args(argv)
    registry = ChatPromptRegistry(args.root)
    try:
        return _dispatch(registry, args)
    except ChatPromptError as exc:
        print(f"chat-prompt-modules: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
