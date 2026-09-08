"""Pytest bootstrap + scratch-registry helpers for the persona library.

The modules under registry/personas/ are standalone scripts with no package
__init__.py (mirroring registry/profiles and registry/prompts). Inserting the
package directory at the front of sys.path lets the tests import them plainly
as ``registry`` and ``mapping``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PKG_DIR not in sys.path:
    sys.path.insert(0, PKG_DIR)

from registry import PersonaRegistry  # noqa: E402

# A minimal valid persona card used as the base for scratch-registry tests.
# Every override in card_yaml() is applied on top.
_BASE_CARD = {
    "id": "alpha",
    "version": "1.0.0",
    "tenant": "platform",
    "name": "Alpha",
    "summary": "scratch persona for tests",
    "expertise": ["verification", "testing"],
    "ownedLanes": ["verification"],
    "posture": "reviewer",
    "defaultModelTier": "LOW",
    "guardrailPolicyRef": "reviewer-bundle",
    "systemPromptRef": "alpha/primary@v1",
    "toolAllowlist": ["file_read", "shell_exec"],
    "capabilitySet": ["code-review"],
    "constraintSet": ["verify-before-done"],
    "memoryScope": ["session"],
    "provenance": ["example/provenance-source"],
}


def card_yaml(**overrides) -> str:
    """Render a persona-card YAML string from _BASE_CARD plus overrides."""
    data = {**_BASE_CARD, **overrides}
    return yaml.safe_dump(data, sort_keys=False)


@pytest.fixture
def scratch(tmp_path):
    """Factory for a scratch registry that never touches the committed ledger.

    Models the tenant model: a managed tenant cards dir plus an optional
    read-only platform library dir (platform defaults) composed at resolve time.
    """

    cards_dir = tmp_path / "tenant-cards"
    cards_dir.mkdir()
    platform_dir = tmp_path / "platform-cards"
    platform_dir.mkdir()
    manifest_path = tmp_path / "versions" / "manifest.yaml"

    class Scratch:
        @staticmethod
        def card_yaml(**overrides) -> str:
            """Render a persona-card YAML string (see module-level card_yaml)."""
            return card_yaml(**overrides)

        @staticmethod
        def write(cards, target="tenant") -> None:
            """cards: {filename.yaml: yaml_str}; target: 'tenant' | 'platform'."""
            base = cards_dir if target == "tenant" else platform_dir
            for name, content in cards.items():
                (base / name).write_text(content, encoding="utf-8")

        @staticmethod
        def registry(use_platform=False) -> PersonaRegistry:
            return PersonaRegistry(
                cards_dir=cards_dir,
                platform_dir=platform_dir if use_platform else None,
                manifest_path=manifest_path,
            )

        @staticmethod
        def write_registry(cards, use_platform=False) -> PersonaRegistry:
            Scratch.write(cards)
            return Scratch.registry(use_platform=use_platform)

    return Scratch
