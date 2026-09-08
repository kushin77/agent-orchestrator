#!/usr/bin/env python3
"""Versioned prompt/instruction library - registry API.

The governance primitive for every model call in the control plane: a caller
never builds an ad-hoc unversioned prompt at runtime. Instead it asks the
registry to resolve the pinned published version of a taskType, which yields a
frozen, schema-constrained prompt (adapted from the gmail-agent
``src/agent/prompts/<task>/v1.ts`` versioned-prompt + OutputSchema convention).

Design (see README.md for the full contract):

- PromptModule definitions live as YAML files under ``modules/`` and validate
  against ``prompt-module.schema.json``.
- A version becomes visible to runtime only when it is **published**, which
  writes it to ``manifest.yaml`` along with a content digest (sha256 of the
  canonical YAML). Publishing freezes the version: the same (taskType,
  version) can never be published twice, and any edit to a published module is
  detected as an integrity violation on the next load.
- ``resolve(taskType)`` returns the pinned published version and **refuses
  unregistered taskTypes** (governance: no unversioned ad-hoc prompts).
- ``render_prompt`` substitutes template variables into the frozen bodies and
  rejects any leftover ``{{...}}`` placeholders.

Usage (from the repo root):

    python3 registry/prompts/registry.py status
    python3 registry/prompts/registry.py resolve classify-route
    python3 registry/prompts/registry.py render classify-route --var input="..."
    python3 registry/prompts/registry.py governance seed/call-plan.yaml
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import jsonschema  # type: ignore
    import yaml  # type: ignore
except ImportError as exc:  # pragma: no cover
    sys.exit(f"registry: missing dependency ({exc}); need jsonschema + PyYAML")

PKG_DIR = Path(__file__).resolve().parent
MODULES_DIR = PKG_DIR / "modules"
MANIFEST_PATH = PKG_DIR / "manifest.yaml"
SCHEMA_PATH = PKG_DIR / "prompt-module.schema.json"

_MANIFEST_SCHEMA = "prompt-manifest/v1"
_PLACEHOLDER_RE = re.compile(r"\{\{([A-Za-z0-9_.-]+)\}\}")


def _load_schema() -> Dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_with_schema(module: Dict[str, Any]) -> None:
    """Validate a module dict against prompt-module.schema.json.

    Raises PromptModuleError with the first schema violation message.
    """
    try:
        jsonschema.validate(instance=module, schema=_load_schema())
    except jsonschema.ValidationError as exc:
        raise PromptModuleError(
            f"module invalid against schema: {exc.message}"
        ) from exc


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_yaml(data: Dict[str, Any]) -> str:
    """Deterministic serialization used for content digests."""
    return yaml.safe_dump(data, sort_keys=True)


def _module_file(task_type: str, version: str) -> Path:
    return MODULES_DIR / f"{task_type}.{version}.yaml"


class PromptModuleError(Exception):
    """Base error for the prompt registry."""


class UnknownTaskTypeError(PromptModuleError):
    """Raised when a taskType is not registered/published (governance)."""


class VersionNotFoundError(PromptModuleError):
    """Raised when a specific (taskType, version) does not exist."""


class VersionAlreadyPublishedError(PromptModuleError):
    """Raised when a version is published twice - published versions are immutable."""


class IntegrityError(PromptModuleError):
    """Raised when a published module's content no longer matches its frozen digest."""


class RenderError(PromptModuleError):
    """Raised when a frozen prompt cannot be rendered (leftover placeholders)."""


class ResolvedModule:
    """A frozen, pinned prompt module ready for a runtime model call."""

    def __init__(self, task_type: str, module: Dict[str, Any]) -> None:
        self.task_type = task_type
        self.module = module
        self.version = module["version"]
        self.output_schema = module["outputSchema"]
        self.model_tier_hint = module["modelTierHint"]
        self.bodies: Dict[str, Path] = {}
        for part, ref in module.get("bodyRefs", {}).items():
            self.bodies[part] = (MODULES_DIR / ref).resolve()

    def body_text(self, part: str) -> str:
        try:
            return self.bodies[part].read_text(encoding="utf-8")
        except KeyError:
            return ""

    @property
    def prompt_id(self) -> str:
        return f"{self.task_type}@{self.version}"


class PromptRegistry:
    """File-backed registry of prompt modules and their published versions."""

    def __init__(self, root: Optional[Path] = None) -> None:
        if root is None:
            root = PKG_DIR
        self.root = Path(root)
        self.modules_dir = self.root / "modules"
        self.manifest_path = self.root / "manifest.yaml"
        self._registered: Dict[tuple, Dict[str, Any]] = {}
        self._manifest = self._load_manifest()

    # ------------------------------------------------------------------ state
    def _load_manifest(self) -> Dict[str, Any]:
        if not self.manifest_path.exists():
            return {"schema": _MANIFEST_SCHEMA, "taskTypes": {}}
        data = yaml.safe_load(self.manifest_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("schema"):
            raise PromptModuleError(f"manifest {self.manifest_path} is malformed")
        data.setdefault("taskTypes", {})
        return data

    def _save_manifest(self) -> None:
        text = yaml.safe_dump(self._manifest, sort_keys=True)
        self.manifest_path.write_text(text, encoding="utf-8")

    def _published_versions(self, task_type: str) -> List[str]:
        ts = self._manifest["taskTypes"].get(task_type)
        if not ts:
            return []
        return [e["version"] for e in ts.get("published", [])]

    def _published_digest(self, task_type: str, version: str) -> Optional[str]:
        ts = self._manifest["taskTypes"].get(task_type)
        if not ts:
            return None
        for entry in ts.get("published", []):
            if entry["version"] == version:
                return entry["digest"]
        return None

    def _validate(self, module: Dict[str, Any]) -> None:
        validate_with_schema(module)

    @staticmethod
    def _digest(module: Dict[str, Any]) -> str:
        raw = _canonical_yaml(module)
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # --------------------------------------------------------------- register
    def register(
        self,
        task_type: Optional[str] = None,
        module: Optional[Dict[str, Any]] = None,
        *,
        path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        """Validate and register a PromptModule for later publishing.

        Registration makes a module known to this registry instance; it does
        not publish it. ``module`` may be a dict or a path to a module YAML
        file. The taskType/version on the module itself are authoritative.
        """
        if path is not None:
            module = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(module, dict):
            raise PromptModuleError("register requires a module dict or path")
        self._validate(module)
        key = (module["taskType"], module["version"])
        if task_type is not None and task_type != module["taskType"]:
            raise PromptModuleError("task_type argument conflicts with module taskType")
        self._registered[key] = module
        return module

    # ---------------------------------------------------------------- publish
    def publish(self, task_type: str, version: str) -> str:
        """Freeze and pin a prompt version, making it resolvable at runtime.

        Raises VersionAlreadyPublishedError if (taskType, version) is already
        published - a published version is immutable.
        """
        key = (task_type, version)
        module = self._registered.get(key)
        if module is None:
            path = self.modules_dir / f"{task_type}.{version}.yaml"
            if not path.exists():
                raise VersionNotFoundError(
                    f"no module definition for {task_type} {version} "
                    f"(registered or at {path.name})"
                )
            module = yaml.safe_load(path.read_text(encoding="utf-8"))
            self._validate(module)
        if version in self._published_versions(task_type):
            raise VersionAlreadyPublishedError(
                f"{task_type}@{version} is already published and immutable"
            )
        digest = self._digest(module)
        ts = self._manifest["taskTypes"].setdefault(
            task_type, {"pinned": None, "published": []}
        )
        ts["published"].append(
            {"version": version, "digest": digest, "publishedAt": _utc_now()}
        )
        ts["pinned"] = version
        self._save_manifest()
        return digest

    def pin(self, task_type: str, version: str) -> None:
        """Point runtime resolution at an already-published version (rollback)."""
        if version not in self._published_versions(task_type):
            raise VersionNotFoundError(
                f"cannot pin {task_type}@{version}: not a published version"
            )
        self._manifest["taskTypes"].setdefault(task_type, {"published": []})
        self._manifest["taskTypes"][task_type]["pinned"] = version
        self._save_manifest()

    # --------------------------------------------------------------------- get
    def get(self, task_type: str, version: str) -> Optional[Dict[str, Any]]:
        """Return the module definition for an exact (taskType, version)."""
        key = (task_type, version)
        if key in self._registered:
            return self._registered[key]
        path = self.modules_dir / f"{task_type}.{version}.yaml"
        if path.exists():
            return yaml.safe_load(path.read_text(encoding="utf-8"))
        return None

    # ----------------------------------------------------------------- resolve
    def resolve(self, task_type: str) -> ResolvedModule:
        """Resolve the pinned published version of a taskType.

        Raises UnknownTaskTypeError when the taskType is not registered and
        published (governance rule: no unversioned ad-hoc prompts at runtime).
        Verifies content integrity and that every referenced file exists.
        """
        ts = self._manifest["taskTypes"].get(task_type)
        if ts is None:
            raise UnknownTaskTypeError(
                f"unregistered taskType '{task_type}': every model call must "
                "reference a registered, published prompt module"
            )
        pinned = ts.get("pinned")
        if pinned is None or not ts.get("published"):
            raise PromptModuleError(f"taskType '{task_type}' has no published version")
        frozen_digest = self._published_digest(task_type, pinned)
        if frozen_digest is None:
            raise PromptModuleError(
                f"taskType '{task_type}' pinned to unpublished version '{pinned}'"
            )
        module = self.get(task_type, pinned)
        if module is None:
            raise PromptModuleError(
                f"module definition missing for {task_type}@{pinned}"
            )
        self._validate(module)
        if self._digest(module) != frozen_digest:
            raise IntegrityError(
                f"{task_type}@{pinned} content changed after publish "
                f"(digest mismatch): edit is forbidden, publish a new version"
            )
        resolved = ResolvedModule(task_type, module)
        self._check_refs(resolved)
        return resolved

    def _check_refs(self, resolved: ResolvedModule) -> None:
        for part, path in resolved.bodies.items():
            if not path.is_file():
                raise PromptModuleError(
                    f"{resolved.prompt_id} bodyRef '{part}' not found: {path}"
                )
        schema = (MODULES_DIR / resolved.output_schema).resolve()
        if not schema.is_file():
            raise PromptModuleError(
                f"{resolved.prompt_id} outputSchema not found: {schema}"
            )
        try:
            json.loads(schema.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PromptModuleError(
                f"{resolved.prompt_id} outputSchema is not valid JSON: {exc}"
            ) from exc

    # ------------------------------------------------------------------ render
    def render_prompt(
        self, task_type: str, variables: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Render the frozen prompt for a taskType with template variables.

        Refuses unregistered taskTypes (via resolve) and refuses to emit a
        prompt with leftover ``{{...}}`` placeholders.
        """
        resolved = self.resolve(task_type)
        variables = variables or {}
        rendered: Dict[str, str] = {}
        for part in resolved.bodies:
            text = self._substitute(resolved.body_text(part), variables)
            leftover = _PLACEHOLDER_RE.findall(text)
            if leftover:
                raise RenderError(
                    f"{resolved.prompt_id} body '{part}' has unfilled "
                    f"placeholders: {sorted(set(leftover))}"
                )
            rendered[part] = text
        return {
            "promptId": resolved.prompt_id,
            "taskType": task_type,
            "version": resolved.version,
            "modelTierHint": resolved.model_tier_hint,
            "parameters": resolved.module.get("parameters", {}),
            "outputSchema": str((MODULES_DIR / resolved.output_schema).resolve()),
            "bodies": rendered,
        }

    @staticmethod
    def _substitute(text: str, variables: Dict[str, Any]) -> str:
        for key, value in variables.items():
            text = text.replace("{{" + key + "}}", str(value))
        return text

    # -------------------------------------------------------------- governance
    def governance_check(self, call_plan: Any) -> List[str]:
        """Check a call plan against the governance rule.

        Accepts a list of taskType strings or a dict shaped like
        ``{"calls": [{"taskType": ...}, ...]}``. Every referenced taskType must
        resolve to a published, pinned prompt module. Returns a list of
        violations (empty means compliant).
        """
        if isinstance(call_plan, dict) and "calls" in call_plan:
            tasks = [
                c["taskType"]
                for c in call_plan["calls"]
                if isinstance(c, dict) and c.get("taskType")
            ]
        elif isinstance(call_plan, list):
            tasks = [str(t) for t in call_plan if t]
        else:
            raise PromptModuleError("call plan must be a list or {'calls': [...]}")
        errors: List[str] = []
        for task_type in tasks:
            try:
                resolved = self.resolve(task_type)
                if not resolved.output_schema:
                    errors.append(f"{task_type}: resolved without an output schema")
            except (UnknownTaskTypeError, PromptModuleError) as exc:
                errors.append(str(exc))
        return errors


def _print_module(resolved: ResolvedModule) -> None:
    module = resolved.module
    print(f"taskType:        {resolved.task_type}")
    print(f"version:         {resolved.version}")
    print(f"description:     {module.get('description', '')}")
    print(f"modelTierHint:   {resolved.model_tier_hint}")
    print(f"outputSchema:    {resolved.output_schema}")
    for part, path in resolved.bodies.items():
        print(f"bodyRef[{part}]:    {path.relative_to(PKG_DIR)}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prompt-registry", description="Versioned prompt/instruction library CLI"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="show published versions per taskType with integrity")

    p_resolve = sub.add_parser("resolve", help="resolve the pinned version of a taskType")
    p_resolve.add_argument("taskType")

    p_render = sub.add_parser("render", help="render the frozen prompt for a taskType")
    p_render.add_argument("taskType")
    p_render.add_argument("--var", action="append", default=[], metavar="k=v")

    p_publish = sub.add_parser("publish", help="publish (freeze+pin) a module version")
    p_publish.add_argument("taskType")
    p_publish.add_argument("version")

    p_pin = sub.add_parser("pin", help="re-pin runtime resolution to a published version")
    p_pin.add_argument("taskType")
    p_pin.add_argument("version")

    p_gov = sub.add_parser("governance", help="check a call plan for unversioned prompts")
    p_gov.add_argument("plan")

    args = parser.parse_args(argv)
    registry = PromptRegistry()
    try:
        return _dispatch(registry, args)
    except PromptModuleError as exc:
        print(f"prompt-registry: {exc}", file=sys.stderr)
        return 1


def _dispatch(registry: PromptRegistry, args: argparse.Namespace) -> int:
    if args.command == "status":
        for task_type in sorted(registry._manifest["taskTypes"]):
            ts = registry._manifest["taskTypes"][task_type]
            print(f"{task_type}  pinned={ts.get('pinned')}  published="
                  f"{','.join(e['version'] for e in ts.get('published', []))}")
        return 0
    if args.command == "resolve":
        _print_module(registry.resolve(args.taskType))
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
    if args.command == "governance":
        plan = yaml.safe_load(Path(args.plan).read_text(encoding="utf-8"))
        errors = registry.governance_check(plan)
        if errors:
            for error in errors:
                print(f"VIOLATION: {error}")
            print(f"governance: FAIL ({len(errors)} violation(s))")
            return 1
        print("governance: PASS (every referenced taskType is a published prompt module)")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
