"""Typed-output validation for the gateway proxy (issue #16).

Request/response is TYPED via the resolved prompt module's ``outputSchema``
(issue #13 contract).  Validation is *consumed* from the merged provider
adapter layer (issue #15: ``providers.schema`` — JSON Schema, fail closed) and
wrapped into a verdict shape the dispatch loop uses to decide
retry-once-then-CANNOT-ASSESS.  There is no silent pass-through of invalid
output anywhere in the funnel: content that does not parse as JSON or does not
satisfy the schema is an explicit invalid-output attempt.

If ``jsonschema`` is unavailable the underlying validator refuses to operate
(``SchemaDefinitionError``), so the proxy never downgrades to a partial
hand-rolled checker.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from providers.errors import OutputValidationError, SchemaDefinitionError
from providers.schema import load_schema, parse_and_validate

#: (ok, content, error) — ``ok`` True only when content satisfies the schema.
Verdict = Tuple[bool, Any, str | None]


def load_output_schema(schema_src: Mapping[str, Any] | str) -> dict[str, Any]:
    """Normalize a prompt-module ``outputSchema`` to a JSON-Schema dict.

    Accepts a schema object or the path returned by the prompt registry's
    ``render_prompt`` (``outputSchema``).  Raises ``SchemaDefinitionError`` on
    a missing/unparseable file or a malformed schema (caller error).
    """
    return load_schema(schema_src)


def validate_typed_output(
    schema: Mapping[str, Any] | None, text: str
) -> Verdict:
    """Validate raw model text against the prompt module's output schema.

    Called by the dispatch loop as ``validator(schema, text)``.  Returns
    ``(ok, content, error)``; ``ok`` is False (never raises) when the output is
    unparseable or schema-invalid, so the dispatch loop can count invalid
    attempts and reach an explicit CANNOT-ASSESS.
    """
    if schema is None:
        return True, text, None
    try:
        content = parse_and_validate(text, schema)
        return True, content, None
    except (OutputValidationError, SchemaDefinitionError) as exc:
        return False, None, str(exc)
