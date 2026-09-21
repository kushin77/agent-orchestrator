"""portal.server.config_flags — the portal's own surface-flag reader (issue #642).

WHY this module exists rather than reusing ``portal.server.fleet``: that module
reads ``infra/feature-flags/registry.yaml``, whose ``surfaces:`` map is the
*control-plane service* registry. ``scripts/check-feature-flags.py`` keeps that
file in a strict 1:1 relation with ``infra/terraform/variables.tf`` (every
``enable_*`` variable has a ``services.<name>`` entry and vice versa), so a
portal *view* — which adds no service, no terraform variable and no deploy
target — cannot be declared there without either inventing a variable or
breaking the checker.

The three workbook-11 surfaces are therefore declared in the portal's own
``portal/config/feature-flags.yaml`` and read here. The same reasoning covers the
ERP module's portal surface (ERP-07, issue #652): it is a view inside the portal
service, and its own manifest (``integrations/erp/module.yaml``) ships no central
promotion row until the surface that becomes reachable lands. The contract is
exactly the fleet reader's, because the failure mode it guards against is the
same one:

* **fail closed.** A missing file, an unreadable file, invalid YAML, a document
  that is not a mapping, a missing ``surfaces`` section, a missing entry, or an
  entry whose ``default`` is anything but an explicit ``on``/``true`` all read
  as ``"off"``. Only an explicit promotion turns a surface on, so no surface can
  ship enabled because of a typo, a truncated write or an absent file.
* **the answer is a value, never an exception.** A caller asking whether a
  surface is on must get ``False`` for every failure, so a boot with a broken
  config serves a dark console instead of crashing — the same posture the
  control-plane registry reader takes.

``pyyaml`` is the repo's accepted stack (stdlib + PyYAML). A missing PyYAML is a
fail-closed ``"off"``, never an enabled surface.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

#: The flag declaration the portal reads at boot (repo-root relative).
CONFIG_RELATIVE = Path("portal") / "config" / "feature-flags.yaml"

#: Local-runtime-only override (issue #1771, `make portal-demo`): with this set
#: truthy every surface in ``DECLARED_SURFACES`` reads ``"on"`` regardless of
#: ``portal/config/feature-flags.yaml``. It never writes to, or reads a
#: substitute for, that file — production boots that never set the env var are
#: unaffected, and the committed defaults (GR-5) are untouched.
DEMO_OVERRIDE_ENV = "AO_PORTAL_DEMO"

#: Surface keys declared in ``portal/config/feature-flags.yaml`` (issue #642).
ORG_CHART_SURFACE = "org_chart"
SKILL_STUDIO_SURFACE = "skill_studio"
TASK_BOARD_SURFACE = "task_board"
#: The ERP module's portal surface (ERP-07, issue #652). Its in-module switch is
#: declared here rather than in the control-plane registry's ``services`` section
#: because it is a view inside the portal service that adds no service, no
#: terraform resource and no deploy target; the central row
#: (``services.erp_module`` / ``surfaces.erp_module`` in
#: ``infra/feature-flags/registry.yaml``) is the *promotion* record the module's
#: own manifest (``integrations/erp/module.yaml``) says lands with this surface,
#: and this key is the id that manifest declares (``erp-module``), underscored
#: exactly as ``infra/feature-flags/registry.yaml`` spells its own surface keys.
ERP_MODULE_SURFACE = "erp_module"
#: The fleet board (issue #880, EPIC #878 lane L1) — projects the live
#: ``.board/snapshot.json`` / ``.board/claims.jsonl`` roster the fleet
#: CLI/cron already treat as the source of truth, joined and schema-validated
#: by ``portal.server.livestore.load_board_rows``.
FLEET_BOARD_SURFACE = "fleet_board"
#: The cross-engine Sessions view (issue #1563) — joins `.fleet/`, `.board/`
#: and `.deepseek-agent/` into one operator row set.
SESSIONS_SURFACE = "sessions"

#: Every surface this module knows about, so a test can assert the set is closed.
DECLARED_SURFACES = frozenset(
    {
        ORG_CHART_SURFACE,
        SKILL_STUDIO_SURFACE,
        TASK_BOARD_SURFACE,
        ERP_MODULE_SURFACE,
        FLEET_BOARD_SURFACE,
        SESSIONS_SURFACE,
    }
)


def read_config_default(
    repo_root: Path | str,
    *,
    config_path: Optional[Path | str] = None,
    surface: str,
) -> str:
    """The declared default for ``surface`` — ``"on"`` or ``"off"``.

    Fails closed: every unreadable or non-conforming input reads as ``"off"``.
    Only ``default: on`` or ``default: true`` returns ``"on"``.
    """
    if surface in DECLARED_SURFACES and (os.environ.get(DEMO_OVERRIDE_ENV) or "").strip().lower() in (
        "1",
        "true",
        "on",
    ):
        return "on"
    path = (
        Path(config_path)
        if config_path is not None
        else Path(repo_root) / CONFIG_RELATIVE
    )
    try:
        import yaml
    except ImportError:
        return "off"
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, yaml.YAMLError):
        # `yaml.YAMLError` is NOT a `ValueError` — a malformed declaration
        # (`yaml.parser.ParserError`, measured) escaped this reader and left it
        # raising, which is the opposite of the contract above: a boot with a
        # broken config would crash instead of serving a dark console. The set is
        # the sibling reader's (`portal.server.fleet.read_registry_surfaces`),
        # which had it right; this reader is now its twin.
        return "off"
    if not isinstance(document, dict):
        return "off"
    surfaces = document.get("surfaces")
    if not isinstance(surfaces, dict):
        return "off"
    entry = surfaces.get(surface)
    if not isinstance(entry, dict):
        return "off"
    default = entry.get("default")
    if default is True or (
        isinstance(default, str) and default.strip().lower() == "on"
    ):
        return "on"
    return "off"


def surface_enabled(
    repo_root: Path | str,
    *,
    config_path: Optional[Path | str] = None,
    surface: str,
) -> bool:
    """True only when the portal config explicitly promotes ``surface``."""
    return (
        read_config_default(repo_root, config_path=config_path, surface=surface)
        == "on"
    )
