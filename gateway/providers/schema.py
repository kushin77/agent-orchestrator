"""Output-schema validation - typed output, fail closed (issue #15).

Every provider adapter validates the model's typed output against the
caller's output schema (JSON Schema, matching the issue-#13 prompt-module
``outputSchema`` contract). Validation is strict:

- content that is not parseable JSON when a schema is required is rejected;
- content that parses but does not satisfy the schema is rejected
  (``OutputValidationError``) - the adapter NEVER silently passes invalid
  output through (no-false-green / fail-closed doctrine).

``jsonschema`` is the validation engine (a declared dependency of the
prompt-library lane and importable in this environment). If it is absent the
validator refuses to operate rather than hand-roll a partial checker -
an honest fail-closed posture, never security/proof theater.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from providers.errors import OutputValidationError, SchemaDefinitionError

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*$")


def load_schema(schema_src: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Normalize an output schema to a dict.

    ``schema_src`` may already be a JSON-Schema object, or a path to a
    ``.json`` schema file (as declared by prompt-module ``outputSchema``).
    """
    if isinstance(schema_src, (str, Path)):
        path = Path(schema_src)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            raise SchemaDefinitionError(
                f"cannot load output schema from {path}: {exc}"
            ) from exc
    if not isinstance(schema_src, Mapping):
        raise SchemaDefinitionError("output schema must be a JSON Schema object or path")
    return dict(schema_src)


class OutputSchemaValidator:
    """Validates parsed model content against a JSON Schema (draft-07)."""

    def __init__(self, schema: Mapping[str, Any]) -> None:
        try:
            from jsonschema import Draft7Validator
            from jsonschema.exceptions import SchemaError
        except ImportError as exc:  # pragma: no cover - exercised only w/o dep
            raise SchemaDefinitionError(
                "jsonschema is required for output-schema validation and is not "
                "installed; refusing to validate with a partial checker (fail closed)"
            ) from exc
        self._schema = dict(schema)
        try:
            # Reject a malformed schema up front (caller error, not a provider issue).
            Draft7Validator.check_schema(self._schema)
            self._validator = Draft7Validator(self._schema)
        except SchemaError as exc:
            raise SchemaDefinitionError(f"invalid JSON Schema: {exc.message}") from exc

    def validate(self, content: Any) -> Any:
        """Validate ``content``; raise ``OutputValidationError`` on any mismatch."""
        errors = sorted(self._validator.iter_errors(content), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            where = "/".join(str(p) for p in first.path) or "<root>"
            raise OutputValidationError(
                f"provider output failed schema validation at {where}: {first.message}"
            )
        return content


def _extract_json_text(text: str) -> str:
    """Return the JSON payload candidate inside ``text``.

    Strips a ```json``` fenced block when the whole payload is inside one;
    a balanced-tail retry for prose-wrapped JSON is handled by
    ``parse_and_validate`` (first ``{``/``[`` onward).
    """
    candidate = text.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and _FENCE_RE.match(lines[0].strip()):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    return candidate


def parse_and_validate(text: str, schema_src: Mapping[str, Any] | str | Path | None) -> Any:
    """Parse ``text`` and validate it against ``schema_src``.

    With ``schema_src`` None the raw text is returned unchanged (plain chat).
    Otherwise the JSON payload is extracted and must satisfy the schema or
    ``OutputValidationError`` is raised.
    """
    if schema_src is None:
        return text
    schema = load_schema(schema_src)
    validator = OutputSchemaValidator(schema)

    payload = _extract_json_text(text)
    candidates: list[str] = [payload]
    first_brace = payload.find("{")
    first_bracket = payload.find("[")
    starts = [i for i in (first_brace, first_bracket) if i >= 0]
    if starts:
        start = min(starts)
        if start > 0:
            candidates.append(payload[start:])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            content = json.loads(candidate)
        except json.JSONDecodeError as exc:
            last_error = exc
            continue
        return validator.validate(content)
    raise OutputValidationError(
        f"provider output was not valid JSON (expected an object/array matching "
        f"the output schema): {last_error}"
    )
