"""guardrails.dlp.catalog — scrub-rule catalog model and strict loader.

Loads ``scrub-rules.yml`` (the policy catalog, single source of truth for the
outbound scrub gate) into validated :class:`ScrubRule` objects.

Fail-closed contract (security lane):

* a catalog that is missing, empty, or carries an unknown
  class/severity/action is rejected with :class:`CatalogError` — the engine
  will *not* run (and therefore no call is dispatched) against a catalog it
  cannot fully understand;
* a rule whose regex does not compile is a hard error, never skipped;
* a ``redact`` rule without a placeholder is a hard error (the gate would not
  know what to substitute);
* duplicate rule ids are rejected so audit counts stay unambiguous.

Nothing here can silently pass on a malformed catalog — every branch raises.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("guardrails.dlp requires PyYAML") from exc

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CATALOG_PATH = os.path.join(_HERE, "scrub-rules.yml")

VALID_CLASSES = frozenset(
    {"secret", "private_key", "cloud_cred", "secret_store", "pii", "internal_infra"}
)
VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low"})
VALID_ACTIONS = frozenset({"block", "redact"})


class CatalogError(ValueError):
    """Raised when a scrub-rule catalog is missing, malformed, or ambiguous."""


@dataclass(frozen=True)
class ScrubRule:
    """A single compiled scrub rule from the policy catalog."""

    id: str
    class_: str
    severity: str
    action: str  # "block" | "redact"
    pattern: str
    placeholder: str = ""
    luhn: bool = False
    source: str = ""

    @property
    def compiled(self) -> "re.Pattern[str]":
        return re.compile(self.pattern)

    @property
    def is_block(self) -> bool:
        return self.action == "block"


@dataclass(frozen=True)
class ScrubMatch:
    """A concrete detection of one rule in a payload."""

    rule_id: str
    rule_class: str
    severity: str
    action: str
    placeholder: str
    start: int
    end: int
    value: str


@dataclass
class RuleCatalog:
    """Validated rule set with schema/ruleset version metadata."""

    schema_version: str
    ruleset_version: str
    rules: list  # list[ScrubRule]
    _by_id: dict = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._by_id = {rule.id: rule for rule in self.rules}

    def get(self, rule_id: str) -> Optional[ScrubRule]:
        return self._by_id.get(rule_id)

    def ids(self) -> list:
        return [rule.id for rule in self.rules]

    @classmethod
    def load(cls, path: Optional[str] = None) -> "RuleCatalog":
        """Load and strictly validate a scrub-rules catalog from YAML."""
        path = path or DEFAULT_CATALOG_PATH
        if not os.path.isfile(path):
            raise CatalogError(f"catalog not found: {path}")
        try:
            with open(path, encoding="utf-8") as fh:
                doc = yaml.safe_load(fh)
        except yaml.YAMLError as exc:
            raise CatalogError(f"catalog is not valid YAML: {exc}") from exc
        if not isinstance(doc, dict):
            raise CatalogError("catalog top level must be a mapping")

        schema_version = doc.get("schema_version")
        ruleset_version = doc.get("ruleset_version")
        if not schema_version or not ruleset_version:
            raise CatalogError("catalog must declare schema_version and ruleset_version")

        raw_rules = doc.get("rules")
        if not isinstance(raw_rules, list) or not raw_rules:
            raise CatalogError("catalog rules must be a non-empty list")

        seen: set = set()
        rules: list = []
        for idx, raw in enumerate(raw_rules):
            if not isinstance(raw, dict):
                raise CatalogError(f"rule #{idx} is not a mapping")
            rule_id = raw.get("id")
            if not rule_id or not isinstance(rule_id, str):
                raise CatalogError(f"rule #{idx} missing a string id")
            if rule_id in seen:
                raise CatalogError(f"duplicate rule id: {rule_id}")
            seen.add(rule_id)

            class_ = raw.get("class")
            if class_ not in VALID_CLASSES:
                raise CatalogError(f"rule {rule_id}: unknown class {class_!r}")
            severity = raw.get("severity")
            if severity not in VALID_SEVERITIES:
                raise CatalogError(f"rule {rule_id}: unknown severity {severity!r}")
            action = raw.get("action")
            if action not in VALID_ACTIONS:
                raise CatalogError(f"rule {rule_id}: unknown action {action!r}")
            pattern = raw.get("pattern")
            if not pattern or not isinstance(pattern, str):
                raise CatalogError(f"rule {rule_id}: missing pattern")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise CatalogError(f"rule {rule_id}: pattern does not compile: {exc}") from exc

            placeholder = raw.get("placeholder", "")
            if action == "redact" and not placeholder:
                raise CatalogError(f"rule {rule_id}: redact rules require a placeholder")

            luhn = raw.get("luhn", False)
            if not isinstance(luhn, bool):
                raise CatalogError(f"rule {rule_id}: luhn must be a boolean")

            rules.append(
                ScrubRule(
                    id=rule_id,
                    class_=class_,
                    severity=severity,
                    action=action,
                    pattern=pattern,
                    placeholder=placeholder,
                    luhn=luhn,
                    source=os.path.basename(path),
                )
            )

        return cls(
            schema_version=str(schema_version),
            ruleset_version=str(ruleset_version),
            rules=rules,
        )

    @classmethod
    def load_default(cls) -> "RuleCatalog":
        """Load the catalog bundled with this package."""
        return cls.load(DEFAULT_CATALOG_PATH)
