#!/usr/bin/env python3
"""CTO overlay engine — four-layer per-repo governance with a truthful verdict.

Issued as kushin77/agent-orchestrator#147 (parent #144). The overlay is a
drop-in governance layer: a repository carries `governance/cto-overlay/`
(this directory) and inherits four layers — executive, engineering, devops,
support — each declaring its checks and whether it blocks, plus four
non-negotiable signals that always run.

EXIT-CODE CONTRACT (repo tri-state convention): 0 OK / 1 NOT-OK / 2 CANNOT-ASSESS.

  * 0 — every blocking check and every non-negotiable signal passed. Warnings
    (and advisory-grade results) are reported but do not block.
  * 1 — the assessment completed and something definite failed: a blocking
    check, or a non-negotiable signal.
  * 2 — the assessment is incomplete: the config is missing, unparseable or
    schema-invalid; it names a check or signal the engine does not implement;
    it contradicts itself; or a blocking check / signal could not be run
    (INDETERMINATE). A definite failure outranks an incomplete assessment
    (rc 1 beats rc 2), because in that case the repository is known bad.

NO-FALSE-GREEN RULES, carried over from the upstream fix in
kushin77/leaderboard#1721 ("the CTO overlay detected failures and passed
anyway"), where ten checks were declared blocking and none could block:

  1. A verdict is RECORDED, not printed. Every check reports through one
     tally; nothing else can affect the exit code, so a future check cannot
     quietly become unable to fail.
  2. INDETERMINATE is a state. "The tool is not installed" is not a clean run
     and "the file was not scanned" is not a pass; at blocking severity an
     unrun check decides the exit code, and it is never 0.
  3. The severity a layer carries is read from the config — both the layer's
     `blocking` flag and the `tiers.<tier>.<layer>` override, which must agree
     at the default tier or the engine refuses to run.
  4. The tally counts every declared layer exactly once, including layers that
     were disabled or not selected, and refuses a duplicate record. A layer
     that cannot be found in the tally is a bug, not a skip.

PORTED SHAPE, RE-IMPLEMENTED IN PYTHON: this engine follows the mature
bash/GitHub-Actions overlay of kushin77/leaderboard (`.cto/` + `scripts/cto/`)
— same layer ids, same tier names, same BLOCKING/WARNING tier table, same
non-negotiable signal names — but re-implements the mechanics here because
this repository has no GitHub Actions (GR-15) and runs gates from Python.
Provenance table: docs/CTO-OVERLAY.md.

Usage:
  python3 governance/cto-overlay/overlay.py run [--tier standard] [--root DIR]
      [--layers executive,engineering] [--diff-base REF] [--format text|json]
  python3 governance/cto-overlay/overlay.py validate [--root DIR]
  python3 governance/cto-overlay/overlay.py list-layers [--tier standard]
  python3 governance/cto-overlay/overlay.py self-test
  python3 governance/cto-overlay/overlay.py apply --target DIR [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - exercised only on a python without PyYAML
    yaml = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_NOT_OK = 1
EXIT_CANNOT_ASSESS = 2

LAYERS: Tuple[str, ...] = ("executive", "engineering", "devops", "support")
TIERS: Tuple[str, ...] = ("experimental", "standard", "critical")
TIER_RANK = {name: rank for rank, name in enumerate(TIERS)}
TIER_ALIASES = {"T1": "experimental", "T2": "standard", "T3": "critical"}
SEVERITIES: Tuple[str, ...] = ("blocking", "warning", "advisory")

STATE_PASS = "PASS"
STATE_FAIL = "FAIL"
STATE_INDET = "INDET"
STATE_SKIP = "SKIP"
STATES = (STATE_PASS, STATE_FAIL, STATE_INDET, STATE_SKIP)

SIGNAL_SCOPE = "non-negotiable"
COMMAND_TIMEOUT_SECONDS = 60

OVERLAY_DIR = Path(__file__).resolve().parent
REPO_ROOT_DEFAULT = OVERLAY_DIR.parents[1]
CONFIG_NAME = "config.yaml"
SCHEMA_NAME = "schema.yaml"
ARTIFACTS = (SCHEMA_NAME, CONFIG_NAME, "overlay.py", "README.md")

Result = Tuple[str, str]


class ConfigError(Exception):
    """The config cannot be trusted: missing, unparseable, invalid, unknown."""


class Indeterminate(Exception):
    """A check could not be run. Never a pass; decided by severity."""


class EngineError(Exception):
    """The engine itself cannot produce a verdict (unknown state, bad wiring)."""


# --------------------------------------------------------------------------
# JSON Schema validation (draft 2020-12 subset, fail-closed)
# --------------------------------------------------------------------------
#
# Python's standard library has no validator and this repo allows stdlib +
# PyYAML only, so the subset the overlay's schema actually uses is implemented
# here. It is deliberately FAIL-CLOSED: a schema keyword this validator does
# not implement raises instead of being skipped, so the schema can never
# silently widen (the alternative — ignoring an unknown keyword — is exactly
# how a check becomes decoration).

SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "title",
        "description",
        "type",
        "required",
        "additionalProperties",
        "properties",
        "items",
        "enum",
        "const",
        "pattern",
        "minLength",
        "maxLength",
        "minItems",
        "uniqueItems",
        "minProperties",
        "minimum",
        "maximum",
        "allOf",
        "contains",
        "$defs",
        "$ref",
    }
)

JSON_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _json_type_of(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if value is None:
        return "null"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _type_matches(value: object, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected in JSON_TYPES:
        return isinstance(value, JSON_TYPES[expected])
    if expected == "any":
        return True
    raise EngineError(f"schema uses unsupported type {expected!r}")


class SchemaValidator:
    """Validate an instance against the keyword subset listed above."""

    def __init__(self, schema: dict) -> None:
        self.root = schema
        self._assert_keywords(schema, "#")

    def _assert_keywords(self, node: object, path: str) -> None:
        if isinstance(node, dict):
            for keyword, value in node.items():
                if keyword not in SUPPORTED_KEYWORDS:
                    raise EngineError(
                        f"schema at {path} uses keyword {keyword!r}, which this "
                        "engine does not implement — refusing to validate "
                        "rather than ignore a constraint"
                    )
                if keyword in ("properties", "$defs"):
                    for name, sub in (value or {}).items():
                        self._assert_keywords(sub, f"{path}/{name}")
                elif keyword in ("items", "additionalProperties", "contains"):
                    self._assert_keywords(value, f"{path}/{keyword}")
                elif keyword == "allOf":
                    for index, sub in enumerate(value or []):
                        self._assert_keywords(sub, f"{path}/allOf/{index}")
        elif isinstance(node, bool):
            return

    def validate(self, instance: object) -> None:
        self._validate(instance, self.root, "$")

    def _resolve(self, ref: str, path: str) -> dict:
        if not ref.startswith("#/"):
            raise EngineError(f"schema $ref at {path} must be a local ref, got {ref!r}")
        node: object = self.root
        for part in ref[2:].split("/"):
            if not isinstance(node, dict) or part not in node:
                raise EngineError(f"schema $ref at {path} does not resolve: {ref!r}")
            node = node[part]
        if not isinstance(node, dict):
            raise EngineError(f"schema $ref at {path} resolves to a non-object: {ref!r}")
        return node

    def _validate(self, value: object, schema: dict, path: str) -> None:
        if not isinstance(schema, dict):
            raise EngineError(f"schema node at {path} is not an object")

        if "$ref" in schema:
            self._validate(value, self._resolve(schema["$ref"], path), path)

        if "type" in schema:
            expected = schema["type"]
            allowed = expected if isinstance(expected, list) else [expected]
            if not any(_type_matches(value, item) for item in allowed):
                raise ConfigError(
                    f"{path}: expected type {'/'.join(allowed)}, got {_json_type_of(value)}"
                )

        if "const" in schema and value != schema["const"]:
            raise ConfigError(f"{path}: expected {schema['const']!r}, got {value!r}")

        if "enum" in schema and value not in schema["enum"]:
            raise ConfigError(
                f"{path}: {value!r} is not one of {', '.join(map(repr, schema['enum']))}"
            )

        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise ConfigError(f"{path}: shorter than {schema['minLength']} characters")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise ConfigError(f"{path}: longer than {schema['maxLength']} characters")
            if "pattern" in schema:
                try:
                    matched = re.search(schema["pattern"], value)
                except re.error as exc:
                    raise EngineError(f"schema pattern at {path} is invalid: {exc}") from exc
                if not matched:
                    raise ConfigError(f"{path}: {value!r} does not match {schema['pattern']!r}")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                raise ConfigError(f"{path}: {value} is below the minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                raise ConfigError(f"{path}: {value} is above the maximum {schema['maximum']}")

        if isinstance(value, dict):
            for key in schema.get("required", []):
                if key not in value:
                    raise ConfigError(f"{path}: required key {key!r} is missing")
            if "minProperties" in schema and len(value) < schema["minProperties"]:
                raise ConfigError(f"{path}: fewer than {schema['minProperties']} propert(ies)")
            properties = schema.get("properties", {})
            if schema.get("additionalProperties") is False:
                extra = sorted(set(value) - set(properties))
                if extra:
                    raise ConfigError(f"{path}: unknown key(s) {', '.join(extra)}")
            for key, sub in properties.items():
                if key in value:
                    self._validate(value[key], sub, f"{path}/{key}")

        if isinstance(value, list):
            if "minItems" in schema and len(value) < schema["minItems"]:
                raise ConfigError(f"{path}: fewer than {schema['minItems']} item(s)")
            if schema.get("uniqueItems") and len(value) != len(
                {json.dumps(item, sort_keys=True) for item in value}
            ):
                raise ConfigError(f"{path}: items must be unique")
            if "items" in schema:
                for index, item in enumerate(value):
                    self._validate(item, schema["items"], f"{path}/{index}")
            for constraint in schema.get("allOf", []):
                if "contains" in constraint:
                    if not any(
                        self._item_matches(item, constraint["contains"], path)
                        for item in value
                    ):
                        raise ConfigError(
                            f"{path}: must contain an item matching "
                            f"{json.dumps(constraint['contains'], sort_keys=True)}"
                        )

        for constraint in schema.get("allOf", []):
            if "contains" not in constraint:
                self._validate(value, constraint, path)

    def _item_matches(self, item: object, subschema: dict, path: str) -> bool:
        try:
            self._assert_keywords(subschema, path)
            self._validate(item, subschema, path)
        except ConfigError:
            return False
        return True


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckSpec:
    id: str
    min_tier: str = "experimental"


@dataclass(frozen=True)
class LayerSpec:
    id: str
    enabled: bool
    blocking: bool
    checks: Tuple[CheckSpec, ...]
    description: str = ""

    @property
    def declared_severity(self) -> str:
        return "blocking" if self.blocking else "warning"


@dataclass
class OverlayConfig:
    path: Path
    repo_name: str
    repo_owner: str
    version: str
    source: str
    default_tier: str
    layers: Dict[str, LayerSpec]
    tiers: Dict[str, Dict[str, str]]
    non_negotiable: Tuple[str, ...]
    signals: Dict[str, dict]

    def effective_severity(self, layer_id: str, tier: str) -> str:
        """Severity a layer carries at a tier: disabled, else tier override, else flag."""
        layer = self.layers[layer_id]
        if not layer.enabled:
            return "disabled"
        override = self.tiers.get(tier, {}).get(layer_id)
        return override if override else layer.declared_severity

    def protected_paths(self) -> Tuple[str, ...]:
        return tuple(self.signals.get("protected_files", {}).get("paths", ()))


def _read_yaml(path: Path) -> object:
    if yaml is None:
        raise ConfigError("PyYAML is not importable, so the overlay config cannot be read")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc


def load_config(root: Path, config_path: Optional[Path] = None) -> OverlayConfig:
    """Load and schema-validate the overlay config. Any problem is fatal."""
    config_file = config_path or (root / "governance" / "cto-overlay" / CONFIG_NAME)
    if not config_file.is_file():
        raise ConfigError(f"overlay config not found at {config_file}")
    schema_file = config_file.parent / SCHEMA_NAME
    if not schema_file.is_file():
        raise ConfigError(f"overlay schema not found at {schema_file}")

    document = _read_yaml(config_file)
    if not isinstance(document, dict):
        raise ConfigError(f"{config_file} must contain a mapping at the top level")

    schema = _read_yaml(schema_file)
    if not isinstance(schema, dict):
        raise ConfigError(f"{schema_file} must contain a mapping at the top level")
    SchemaValidator(schema).validate(document)

    layers: Dict[str, LayerSpec] = {}
    for layer_id in LAYERS:
        raw = document["layers"][layer_id]
        unknown = [item["id"] for item in raw["checks"] if item["id"] not in CHECKS]
        if unknown:
            raise ConfigError(
                f"layer {layer_id!r} declares check(s) this engine does not implement: "
                f"{', '.join(sorted(unknown))} — an unimplemented check cannot be assessed"
            )
        layers[layer_id] = LayerSpec(
            id=layer_id,
            enabled=bool(raw["enabled"]),
            blocking=bool(raw["blocking"]),
            description=str(raw.get("description", "")),
            checks=tuple(
                CheckSpec(id=item["id"], min_tier=item.get("min_tier", "experimental"))
                for item in raw["checks"]
            ),
        )

    tiers = {
        tier: {layer: severity for layer, severity in (document["tiers"][tier] or {}).items()}
        for tier in TIERS
    }
    for tier, mapping in tiers.items():
        for layer_id, severity in mapping.items():
            if severity not in SEVERITIES:
                raise ConfigError(f"tiers.{tier}.{layer_id}: unknown severity {severity!r}")

    non_negotiable = tuple(document["non_negotiable"])
    unknown_signals = [name for name in non_negotiable if name not in SIGNALS]
    if unknown_signals:
        raise ConfigError(
            "non_negotiable names signal(s) this engine does not implement: "
            f"{', '.join(sorted(unknown_signals))}"
        )

    config = OverlayConfig(
        path=config_file,
        repo_name=str(document["repo"]["name"]),
        repo_owner=str(document["repo"]["owner"]),
        version=str(document["overlay"]["version"]),
        source=str(document["overlay"]["source"]),
        default_tier=str(document["overlay"].get("default_tier", "standard")),
        layers=layers,
        tiers=tiers,
        non_negotiable=non_negotiable,
        signals=dict(document.get("signals", {})),
    )
    if config.default_tier not in TIERS:
        raise ConfigError(f"overlay.default_tier: unknown tier {config.default_tier!r}")

    # A config that contradicts itself cannot be assessed. `standard` is the
    # default tier, so its entry and the layer's own `blocking` flag make the
    # same claim; if they disagree, one of them is a lie and picking either
    # silently is how a blocking layer becomes decorative.
    for layer_id in LAYERS:
        declared = config.tiers["standard"].get(layer_id)
        if declared and declared != layers[layer_id].declared_severity:
            raise ConfigError(
                f"contradiction: layers.{layer_id}.blocking="
                f"{str(layers[layer_id].blocking).lower()} implies "
                f"{layers[layer_id].declared_severity!r}, but tiers.standard.{layer_id} "
                f"declares {declared!r}"
            )
    return config


# --------------------------------------------------------------------------
# Run context and helpers
# --------------------------------------------------------------------------


@dataclass
class Context:
    root: Path
    config: OverlayConfig
    tier: str
    diff_base: Optional[str] = None

    def excluded_shell_prefixes(self) -> Tuple[str, ...]:
        raw = self.config.signals.get("shell_syntax", {}).get("exclude_prefixes", ())
        return tuple(raw) if raw else ("vendor/", ".research/", "node_modules/")


def run_command(command: Sequence[str], cwd: Path, timeout: int = COMMAND_TIMEOUT_SECONDS):
    """Run a real command offline. Returns (rc, stdout, stderr)."""
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise Indeterminate(f"{command[0]} is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise Indeterminate(
            f"{command[0]} exceeded its {timeout}s budget — verdict unknown, not clean"
        ) from exc
    return completed.returncode, completed.stdout, completed.stderr


def tracked_files(ctx: Context, *patterns: str) -> List[str]:
    """Tracked paths only. Untracked scratch is not the repository's verdict."""
    command = ["git", "-C", str(ctx.root), "ls-files", "-z"]
    if patterns:
        command += ["--", *patterns]
    rc, out, err = run_command(command, ctx.root)
    if rc != 0:
        raise Indeterminate(f"git ls-files failed (rc={rc}): {err.strip()[:200]}")
    return [item for item in out.split("\0") if item]


def _preview(items: Iterable[str], limit: int = 5) -> str:
    listing = list(items)
    shown = ", ".join(listing[:limit])
    return shown if len(listing) <= limit else f"{shown}, +{len(listing) - limit} more"


def _read_text(path: Path, limit: Optional[int] = None) -> str:
    data = path.read_bytes()
    if limit is not None:
        data = data[:limit]
    return data.decode("utf-8", errors="replace")


# --------------------------------------------------------------------------
# Layer checks — real gates over the repository, no network
# --------------------------------------------------------------------------


def check_adr(ctx: Context) -> Result:
    """Executive: architecture decisions are recorded as ADRs."""
    directory = ctx.root / "docs" / "decision-records"
    if not directory.is_dir():
        return STATE_FAIL, "docs/decision-records/ is missing — decisions are unrecorded"
    records = [p for p in tracked_files(ctx, "docs/decision-records/ADR-*.md")]
    if not records:
        return STATE_FAIL, "docs/decision-records/ tracks no ADR-*.md record"
    return STATE_PASS, f"{len(records)} ADR(s) tracked"


def check_dep_policy(ctx: Context) -> Result:
    """Executive: vendored and generated trees stay out of the tree; pins hold."""
    forbidden = [
        path
        for path in tracked_files(ctx)
        if path.startswith(("node_modules/", ".research/"))
    ]
    if forbidden:
        return STATE_FAIL, f"vendored/generated trees are tracked: {_preview(forbidden)}"

    gitmodules = ctx.root / ".gitmodules"
    if not gitmodules.is_file():
        return STATE_PASS, "no submodules and no vendored trees tracked"
    declared = [
        line.split("=", 1)[1].strip()
        for line in _read_text(gitmodules).splitlines()
        if line.strip().startswith("path")
    ]
    for path in declared:
        rc, out, err = run_command(
            ["git", "-C", str(ctx.root), "ls-files", "-s", "--", path], ctx.root
        )
        if rc != 0:
            raise Indeterminate(f"git ls-files -s {path} failed: {err.strip()[:200]}")
        if not out.startswith("160000"):
            return STATE_FAIL, f"declared submodule {path} is not a pinned gitlink"
    if not declared:
        return STATE_PASS, "no submodule paths declared"
    return STATE_PASS, f"{len(declared)} submodule(s) pinned as gitlinks"


def check_security_baseline(ctx: Context) -> Result:
    """Executive (critical tier): no private key material or raw env files tracked."""
    offenders: List[str] = []
    for pattern in ("*.pem", "*.key"):
        for path in tracked_files(ctx, pattern):
            if "PRIVATE KEY" in _read_text(ctx.root / path, limit=8192):
                offenders.append(path)
    for path in tracked_files(ctx):
        if Path(path).name == ".env":
            offenders.append(path)
    if offenders:
        return STATE_FAIL, f"private key material or raw .env tracked: {_preview(offenders)}"
    return STATE_PASS, "no private keys and no raw .env files tracked"


def check_syntax(ctx: Context) -> Result:
    """Engineering: every tracked shell script parses."""
    scripts = [
        path
        for path in tracked_files(ctx, "*.sh")
        if not path.startswith(ctx.excluded_shell_prefixes())
    ]
    if not scripts:
        return STATE_INDET, "git ls-files returned no shell scripts — not a checkout?"
    broken: List[str] = []
    for path in scripts:
        rc, _, _ = run_command(["bash", "-n", str(ctx.root / path)], ctx.root)
        if rc != 0:
            broken.append(path)
    if broken:
        return STATE_FAIL, f"shell syntax errors in {_preview(broken)}"
    return STATE_PASS, f"{len(scripts)} tracked shell script(s) parse"


def check_coverage(ctx: Context) -> Result:
    """Engineering: every declared pytest suite is discoverable."""
    manifest = ctx.root / "scripts" / "pytest-suites.txt"
    if not manifest.is_file():
        return STATE_FAIL, "scripts/pytest-suites.txt is missing — no declared suites"
    declared: List[str] = []
    for line in _read_text(manifest).splitlines():
        entry = line.split("#", 1)[0].strip()
        if entry:
            declared.append(entry)
    if not declared:
        return STATE_FAIL, "scripts/pytest-suites.txt declares no suites"
    missing: List[str] = []
    for entry in declared:
        tests_dir = ctx.root / entry / "tests"
        if not tests_dir.is_dir():
            missing.append(entry)
            continue
        if not any(tests_dir.glob("test_*.py")):
            missing.append(entry)
    if missing:
        return STATE_FAIL, f"declared suite(s) without tests: {_preview(missing)}"
    return STATE_PASS, f"{len(declared)} declared suite(s) discoverable"


def check_sast(ctx: Context) -> Result:
    """Engineering (critical tier): a SAST tool is available to run at all."""
    found = shutil.which("semgrep")
    if not found:
        return STATE_INDET, "semgrep is not installed — SAST did not run, so it is not clean"
    rc, out, err = run_command(["semgrep", "--version"], ctx.root)
    if rc != 0:
        return STATE_INDET, f"semgrep --version failed (rc={rc}): {err.strip()[:120]}"
    return STATE_PASS, f"semgrep {out.strip().splitlines()[0] if out.strip() else 'present'}"


def check_dockerfile_lint(ctx: Context) -> Result:
    """DevOps: every tracked Dockerfile declares a base image."""
    dockerfiles = [
        path
        for path in tracked_files(ctx)
        if Path(path).name == "Dockerfile" or Path(path).name.startswith("Dockerfile.")
    ]
    if not dockerfiles:
        return STATE_PASS, "no tracked Dockerfiles"
    broken = [
        path
        for path in dockerfiles
        if not re.search(r"^\s*FROM\s+\S", _read_text(ctx.root / path), re.MULTILINE)
    ]
    if broken:
        return STATE_FAIL, f"Dockerfile(s) without FROM: {_preview(broken)}"
    return STATE_PASS, f"{len(dockerfiles)} Dockerfile(s) declare a base image"


def check_compose_validate(ctx: Context) -> Result:
    """DevOps: every tracked compose file parses, or say so honestly."""
    compose = [
        path
        for path in tracked_files(ctx)
        if re.search(r"(^|/)docker-compose[^/]*\.ya?ml$", path)
        or Path(path).name in ("compose.yml", "compose.yaml")
    ]
    if not compose:
        return STATE_PASS, "no tracked compose files"
    if yaml is None:
        return STATE_INDET, "PyYAML is not importable — compose files were not validated"
    broken: List[str] = []
    for path in compose:
        try:
            yaml.safe_load((ctx.root / path).read_text(encoding="utf-8"))
        except (yaml.YAMLError, OSError, UnicodeDecodeError):
            broken.append(path)
    if broken:
        return STATE_FAIL, f"compose file(s) do not parse: {_preview(broken)}"
    return STATE_PASS, f"{len(compose)} compose file(s) parse"


def check_terraform_fmt(ctx: Context) -> Result:
    """DevOps: tracked Terraform is canonical, using the offline formatter."""
    terraform_files = tracked_files(ctx, "*.tf")
    if not terraform_files:
        return STATE_PASS, "no tracked Terraform"
    if not shutil.which("terraform"):
        return STATE_INDET, "terraform is not installed — formatting was not checked"
    roots = sorted({Path(path).parts[0] for path in terraform_files})
    unformatted: List[str] = []
    for top in roots:
        target = ctx.root / top
        if not target.is_dir():
            continue
        rc, out, _ = run_command(
            ["terraform", "fmt", "-check", "-recursive", str(target)], ctx.root
        )
        if rc != 0:
            unformatted.extend(line.strip() for line in out.splitlines() if line.strip())
    if unformatted:
        return STATE_FAIL, f"terraform fmt would rewrite: {_preview(unformatted)}"
    return STATE_PASS, f"{len(terraform_files)} Terraform file(s) canonical in {', '.join(roots)}"


def check_log_harvest(ctx: Context) -> Result:
    """Support: the telemetry surface exists and carries code."""
    telemetry = ctx.root / "telemetry"
    if not telemetry.is_dir():
        return STATE_FAIL, "telemetry/ is missing — there is nothing to harvest"
    modules = [
        path
        for path in tracked_files(ctx)
        if path.startswith("telemetry/") and path.endswith(".py")
    ]
    if not modules:
        return STATE_FAIL, "telemetry/ tracks no Python module — the surface is empty"
    return STATE_PASS, f"{len(modules)} telemetry module(s) tracked"


def check_self_heal(ctx: Context) -> Result:
    """Support: the reconcile worker and its gate are present and parse."""
    tracker = "governance/reconcile/cli.py"
    if tracker not in tracked_files(ctx):
        return STATE_FAIL, f"{tracker} is not tracked — self-healing has no worker"
    gate = ctx.root / "scripts" / "check-reconcile.sh"
    if not gate.is_file():
        return STATE_FAIL, "scripts/check-reconcile.sh is missing — the worker is unguarded"
    rc, _, _ = run_command(["bash", "-n", str(gate)], ctx.root)
    if rc != 0:
        return STATE_FAIL, "scripts/check-reconcile.sh does not parse"
    return STATE_PASS, "reconcile worker tracked and its gate parses"


def check_incident_response(ctx: Context) -> Result:
    """Support (critical tier): the runbook and the watchdog are in the tree."""
    required = ("governance/lessons/rca-template.md", "fleet/watchdog.py")
    tracked = set(tracked_files(ctx))
    missing = [path for path in required if path not in tracked]
    if missing:
        return STATE_FAIL, f"incident surface not tracked: {_preview(missing)}"
    return STATE_PASS, "RCA template and fleet watchdog tracked"


CHECKS: Dict[str, Callable[[Context], Result]] = {
    "adr-check": check_adr,
    "dep-policy": check_dep_policy,
    "security-baseline": check_security_baseline,
    "syntax": check_syntax,
    "coverage": check_coverage,
    "sast": check_sast,
    "dockerfile-lint": check_dockerfile_lint,
    "compose-validate": check_compose_validate,
    "terraform-fmt": check_terraform_fmt,
    "log-harvest": check_log_harvest,
    "self-heal": check_self_heal,
    "incident-response": check_incident_response,
}


# --------------------------------------------------------------------------
# Non-negotiable signals — always run, whatever the tier
# --------------------------------------------------------------------------
#
# The secret shapes below mirror this repository's own gate
# (scripts/check-secrets.sh) rather than inventing a second taxonomy: the same
# eight high-signal shapes and the same generic-assignment rule with the same
# placeholder exemption idea. The overlay's scan is scoped to TRACKED files so
# its verdict is about the repository, not about someone's scratch copies.

SECRET_SHAPES = (
    r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY( BLOCK)?-----",
    r"AKIA[0-9A-Z]{16}",
    r"(ghp|gho|ghu|ghs)_[A-Za-z0-9]{36,}",
    r"github_pat_[A-Za-z0-9_]{20,}",
    r"(sk|sk-ant|sk-proj)-[A-Za-z0-9_\-]{16,}",
    r"AIza[0-9A-Za-z_\-]{30,}",
    r"xox[baprs]-[A-Za-z0-9\-]{10,}",
)

SECRET_ASSIGNMENT = (
    r"(?:^|[^\w])(api[_-]?key|secret|password|token|access[_-]?key|client[_-]?secret)"
    r"[\"']?\s*[:=]\s*[\"'][^\"']{8,}[\"']"
)

DEFAULT_EXEMPT_MARKERS = (
    "example",
    "sample",
    "placeholder",
    "dummy",
    "fake",
    "changeme",
    "replace-me",
    "redacted",
    "not-a-real",
)

DEFAULT_FILE_SUFFIXES = (
    ".md",
    ".sh",
    ".py",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".txt",
    ".tsv",
)


def signal_shell_syntax(ctx: Context) -> Result:
    """Non-negotiable: shell syntax, whatever layer or tier is in play."""
    return check_syntax(ctx)


def signal_secret_scan(ctx: Context) -> Result:
    """Non-negotiable: a mechanical secret scan over tracked text files."""
    options = ctx.config.signals.get("secret_scan", {})
    max_bytes = int(options.get("max_file_bytes", 1048576))
    suffixes = tuple(options.get("file_suffixes", DEFAULT_FILE_SUFFIXES))
    markers = tuple(options.get("exempt_markers", DEFAULT_EXEMPT_MARKERS))
    shape_re = re.compile("|".join(SECRET_SHAPES))
    assignment_re = re.compile(SECRET_ASSIGNMENT)
    marker_re = re.compile("|".join(re.escape(marker) for marker in markers), re.IGNORECASE)

    findings: List[str] = []
    scanned = 0
    for path in tracked_files(ctx):
        lowered = path.lower()
        if not lowered.endswith(suffixes) and Path(path).name != "Makefile":
            continue
        full = ctx.root / path
        try:
            text = _read_text(full, limit=max_bytes)
        except OSError:
            continue
        scanned += 1
        for lineno, line in enumerate(text.splitlines(), start=1):
            if shape_re.search(line):
                if marker_re.search(line):
                    continue
                findings.append(f"{path}:{lineno}")
                continue
            if assignment_re.search(line) and not marker_re.search(line):
                findings.append(f"{path}:{lineno}")
    if findings:
        return STATE_FAIL, f"possible secret(s) at {_preview(findings)}"
    return STATE_PASS, f"no secret shape found in {scanned} tracked text file(s)"


def path_problem(path: str, max_length: int = 240) -> Optional[str]:
    """Why a repository-relative path is not safe, or None when it is."""
    if not path:
        return "empty path"
    if len(path) > max_length:
        return f"longer than {max_length} characters"
    if path.startswith("/"):
        return "absolute path"
    if "\\" in path:
        return "backslash in path"
    if any(ord(char) < 32 or ord(char) == 127 for char in path):
        return "control character in path"
    parts = path.split("/")
    if ".." in parts:
        return "parent-directory traversal"
    if any(part in ("", ".") for part in parts):
        return "empty path segment"
    return None


def signal_path_integrity(ctx: Context) -> Result:
    """Non-negotiable: tracked paths and declared paths stay inside the repo."""
    max_length = int(
        ctx.config.signals.get("path_integrity", {}).get("max_path_length", 240)
    )
    problems: List[str] = []
    for path in tracked_files(ctx):
        problem = path_problem(path, max_length)
        if problem:
            problems.append(f"tracked {path!r}: {problem}")
    for path in ctx.config.protected_paths():
        problem = path_problem(path, max_length)
        if problem:
            problems.append(f"declared {path!r}: {problem}")
            continue
        try:
            resolved = (ctx.root / path).resolve()
            root_resolved = ctx.root.resolve()
            if root_resolved not in resolved.parents and resolved != root_resolved:
                problems.append(f"declared {path!r}: resolves outside the repository root")
        except (OSError, RuntimeError) as exc:
            problems.append(f"declared {path!r}: cannot be resolved ({exc})")
    if problems:
        return STATE_FAIL, "; ".join(problems[:3])
    return STATE_PASS, f"tracked and declared paths stay inside the repository root"


def signal_protected_files(ctx: Context) -> Result:
    """Non-negotiable: critical files exist, are tracked, and are unmodified."""
    paths = ctx.config.protected_paths()
    problems: List[str] = []
    tracked = set(tracked_files(ctx))
    for path in paths:
        full = ctx.root / path
        if not full.is_file():
            problems.append(f"{path} is missing")
        elif path not in tracked:
            problems.append(f"{path} is not tracked")
    if problems:
        return STATE_FAIL, "; ".join(problems[:3])

    for path in paths:
        rc, out, err = run_command(
            ["git", "-C", str(ctx.root), "status", "--porcelain", "--", path], ctx.root
        )
        if rc != 0:
            raise Indeterminate(f"git status failed for {path}: {err.strip()[:200]}")
        if out.strip():
            problems.append(f"{path} has unreviewed working-tree changes")
    if ctx.diff_base:
        rc, _, err = run_command(
            ["git", "-C", str(ctx.root), "rev-parse", "--verify", ctx.diff_base], ctx.root
        )
        if rc != 0:
            raise Indeterminate(f"diff base {ctx.diff_base!r} does not resolve")
        for path in paths:
            rc, out, _ = run_command(
                ["git", "-C", str(ctx.root), "diff", "--name-only", ctx.diff_base, "--", path],
                ctx.root,
            )
            if rc != 0:
                raise Indeterminate(f"git diff against {ctx.diff_base!r} failed for {path}")
            if out.strip():
                problems.append(f"{path} differs from {ctx.diff_base}")
    if problems:
        return STATE_FAIL, "; ".join(problems[:3])
    return STATE_PASS, f"{len(paths)} protected file(s) present and unmodified"


SIGNALS: Dict[str, Callable[[Context], Result]] = {
    "shell_syntax": signal_shell_syntax,
    "secret_scan": signal_secret_scan,
    "protected_files": signal_protected_files,
    "path_integrity": signal_path_integrity,
}


# --------------------------------------------------------------------------
# The verdict tally (#1721)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    scope: str
    check: str
    state: str
    severity: str
    detail: str

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "check": self.check,
            "state": self.state,
            "severity": self.severity,
            "detail": self.detail,
        }


@dataclass
class LayerSummary:
    layer: str
    status: str
    severity: str
    passed: int = 0
    failed: int = 0
    indeterminate: int = 0
    skipped: int = 0

    def as_dict(self) -> dict:
        return {
            "layer": self.layer,
            "status": self.status,
            "severity": self.severity,
            "passed": self.passed,
            "failed": self.failed,
            "indeterminate": self.indeterminate,
            "skipped": self.skipped,
        }


class Tally:
    """One record per (scope, check). Duplicates and unknown states are refused."""

    def __init__(self, expected_scopes: Sequence[str]) -> None:
        self.expected_scopes = tuple(expected_scopes)
        self._verdicts: List[Verdict] = []
        self._seen: set = set()

    @property
    def verdicts(self) -> List[Verdict]:
        return list(self._verdicts)

    def record(self, verdict: Verdict) -> None:
        if verdict.state not in STATES:
            raise EngineError(
                f"refusing verdict {verdict.state!r} for {verdict.scope}/{verdict.check} — "
                "an unknown state is not a pass"
            )
        key = (verdict.scope, verdict.check)
        if key in self._seen:
            raise EngineError(
                f"{verdict.scope}/{verdict.check} was recorded twice — the tally must "
                "count every check exactly once"
            )
        self._seen.add(key)
        self._verdicts.append(verdict)

    def total(self) -> int:
        return len(self._verdicts)

    def counts(self) -> Dict[str, int]:
        counts = {state: 0 for state in STATES}
        for verdict in self._verdicts:
            counts[verdict.state] += 1
        return counts

    def layer_summaries(self) -> List[LayerSummary]:
        summaries: List[LayerSummary] = []
        for scope in self.expected_scopes:
            entries = [v for v in self._verdicts if v.scope == scope]
            severity = entries[0].severity if entries else "unknown"
            summary = LayerSummary(layer=scope, status="UNKNOWN", severity=severity)
            for verdict in entries:
                if verdict.state == STATE_PASS:
                    summary.passed += 1
                elif verdict.state == STATE_FAIL:
                    summary.failed += 1
                elif verdict.state == STATE_INDET:
                    summary.indeterminate += 1
                else:
                    summary.skipped += 1
            summary.status = _layer_status(summary, severity)
            summaries.append(summary)
        return summaries


def _layer_status(summary: LayerSummary, severity: str) -> str:
    if severity == "disabled":
        return "DISABLED"
    if summary.passed == 0 and summary.failed == 0 and summary.indeterminate == 0:
        return "UNSELECTED"
    if severity == "blocking" and summary.failed:
        return "BLOCKING-FAIL"
    if severity == "blocking" and summary.indeterminate:
        return "BLOCKING-INDET"
    if summary.failed or summary.indeterminate:
        return "WARNING"
    return "OK"


# --------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------


@dataclass
class Report:
    root: Path
    tier: str
    config_path: Path
    tally: Tally
    exit_code: int
    cannot_assess: List[str] = field(default_factory=list)
    not_ok: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "root": str(self.root),
            "tier": self.tier,
            "config": str(self.config_path),
            "verdicts": [v.as_dict() for v in self.tally.verdicts],
            "layers": [s.as_dict() for s in self.tally.layer_summaries()],
            "counts": self.tally.counts(),
            "cannot_assess": self.cannot_assess,
            "not_ok": self.not_ok,
            "exit_code": self.exit_code,
        }


def _assess(ctx: Context, name: str, function: Callable[[Context], Result]) -> Result:
    """Run one check. Anything that stops it becomes INDET, never a pass."""
    try:
        state, detail = function(ctx)
    except Indeterminate as exc:
        return STATE_INDET, str(exc)
    except Exception as exc:  # a crashing check is never a pass
        return STATE_INDET, f"check raised {type(exc).__name__}: {exc}"
    if state not in STATES:
        raise EngineError(f"check {name!r} returned an unknown state {state!r}")
    return state, detail


def run_overlay(
    root: Path,
    config: OverlayConfig,
    tier: str,
    selected_layers: Optional[Sequence[str]] = None,
    diff_base: Optional[str] = None,
) -> Report:
    """Run every selected layer check plus every non-negotiable signal."""
    ctx = Context(root=root, config=config, tier=tier, diff_base=diff_base)
    tally = Tally(expected_scopes=tuple(LAYERS) + (SIGNAL_SCOPE,))
    cannot_assess: List[str] = []
    not_ok: List[str] = []
    selection = tuple(selected_layers) if selected_layers else tuple(LAYERS)

    for layer_id in LAYERS:
        layer = config.layers[layer_id]
        severity = config.effective_severity(layer_id, tier)
        for check in layer.checks:
            if not layer.enabled:
                tally.record(
                    Verdict(layer_id, check.id, STATE_SKIP, "disabled", "layer disabled in config")
                )
                continue
            if layer_id not in selection:
                tally.record(
                    Verdict(layer_id, check.id, STATE_SKIP, severity, "layer not selected")
                )
                continue
            if TIER_RANK[tier] < TIER_RANK[check.min_tier]:
                tally.record(
                    Verdict(
                        layer_id,
                        check.id,
                        STATE_SKIP,
                        severity,
                        f"requires tier >= {check.min_tier}",
                    )
                )
                continue
            state, detail = _assess(ctx, check.id, CHECKS[check.id])
            tally.record(Verdict(layer_id, check.id, state, severity, detail))

    for signal_name in config.non_negotiable:
        state, detail = _assess(ctx, signal_name, SIGNALS[signal_name])
        tally.record(Verdict(SIGNAL_SCOPE, signal_name, state, "blocking", detail))

    for summary in tally.layer_summaries():
        if summary.layer == SIGNAL_SCOPE:
            continue
        if summary.status == "BLOCKING-FAIL":
            not_ok.append(f"{summary.layer}: blocking check failed")
        elif summary.status == "BLOCKING-INDET":
            cannot_assess.append(f"{summary.layer}: blocking check could not be assessed")
    for verdict in tally.verdicts:
        if verdict.scope != SIGNAL_SCOPE:
            continue
        if verdict.state == STATE_FAIL:
            not_ok.append(f"{SIGNAL_SCOPE}/{verdict.check} failed")
        elif verdict.state == STATE_INDET:
            cannot_assess.append(f"{SIGNAL_SCOPE}/{verdict.check} could not be assessed")

    if not_ok:
        exit_code = EXIT_NOT_OK
    elif cannot_assess:
        exit_code = EXIT_CANNOT_ASSESS
    else:
        exit_code = EXIT_OK
    return Report(
        root=root,
        tier=tier,
        config_path=config.path,
        tally=tally,
        exit_code=exit_code,
        cannot_assess=cannot_assess,
        not_ok=not_ok,
    )


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

ICONS = {STATE_PASS: "OK  ", STATE_FAIL: "FAIL", STATE_INDET: "INDET", STATE_SKIP: "SKIP"}
VERDICT_LINE = {
    EXIT_OK: "OK",
    EXIT_NOT_OK: "NOT-OK",
    EXIT_CANNOT_ASSESS: "CANNOT-ASSESS",
}


def render_text(report: Report, config: OverlayConfig) -> str:
    lines: List[str] = []
    lines.append(f"CTO overlay — {config.repo_owner}/{config.repo_name} @ tier {report.tier}")
    lines.append(f"  root:   {report.root}")
    lines.append(f"  config: {report.config_path}")
    lines.append("")
    for summary in report.tally.layer_summaries():
        if summary.layer == SIGNAL_SCOPE:
            continue
        lines.append(f"[{summary.layer}] {summary.status} (severity={summary.severity})")
        for verdict in report.tally.verdicts:
            if verdict.scope != summary.layer:
                continue
            lines.append(f"  {ICONS[verdict.state]} {verdict.check}: {verdict.detail}")
    lines.append("")
    lines.append("[non-negotiable] signals — always run, whatever the tier")
    for verdict in report.tally.verdicts:
        if verdict.scope != SIGNAL_SCOPE:
            continue
        lines.append(f"  {ICONS[verdict.state]} {verdict.check}: {verdict.detail}")
    counts = report.tally.counts()
    lines.append("")
    lines.append(
        "pass={pass} fail={fail} indet={indet} skip={skip} of {total} recorded verdict(s)".format(
            total=report.tally.total(), **{key.lower(): value for key, value in counts.items()}
        )
    )
    for reason in report.not_ok:
        lines.append(f"  NOT-OK: {reason}")
    for reason in report.cannot_assess:
        lines.append(f"  CANNOT-ASSESS: {reason}")
    lines.append(f"CTO overlay verdict: {VERDICT_LINE[report.exit_code]}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _resolve_tier(name: Optional[str], config: OverlayConfig) -> str:
    candidate = name or config.default_tier
    resolved = TIER_ALIASES.get(candidate, candidate)
    if resolved not in TIERS:
        raise ConfigError(f"unknown tier {candidate!r}; expected one of {', '.join(TIERS)}")
    return resolved


def cmd_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(root, Path(args.config).resolve() if args.config else None)
    tier = _resolve_tier(args.tier, config)
    selection = [item.strip() for item in (args.layers or "").split(",") if item.strip()]
    unknown = [item for item in selection if item not in LAYERS]
    if unknown:
        raise ConfigError(f"--layers names unknown layer(s): {', '.join(unknown)}")
    report = run_overlay(root, config, tier, selection, args.diff_base)
    if args.format == "json":
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True))
    else:
        print(render_text(report, config))
    return report.exit_code


def cmd_validate(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(root, Path(args.config).resolve() if args.config else None)
    print(
        f"CTO overlay config OK: {config.repo_owner}/{config.repo_name} "
        f"v{config.version}, {len(config.layers)} layer(s), "
        f"{len(config.non_negotiable)} non-negotiable signal(s)"
    )
    for layer_id in LAYERS:
        layer = config.layers[layer_id]
        checks = ", ".join(
            check.id if check.min_tier == "experimental" else f"{check.id}@{check.min_tier}"
            for check in layer.checks
        )
        print(f"  {layer_id}: blocking={str(layer.blocking).lower()} checks: {checks}")
    return EXIT_OK


def cmd_list_layers(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    config = load_config(root, Path(args.config).resolve() if args.config else None)
    tier = _resolve_tier(args.tier, config)
    for layer_id in LAYERS:
        layer = config.layers[layer_id]
        severity = config.effective_severity(layer_id, tier)
        checks = ", ".join(check.id for check in layer.checks)
        print(f"{layer_id}: {severity} at tier {tier} — checks: {checks}")
    print(f"non-negotiable: {', '.join(config.non_negotiable)}")
    return EXIT_OK


# --------------------------------------------------------------------------
# Negative control: prove the gate can fail
# --------------------------------------------------------------------------

MINIMAL_FIXTURE = {
    "docs/decision-records/ADR-0001-fixture.md": "# ADR-0001: fixture\n",
    "scripts/pytest-suites.txt": "sample\n",
    "sample/tests/test_sample.py": "def test_sample():\n    assert True\n",
    "telemetry/README.md": "# telemetry\n",
    "telemetry/collect.py": "TELEMETRY = True\n",
    "governance/reconcile/cli.py": "RECONCILE = True\n",
    "scripts/check-reconcile.sh": "#!/usr/bin/env bash\nset -u\ntrue\n",
    "Makefile": "verify:\n\t@true\n",
    "scripts/verify.sh": "#!/usr/bin/env bash\nset -u\ntrue\n",
    "AGENTS.md": "# fixture\n",
    "docs/GOLDEN-RULES.md": "# fixture rules\n",
}


def _fixture_repo(base: Path, source_config: Path) -> Path:
    """A minimal governed repo: only the surfaces the shipped config asserts."""
    root = base / "fixture"
    overlay = root / "governance" / "cto-overlay"
    overlay.mkdir(parents=True)
    for name in (SCHEMA_NAME, CONFIG_NAME):
        shutil.copyfile(source_config.parent / name, overlay / name)
    for relative, content in MINIMAL_FIXTURE.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    run_command(["git", "init", "-q"], root)
    run_command(["git", "-C", str(root), "add", "-A"], root)
    run_command(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.email=overlay@example.invalid",
            "-c",
            "user.name=overlay self-test",
            "commit",
            "-q",
            "-m",
            "fixture",
        ],
        root,
    )
    return root


def _engine_rc(root: Path, *extra: str) -> Tuple[int, str]:
    command = [sys.executable, str(Path(__file__).resolve()), "run", "--root", str(root), *extra]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return 124, "engine exceeded its 300s budget"
    return completed.returncode, completed.stdout + completed.stderr


def cmd_self_test(args: argparse.Namespace) -> int:
    """Plant real defects in a scratch fixture and require the gate to fail.

    Five controls, each one a claim the overlay makes about itself. The
    config control deliberately uses a layer-only defect (a missing ADR) and
    not a missing shell script: shell syntax is also a non-negotiable signal,
    so it must keep blocking even when the engineering layer is demoted. That
    separation is the point of the baseline.
    """
    source_config = Path(args.config).resolve() if args.config else OVERLAY_DIR / CONFIG_NAME
    failures: List[str] = []
    total = 0
    with tempfile.TemporaryDirectory(prefix="cto-overlay-self-test-") as workdir:
        base = Path(workdir)
        root = _fixture_repo(base, source_config)
        fixture_config = root / "governance" / "cto-overlay" / CONFIG_NAME
        text = fixture_config.read_text(encoding="utf-8")
        adr = root / "docs" / "decision-records" / "ADR-0001-fixture.md"

        def check(label: str, ok: bool, observed: str) -> None:
            nonlocal total
            total += 1
            if ok:
                print(f"  OK   {label}")
            else:
                print(f"  FAIL {label}")
                print("\n".join(f"       {line}" for line in observed.splitlines()[-12:]))
                failures.append(label)

        rc, out = _engine_rc(root)
        check("positive control: clean fixture exits 0", rc == EXIT_OK, out)

        broken = root / "scripts" / "broken.sh"
        broken.write_text(
            '#!/usr/bin/env bash\nif [ 1 -eq 1 ]; then\n  echo "unterminated\n', encoding="utf-8"
        )
        run_command(["git", "-C", str(root), "add", "-A"], root)
        rc, out = _engine_rc(root)
        check(
            f"negative control: planted syntax error exits {EXIT_NOT_OK} and names the file",
            rc == EXIT_NOT_OK and "broken.sh" in out,
            out,
        )
        broken.unlink()
        run_command(["git", "-C", str(root), "add", "-A"], root)

        adr.unlink()
        run_command(["git", "-C", str(root), "add", "-A"], root)
        rc, out = _engine_rc(root)
        check(
            f"layer control: a failing blocking layer exits {EXIT_NOT_OK}",
            rc == EXIT_NOT_OK and "adr-check" in out and "BLOCKING-FAIL" in out,
            out,
        )

        demoted = text.replace(
            "  executive:\n    enabled: true\n    blocking: true\n",
            "  executive:\n    enabled: true\n    blocking: false\n",
        ).replace("    executive: blocking", "    executive: warning")
        fixture_config.write_text(demoted, encoding="utf-8")
        rc, out = _engine_rc(root)
        check(
            "config control: the same defect no longer blocks once the config says so",
            rc == EXIT_OK and "FAIL adr-check" in out,
            out,
        )
        fixture_config.write_text(text, encoding="utf-8")
        adr.write_text("# ADR-0001: fixture\n", encoding="utf-8")
        run_command(["git", "-C", str(root), "add", "-A"], root)

        contradiction = text.replace("    executive: blocking", "    executive: advisory")
        fixture_config.write_text(contradiction, encoding="utf-8")
        rc, out = _engine_rc(root)
        check(
            f"contradiction control: a self-contradicting config exits {EXIT_CANNOT_ASSESS}",
            rc == EXIT_CANNOT_ASSESS and "contradiction" in out,
            out,
        )
        fixture_config.write_text(text, encoding="utf-8")

        schema = root / "governance" / "cto-overlay" / SCHEMA_NAME
        schema.write_text(
            schema.read_text(encoding="utf-8") + "\nunknownKeyword: true\n", encoding="utf-8"
        )
        rc, out = _engine_rc(root)
        check(
            f"schema control: an unenforceable schema keyword exits {EXIT_CANNOT_ASSESS}, not 0",
            rc == EXIT_CANNOT_ASSESS and "does not implement" in out,
            out,
        )

    print("")
    if failures:
        print(
            f"CTO overlay self-test: {len(failures)}/{total} control(s) misbehaved — "
            "the gate is not trustworthy"
        )
        return EXIT_NOT_OK
    print(f"CTO overlay self-test: {total}/{total} controls behaved as specified")
    return EXIT_OK


# --------------------------------------------------------------------------
# apply: drop the overlay into another repository
# --------------------------------------------------------------------------


def cmd_apply(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve()
    source_root = OVERLAY_DIR.parents[1]
    if target == source_root:
        print(
            f"CTO overlay apply: refusing to apply the overlay onto its own source repo ({target})",
            file=sys.stderr,
        )
        return EXIT_NOT_OK
    if not target.is_dir():
        print(f"CTO overlay apply: target is not a directory: {target}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    destination = target / "governance" / "cto-overlay"
    if destination.exists() and not args.force:
        print(
            f"CTO overlay apply: {destination} already exists — pass --force to overwrite",
            file=sys.stderr,
        )
        return EXIT_NOT_OK

    missing = [name for name in ARTIFACTS if not (OVERLAY_DIR / name).is_file()]
    if missing:
        print(f"CTO overlay apply: source artifact(s) missing: {', '.join(missing)}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS

    if args.dry_run:
        print(f"CTO overlay apply (dry run): would write {len(ARTIFACTS)} artifact(s) to {destination}")
        for name in ARTIFACTS:
            print(f"  {name}")
        return EXIT_OK

    destination.mkdir(parents=True, exist_ok=True)
    for name in ARTIFACTS:
        shutil.copyfile(OVERLAY_DIR / name, destination / name)

    manifest = destination / CONFIG_NAME
    text = manifest.read_text(encoding="utf-8")
    owner, name = _target_identity(target, args.owner, args.name)
    text = re.sub(r'^  name: ".*"$', f'  name: "{name}"', text, count=1, flags=re.MULTILINE)
    text = re.sub(r'^  owner: ".*"$', f'  owner: "{owner}"', text, count=1, flags=re.MULTILINE)
    manifest.write_text(text, encoding="utf-8")

    load_config(target)
    print(f"CTO overlay applied to {target}")
    print(f"  {len(ARTIFACTS)} artifact(s) in {destination}")
    print(f"  config validated for {owner}/{name}")
    print("  run it: python3 governance/cto-overlay/overlay.py run --root .")
    return EXIT_OK


def _target_identity(target: Path, owner: Optional[str], name: Optional[str]) -> Tuple[str, str]:
    resolved_name = name or target.name
    resolved_owner = owner
    if not resolved_owner:
        rc, out, _ = run_command(
            ["git", "-C", str(target), "config", "--get", "remote.origin.url"], target
        )
        if rc == 0 and out.strip():
            match = re.search(r"[:/]([^/:]+)/[^/]+?(?:\.git)?$", out.strip())
            resolved_owner = match.group(1) if match else "unknown"
        else:
            resolved_owner = "unknown"
    return resolved_owner, resolved_name


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="overlay.py",
        description="CTO overlay — four-layer governance with a truthful verdict (#147)",
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--root", default=str(REPO_ROOT_DEFAULT), help="repository to assess")
        sub.add_argument("--config", default=None, help="config path override")

    run = subparsers.add_parser("run", help="run the layers and the non-negotiable signals")
    add_common(run)
    run.add_argument("--tier", default=None, help="experimental | standard | critical (or T1/T2/T3)")
    run.add_argument("--layers", default=None, help="comma-separated subset; signals always run")
    run.add_argument("--diff-base", default=None, help="git ref for the protected-files comparison")
    run.add_argument("--format", default="text", choices=("text", "json"))

    validate = subparsers.add_parser("validate", help="schema-validate the config only")
    add_common(validate)

    listing = subparsers.add_parser("list-layers", help="show layers, severities and checks")
    add_common(listing)
    listing.add_argument("--tier", default=None)

    subparsers.add_parser("self-test", help="negative control: prove the gate can fail").add_argument(
        "--config", default=None
    )

    apply_parser = subparsers.add_parser("apply", help="drop the overlay into another repository")
    apply_parser.add_argument("--target", required=True)
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.add_argument("--force", action="store_true")
    apply_parser.add_argument("--owner", default=None)
    apply_parser.add_argument("--name", default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "run"
    handlers = {
        "run": cmd_run,
        "validate": cmd_validate,
        "list-layers": cmd_list_layers,
        "self-test": cmd_self_test,
        "apply": cmd_apply,
    }
    if command not in handlers:
        parser.print_help(sys.stderr)
        return EXIT_CANNOT_ASSESS
    if command == "apply" and not hasattr(args, "root"):
        args.root = str(REPO_ROOT_DEFAULT)
    try:
        return handlers[command](args)
    except ConfigError as exc:
        print(f"CTO overlay CANNOT-ASSESS: {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS
    except EngineError as exc:
        print(f"CTO overlay CANNOT-ASSESS (engine): {exc}", file=sys.stderr)
        return EXIT_CANNOT_ASSESS


if __name__ == "__main__":
    sys.exit(main())
