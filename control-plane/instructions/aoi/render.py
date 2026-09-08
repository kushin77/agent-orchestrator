"""Per-tool mirror generator (issue #42).

Renders ONE canonical instruction source (+ an optional tenant override) into
deterministic, byte-stable per-tool mirrors — AGENTS.md, CLAUDE.md,
.cursorrules and copilot-instructions.md — plus a sha256 distribution
manifest.  Mirrors are always *generated*, never hand-forked: the renderer is
a pure function of its inputs (no timestamps, no randomness, stable ordering),
so regenerating over the same inputs reproduces the committed files
byte-for-byte (the not-hand-forked guarantee).

Every mirror carries the FULL rule set and the FULL precedence order of the
canonical source, so each harness (any model that reads AGENTS.md, Claude Code
reading CLAUDE.md, Cursor reading .cursorrules, Copilot reading
copilot-instructions.md) sees identical semantics — same task, same behaviour.

A machine-readable ledger comment is embedded at the end of every mirror so
the conformance suite can extract and cross-check the semantics each mirror
carries.
"""

from __future__ import annotations

import json

from .model import canonical_version
from .versioning import build_distribution_manifest

MIRROR_TARGETS = ("AGENTS.md", "CLAUDE.md", ".cursorrules", "copilot-instructions.md")

LEDGER_MARKER = "<!-- ao-instructions: "
LEDGER_SCHEMA = "ao.instructions.mirror/v1"

_LOCAL_LABEL = "Local tenant layer"

# Per-tool heading + intro.  The shared body (precedence + rules) below is
# identical across all four mirrors: only the harness-facing preamble and the
# ledger differ.
_HEADINGS = {
    "AGENTS.md": "# {title}",
    "CLAUDE.md": "# {title} — Claude Code mirror",
    ".cursorrules": "# {title} — Cursor mirror (terse)",
    "copilot-instructions.md": "# {title} — GitHub Copilot mirror",
}

_INTROS = {
    "AGENTS.md": (
        "Instructions for AI agents (and every harness) working in this repository. "
        "This file is a **generated mirror** of ONE canonical instruction source: the "
        "same rules and the same precedence are regenerated into CLAUDE.md, .cursorrules "
        "and copilot-instructions.md. Never edit a mirror by hand — edit the canonical "
        "source and regenerate."
    ),
    "CLAUDE.md": (
        "Claude Code working in this repository. This file is a **generated mirror** of "
        "the canonical instruction source: the same rules and precedence appear in "
        "AGENTS.md, .cursorrules and copilot-instructions.md. Never edit by hand — "
        "regenerate. This file never contradicts the canonical rules."
    ),
    ".cursorrules": (
        "Cursor working in this repository — terse generated mirror of the canonical "
        "instruction source. Same rules and precedence as AGENTS.md, CLAUDE.md and "
        "copilot-instructions.md. Never edit by hand — regenerate."
    ),
    "copilot-instructions.md": (
        "GitHub Copilot working in this repository. This file is a **generated mirror** "
        "of the canonical instruction source: the same rules and precedence appear in "
        "AGENTS.md, CLAUDE.md and .cursorrules. Never edit by hand — regenerate."
    ),
}


def _one_line(text: str) -> str:
    """Collapse a rule statement onto a single line (deterministic output)."""
    return " ".join(str(text).split())


def _precedence(canonical: dict, extra_rules: list[dict]) -> list[tuple[int, str, str, bool]]:
    """Ordered ``(rank, layer_id, label, managed)`` — most governing first."""
    items = []
    for index, layer in enumerate(canonical["layers"], start=1):
        items.append((index, layer["id"], layer["label"], layer["managed"]))
    if extra_rules:
        items.append((len(items) + 1, "local", _LOCAL_LABEL, False))
    return items


def _ordered_rules(canonical: dict, extra_rules: list[dict]) -> list[tuple[str, str, bool]]:
    """Ordered ``(layer_id, rule_id, rule_text, managed)`` semantics."""
    ordered = []
    for layer in canonical["layers"]:
        for rule in layer["rules"]:
            ordered.append((layer["id"], rule["id"], rule["text"], layer["managed"]))
    if extra_rules:
        for rule in extra_rules:
            ordered.append(("local", rule["id"], rule["text"], False))
    return ordered


def _ledger_line(canonical: dict, extra_rules: list[dict], tool: str) -> str:
    rule_ids = [rule_id for _, rule_id, _text, _managed in _ordered_rules(canonical, extra_rules)]
    precedence_ids = [layer_id for _rank, layer_id, _label, _managed in _precedence(canonical, extra_rules)]
    ledger = {
        "schema": LEDGER_SCHEMA,
        "canonical": {"id": canonical["id"], "version": canonical_version(canonical)},
        "tool": tool,
        "rules": rule_ids,
        "precedence": precedence_ids,
    }
    compact = json.dumps(ledger, separators=(",", ":"), ensure_ascii=True)
    return f"{LEDGER_MARKER}{compact} -->"


def _shared_body(canonical: dict, branding: dict | None, extra_rules: list[dict]) -> list[str]:
    """The precedence + rules sections, identical for every mirror target."""
    lines: list[str] = []
    lines.append("## Precedence")
    lines.append("")
    lines.append("When instructions conflict the highest-precedence rule wins; a rule in a")
    lines.append("lower-precedence layer never overrides a rule in a higher-precedence layer.")
    lines.append("")
    for rank, layer_id, label, managed in _precedence(canonical, extra_rules):
        suffix = "governed" if managed else "tenant-owned"
        lines.append(f"{rank}. {layer_id} — {label} ({suffix})")
    lines.append("")
    lines.append("## Rules")
    lines.append("")
    current_layer = None
    for layer_id, rule_id, text, managed in _ordered_rules(canonical, extra_rules):
        if layer_id != current_layer:
            if current_layer is not None:
                lines.append("")
            suffix = "governed" if managed else "tenant-owned"
            label = _LOCAL_LABEL if layer_id == "local" else _layer_label(canonical, layer_id)
            lines.append(f"### {layer_id} — {label} ({suffix})")
            lines.append("")
            current_layer = layer_id
        lines.append(f"- **{rule_id}** — {_one_line(text)}")
    lines.append("")
    lines.append("## Canonical source")
    lines.append("")
    lines.append(
        f"{canonical['id']}@{canonical_version(canonical)} — rendered from the "
        "agent-orchestrator model-agnostic instruction layer."
    )
    if branding and branding.get("repository"):
        subtitle = branding.get("subtitle")
        if subtitle:
            lines.append(f"Repository: {branding['repository']} — {subtitle}.")
        else:
            lines.append(f"Repository: {branding['repository']}.")
    return lines


def _layer_label(canonical: dict, layer_id: str) -> str:
    for layer in canonical["layers"]:
        if layer["id"] == layer_id:
            return layer["label"]
    return layer_id


def _render_tool(tool: str, canonical: dict, branding: dict | None,
                 extra_rules: list[dict]) -> str:
    lines: list[str] = []
    lines.append(_HEADINGS[tool].format(title=canonical["title"]))
    lines.append("")
    lines.append(_INTROS[tool])
    if canonical.get("summary"):
        lines.append("")
        lines.append(_one_line(canonical["summary"]))
    lines.append("")
    lines.extend(_shared_body(canonical, branding, extra_rules))
    lines.append("")
    lines.append(_ledger_line(canonical, extra_rules, tool))
    return "\n".join(lines) + "\n"


def render_all(canonical: dict, override: dict | None = None) -> dict[str, str]:
    """Render every per-tool mirror from a canonical source (+ optional override).

    Deterministic: the same inputs always produce byte-identical outputs.
    """
    from .override import apply_local_rules

    branding = None
    extra_rules: list[dict] = []
    if override is not None:
        extra_rules = apply_local_rules(override, canonical)
        branding = override.get("branding")
    return {
        tool: _render_tool(tool, canonical, branding, extra_rules)
        for tool in MIRROR_TARGETS
    }


def distribution_manifest(canonical: dict, override: dict | None = None) -> dict:
    """Deterministic sha256 distribution manifest over the rendered mirrors."""
    return build_distribution_manifest(
        canonical["id"], canonical_version(canonical), render_all(canonical, override)
    )
