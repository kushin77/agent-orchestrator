"""Live hermes head-agent registration sync (issue #889, lane L10).

Reads the real, committed seed profile + persona card off disk, validates
each against its schema + the live catalog (loading
``registry/profiles/validate.py`` and ``registry/personas/registry.py`` by
file path, mirroring ``registry/profiles/tests/test_agent_profiles.py`` and
``registry/personas/tests/conftest.py`` — those modules carry no
``__init__.py``), and appends one ``register`` event to the append-only event
log (``registry/events/event_log.py``) only when both validate.

No-false-green: a persona card missing the head-of-org identity fields
(``id`` / ``name``) is refused BY NAME before anything is appended, and a
profile/card that fails schema or catalog-membership validation is refused
with the validator's own error text — never silently registered.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

#: fields a head-of-org persona card must declare by name (issue #889).
HEAD_IDENTITY_FIELDS = ("id", "name")

DEFAULT_REGISTRY_ROOT = Path(__file__).resolve().parent.parent


class HeadRegistrationError(Exception):
    """Raised when the seed profile / persona card fails validation, or is
    missing a required head-of-org identity field."""


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def _load_yaml(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _validate_profile(registry_root: Path, agent_id: str, version: str) -> List[str]:
    profiles_dir = registry_root / "profiles"
    validate = _load_module(profiles_dir / "validate.py", "ao_registry_sync_profile_validate")
    schema = validate.load_json(str(profiles_dir / "agent-profile.schema.json"))
    catalog = validate.load_yaml(str(profiles_dir / "catalog.yaml"))
    seed_path = profiles_dir / "seeds" / f"{agent_id}.{version}.yaml"
    if not seed_path.is_file():
        raise HeadRegistrationError(f"no seed profile at {seed_path}")
    return validate.validate_seed_file(str(seed_path), schema, catalog)


def _validate_persona(registry_root: Path, agent_id: str) -> Optional[str]:
    personas_dir = registry_root / "personas"
    reg = _load_module(personas_dir / "registry.py", "ao_registry_sync_persona_registry")
    card_path = personas_dir / "cards" / f"{agent_id}.yaml"
    if not card_path.is_file():
        raise HeadRegistrationError(f"no persona card at {card_path}")
    card = _load_yaml(card_path)
    for field in HEAD_IDENTITY_FIELDS:
        value = card.get(field)
        if not value or not isinstance(value, str) or not value.strip():
            raise HeadRegistrationError(
                f"persona card {card_path.name} refuses head-of-org registration: "
                f"required identity field {field!r} is missing or empty"
            )
    try:
        reg.validate_card(card)
    except reg.InvalidCardError as exc:
        return str(exc)
    return None


def register_head_agent(
    registry_root: Path | str = DEFAULT_REGISTRY_ROOT,
    agent_id: str = "hermes",
    version: str = "1.0.0",
    event_log: Optional[Any] = None,
) -> Dict[str, Any]:
    """Validate + register the hermes head agent; append the ``register`` event.

    Raises :class:`HeadRegistrationError` naming the failure (missing head
    identity field, or the schema/catalog validation errors) and appends
    NOTHING to the event log when validation fails.
    """
    root = Path(registry_root)

    profile_errors = _validate_profile(root, agent_id, version)
    if profile_errors:
        raise HeadRegistrationError(
            f"seed profile {agent_id}.{version}.yaml failed validation: "
            + "; ".join(profile_errors)
        )

    persona_error = _validate_persona(root, agent_id)
    if persona_error:
        raise HeadRegistrationError(
            f"persona card {agent_id}.yaml failed validation: {persona_error}"
        )

    record = None
    if event_log is not None:
        record = event_log.append(
            "register",
            agent_id=agent_id,
            status="head-agent-registered",
            detail={"schema": "ao.registry.sync/head-agent-v1", "version": version},
        )

    return {
        "schema": "ao.registry.sync/head-agent-v1",
        "agent_id": agent_id,
        "version": version,
        "registered": True,
        "event": record,
    }
