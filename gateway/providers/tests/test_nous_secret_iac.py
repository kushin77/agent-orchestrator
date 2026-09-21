"""Nous credential IaC declaration (issue #1748): no value, off by default.

Covers the acceptance criteria that are not already exercised by
``scripts/check-secrets.sh`` (which scans the declaration file itself for a
literal) or ``terraform validate``: that the declaration carries no value,
that its reading constant matches the code that actually reads it
(``gateway/providers/nous.py``), and that its gate flag defaults OFF in
``infra/terraform/variables.tf``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
DECLARATION = REPO_ROOT / "infra" / "terraform" / "provider-credentials.json"
TF_VARIABLES = REPO_ROOT / "infra" / "terraform" / "variables.tf"


def _load():
    return json.loads(DECLARATION.read_text(encoding="utf-8"))


def _nous_entry(doc):
    for secret in doc["secrets"]:
        if secret["env"] == "NOUS_API_KEY":
            return secret
    raise AssertionError("no NOUS_API_KEY entry in the declaration")


def _findings(secret, code_constant):
    """The same rule the gate runs -- named findings, so a mutant is refused
    BY NAME rather than by a bare assert (mirrors check-secrets.sh's
    high-signal-shape / generic-assignment naming)."""
    findings = []
    if "value" in secret:
        findings.append("declared-value")
    if secret.get("code_constant") != code_constant:
        findings.append("constant-drift")
    return findings


def test_declaration_carries_no_value_and_constant_matches(monkeypatch):
    from providers import nous

    entry = _nous_entry(_load())

    # The unmodified twin: no findings.
    assert _findings(entry, "NOUS_API_KEY_ENV") == []
    assert nous.NOUS_API_KEY_ENV == entry["env"] == "NOUS_API_KEY"

    # MUTANT 1: a literal value rides in the declaration -- refused by name.
    with_value = {**entry, "value": "not-a-real-value-but-should-still-be-refused"}
    assert _findings(with_value, "NOUS_API_KEY_ENV") == ["declared-value"]

    # MUTANT 2: the declared constant drifts from the one the code defines --
    # refused by name (a vacuous check would accept any string here).
    drifted = {**entry, "code_constant": "NOUS_KEY_ENV_TYPO"}
    assert _findings(drifted, "NOUS_API_KEY_ENV") == ["constant-drift"]


def _gate_default(text, gate):
    match = re.search(rf'variable "{gate}" \{{.*?default\s*=\s*(\w+)', text, re.DOTALL)
    assert match, f"{gate!r} not found in the variables file"
    return match.group(1)


def test_gate_flag_defaults_off():
    entry = _nous_entry(_load())
    text = TF_VARIABLES.read_text(encoding="utf-8")

    # The unmodified twin: OFF.
    assert _gate_default(text, entry["gate"]) == "false"

    # MUTANT: the same reader against a flipped default -- must move.
    mutated = text.replace(
        f'variable "{entry["gate"]}" {{', f'variable "{entry["gate"]}" {{\n  default = true', 1
    )
    assert _gate_default(mutated, entry["gate"]) == "true"


def test_default_api_key_reads_the_declared_env_and_fails_closed(monkeypatch):
    from providers import nous

    monkeypatch.delenv("NOUS_API_KEY", raising=False)
    assert nous.default_api_key() is None

    monkeypatch.setenv("NOUS_API_KEY", "operator-supplied-value")
    assert nous.default_api_key() == "operator-supplied-value"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
