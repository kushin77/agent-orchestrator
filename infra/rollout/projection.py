"""The projection from a promotion's live state to the declaration the portal serves (issue #967).

WHY THIS MODULE EXISTS. The ordered go-live ladder records a promotion in
``infra/rollout/live-state.yaml`` (the only committed file that may record a
stage above ``off``), while the console decides whether a surface answers from
``infra/feature-flags/registry.yaml``'s ``surfaces.<name>.default`` (plus the
``.rollout/surface-state.json`` rollback overlay the runtime maintains). Measured
before this module landed: nothing in the repo turned the first into the second -
``grep -rn 'live-state\\|live_state' --include='*.py' portal/`` found no reader,
and ``infra/rollout/checks/check_rollout.py::check_registry_parity`` asked only
whether a promoted flag *has* a registry row, never whether that row *reflects*
the promotion. So a completed, owner-approved go-live could leave the surface
dark until a human remembered to edit the declaration by hand: the last manual
step between a promotion and a served surface.

WHAT THIS MODULE IS. The declared coupling, in one place:

  * :func:`plan` answers the question nothing asked - for every promotion the
    live state records, which declaration does it owe, and does that declaration
    reflect it? An unprojected promotion is reported **by name**
    (``UNPROJECTED services.org_chart -> declaration surfaces.org_chart.default is
    'off'``), and a promotion that owes no served surface is reported by name too
    (with its reason), so the check is total rather than silently one-directional.
  * :func:`write_projection` applies the coupling **surgically**: it rewrites the
    one ``default:`` (and ``promoted:``) line of the named surface entry, leaving
    every comment, comment block and unrelated row byte-identical. Re-emitting the
    document through a YAML dumper would have destroyed the ~600 lines of recorded
    rationale this registry is made of, which is why the edit is line-targeted and
    *refuses* (never guesses) when it cannot locate exactly one such line.
  * ``python3 -m infra.rollout.projection --check`` is the reporting half;
    ``--write`` is the projecting half. ``scripts/check-rollout-projection.sh``
    runs both in the gate of record, with the negative controls.

WHY THE STEP IS *NAMED AND REVIEWED* RATHER THAN AN AMBIENT SIDE EFFECT. The
acceptance here is the manual step named as the projection and gated, not a
deploy that silently rewrites a reviewed IaC declaration: ``apply_path`` in the
registry declares the reviewed go-live as the only promotion route (GR-5), and
``e2e/go_live_delivery.py::project_registry`` already records the same reason for
keeping promotion and serving as two documents - "a reviewed IaC change is what
couples them (GR-5: never an ambient side effect of a deploy)". So the projector
removes the *hand editing*, not the review: it produces the exact one-line-per-
declaration diff an operator reviews and commits, and the gate refuses to let a
promotion stay unprojected. The PR is the audit record.

WHAT IS DELIBERATELY NOT PROJECTED. Only ``surfaces.<name>`` is written, because
that is the declaration the console reads and the only one whose value can move:

  * ``services.<name>.default`` must stay ``off`` and ``services.<name>.promoted``
    must stay false while it is off - ``scripts/check-feature-flags.py`` (the
    feature-flag gate in ``make verify``) fails either one, so a promotion may not
    be recorded there at all.
  * ``ci_cd.<name>.default`` must stay ``off`` for the same reason; arming a
    trigger is an out-of-band operational step (the registry's own note).
  * a promotion whose group/name has no ``surfaces.<name>`` partner owes no served
    declaration. That is reported by name with its reason, never skipped in
    silence - a one-directional check that ignores half its inputs is exactly the
    defect class this module exists to end.

Exit codes follow the repo's tri-state convention: ``0`` projected/clean, ``1`` a
finding (an unprojected promotion, or a declaration drift that breaks the join),
``2`` CANNOT-ASSESS (a declaration that could not be read or located - never
reported as clean).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import yaml
except ImportError as exc:  # pragma: no cover - the repo's accepted stack has it
    raise SystemExit(f"infra.rollout.projection requires PyYAML ({exc})") from exc

#: Repo root, from this file's own location (``<root>/infra/rollout/projection.py``).
REPO_ROOT = Path(__file__).resolve().parents[2]

REGISTRY_REL = Path("infra") / "feature-flags" / "registry.yaml"
LIVE_STATE_REL = Path("infra") / "rollout" / "live-state.yaml"

#: The registry section the console reads (``portal/server/fleet.py``).
SERVED_SECTION = "surfaces"
SERVED_ON = "on"
SERVED_OFF = "off"

#: The flag groups whose promotions own no ``surfaces`` row, with the reason
#: printed beside each one so an exemption is never indistinguishable from a gap.
NO_SERVED_SURFACE: Dict[str, str] = {
    "ci_cd": (
        "the ci_cd group arms a Cloud Build trigger out-of-band; its served declaration is that "
        "config's own _ENABLE_* substitution (registry ci_cd.*.default must stay off)"
    ),
    "rollout": (
        "rollout-native flags gate this lane's own ladder and own no registry row at all"
    ),
}

EXIT_OK = 0
EXIT_FINDING = 1
EXIT_CANNOT_ASSESS = 2

#: Values that mean OFF. PyYAML parses the bare scalar ``off`` as boolean False,
#: so both spellings must read as off (the same tolerance
#: ``scripts/check-feature-flags.py`` documents).
_OFF_STRINGS = frozenset({"off", "false", "", "no", "0", "none", "null"})


class ProjectionError(RuntimeError):
    """A declaration could not be read, or the projection could not be located.

    Raised only for CANNOT-ASSESS conditions. It is never raised for "already
    projected": that is a clean no-op, not a failure.
    """


def is_off(value: Any) -> bool:
    """True when ``value`` is not a promotion (the reader's own polarity)."""
    if value is None or value is False:
        return True
    if value is True:
        return False
    if isinstance(value, str):
        return value.strip().lower() in _OFF_STRINGS
    return True


def declares_on(entry: Any) -> bool:
    """True only when ``entry`` explicitly promotes the surface.

    The same predicate ``portal/server/fleet.py::declares_on`` applies, kept here
    so this module answers with the reader's rule rather than a second one.
    """
    if not isinstance(entry, Mapping):
        return False
    default = entry.get("default")
    return default is True or (
        isinstance(default, str) and default.strip().lower() == SERVED_ON
    )


def load_yaml_document(path: Path, *, what: str) -> Any:
    """Parse ``path`` or raise :class:`ProjectionError` (never a bare traceback)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectionError(f"cannot read the {what} at {path}: {exc}") from exc
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ProjectionError(f"the {what} at {path} does not parse: {exc}") from exc


def promoted_flags(live_state_doc: Any) -> Dict[str, str]:
    """``{flag: stage}`` for every live-state entry recorded above ``off``.

    An absent or empty document records nothing (``cli.py promote`` writes the
    entry; an empty ``flags: {}`` is the honest state before any promotion).
    """
    if live_state_doc is None:
        return {}
    if not isinstance(live_state_doc, Mapping):
        raise ProjectionError(
            "live-state must be a mapping (it carries a 'flags' mapping of entries)"
        )
    flags = live_state_doc.get("flags")
    if flags is None:
        return {}
    if not isinstance(flags, Mapping):
        raise ProjectionError("live-state 'flags' must be a mapping of flag entries")
    promoted: Dict[str, str] = {}
    for name, entry in flags.items():
        if not isinstance(entry, Mapping):
            raise ProjectionError(f"live-state entry '{name}' is not a mapping")
        stage = entry.get("stage")
        if is_off(stage):
            continue
        promoted[str(name)] = str(stage)
    return promoted


@dataclass(frozen=True)
class Finding:
    """A promotion whose served declaration does not reflect it."""

    flag: str
    stage: str
    surface: str
    declared: str
    registry_rel: str

    def line(self) -> str:
        return (
            f"UNPROJECTED {self.flag} -> declaration {SERVED_SECTION}.{self.surface}.default is "
            f"'{self.declared}' while live-state records stage '{self.stage}' "
            f"(project with: python3 -m infra.rollout.projection --write)"
        )


@dataclass
class Projection:
    """The plan: what the record owes, and whether each declaration reflects it."""

    promoted: Dict[str, str] = field(default_factory=dict)
    findings: List[Finding] = field(default_factory=list)
    no_partner: List[Tuple[str, str]] = field(default_factory=list)
    exempt: List[Tuple[str, str]] = field(default_factory=list)
    ahead: List[Tuple[str, str]] = field(default_factory=list)
    drift: List[str] = field(default_factory=list)
    live_state_rel: str = ""
    registry_rel: str = ""

    @property
    def ok(self) -> bool:
        """Clean when nothing is unprojected and no declaration drifts."""
        return not self.findings and not self.drift

    def render(self) -> List[str]:
        lines = [
            f"projection: {len(self.promoted)} promotion(s) recorded, "
            f"{len(self.findings)} unprojected, {len(self.no_partner)} with no served surface, "
            f"{len(self.exempt)} exempt"
        ]
        for flag, reason in sorted(self.exempt):
            lines.append(f"EXEMPT {flag} ({reason})")
        for flag, reason in sorted(self.no_partner):
            lines.append(f"NO-SERVED-SURFACE {flag} ({reason})")
        for finding in sorted(self.findings, key=lambda item: item.flag):
            lines.append(finding.line())
        for drift in sorted(self.drift):
            lines.append(drift)
        for surface, declared in sorted(self.ahead):
            lines.append(
                f"OBSERVATION {SERVED_SECTION}.{surface} declares default '{declared}' while no "
                "live-state entry records its promotion - a reviewed PR promoted it outside the "
                "ladder, which this gate reports rather than refuses (it gates the promotion -> "
                "declaration direction, issue #967)"
            )
        lines.append("projection: OK" if self.ok else "projection: FAILED")
        return lines

    def as_dict(self) -> Dict[str, Any]:
        return {
            "promoted": dict(sorted(self.promoted.items())),
            "findings": [
                {
                    "flag": finding.flag,
                    "stage": finding.stage,
                    "surface": finding.surface,
                    "declared": finding.declared,
                }
                for finding in sorted(self.findings, key=lambda item: item.flag)
            ],
            "noServedSurface": [{"flag": flag, "reason": reason} for flag, reason in sorted(self.no_partner)],
            "exempt": [{"flag": flag, "reason": reason} for flag, reason in sorted(self.exempt)],
            "observation": [{"surface": surface, "declared": declared} for surface, declared in sorted(self.ahead)],
            "drift": sorted(self.drift),
            "ok": self.ok,
            "liveState": self.live_state_rel,
            "registry": self.registry_rel,
        }


def _default_value(entry: Any) -> str:
    """The declared value in the registry's own vocabulary (``on`` / ``off``).

    ``default: on`` parses as boolean True under YAML 1.1 (the same artefact
    ``scripts/check-feature-flags.py`` documents for ``off``), so reporting the
    parsed boolean would name a value the file does not contain. Both spellings
    mean the same thing; the line an operator reads is the one reported here.
    """
    if not isinstance(entry, Mapping):
        return "<not a mapping>"
    value = entry.get("default")
    if value is None:
        return "<absent>"
    if value is True:
        return SERVED_ON
    if value is False:
        return SERVED_OFF
    return str(value)


def _tf_flag(entry: Any) -> Optional[str]:
    if not isinstance(entry, Mapping):
        return None
    value = entry.get("tf_flag")
    return str(value) if isinstance(value, str) and value else None


def surface_for_flag(flag: str) -> Tuple[str, str]:
    """Split ``<group>.<name>``; the served partner of ``<group>.<name>`` is ``<name>``.

    The join is by NAME - the workbook and ERP rows the registry declares are the
    same promotion unit on both sides (``services.erp_module`` <-> the surface the
    in-module switch ``surfaces.erp_module`` reads), which is what makes the
    service row's own ``enable_*`` and the surface row's switch one decision. It
    is deliberately not a ``tf_flag`` join: ``enable_portal`` is shared by seven
    surfaces, so joining on it would project the portal's promotion onto all of
    them (the same reason the e2e's projection keys on ``surfaces.<name>``).
    """
    group, _, name = flag.partition(".")
    return group, name


def plan(
    live_state_doc: Any,
    registry_doc: Any,
    *,
    live_state_rel: str = "",
    registry_rel: str = "",
) -> Projection:
    """Every promotion the record owes a declaration to, and whether it got it."""
    if not isinstance(registry_doc, Mapping):
        raise ProjectionError("the registry must be a mapping (it carries services/ci_cd/surfaces)")
    surfaces = registry_doc.get(SERVED_SECTION)
    if not isinstance(surfaces, Mapping):
        raise ProjectionError(
            f"the registry declares no '{SERVED_SECTION}' mapping - the console reads that section, "
            "so there is nothing to project into"
        )
    services = registry_doc.get("services")

    result = Projection(
        promoted=promoted_flags(live_state_doc),
        live_state_rel=live_state_rel,
        registry_rel=registry_rel,
    )

    for flag, stage in sorted(result.promoted.items()):
        group, name = surface_for_flag(flag)
        if group in NO_SERVED_SURFACE:
            result.exempt.append((flag, NO_SERVED_SURFACE[group]))
            continue
        if group not in ("services", SERVED_SECTION):
            result.no_partner.append(
                (
                    flag,
                    f"the '{group}' group declares no served-surface partner "
                    f"(this gate knows {SERVED_SECTION}.<name> and services.<name>)",
                )
            )
            continue
        entry = surfaces.get(name)
        if entry is None:
            result.no_partner.append(
                (flag, f"the registry declares no {SERVED_SECTION}.{name} for it")
            )
            continue
        if not declares_on(entry):
            result.findings.append(
                Finding(
                    flag=flag,
                    stage=stage,
                    surface=name,
                    declared=_default_value(entry),
                    registry_rel=registry_rel,
                )
            )
        if group == "services" and isinstance(services, Mapping):
            service_tf = _tf_flag(services.get(name))
            surface_tf = _tf_flag(entry)
            if service_tf and surface_tf and service_tf != surface_tf:
                result.drift.append(
                    f"DRIFT services.{name}.tf_flag '{service_tf}' != "
                    f"{SERVED_SECTION}.{name}.tf_flag '{surface_tf}' - the two rows are not the "
                    "same promotion unit, so promoting the service does not gate the surface"
                )

    for name, entry in sorted(surfaces.items()):
        if not declares_on(entry):
            continue
        if f"services.{name}" in result.promoted or f"{SERVED_SECTION}.{name}" in result.promoted:
            continue
        result.ahead.append((str(name), _default_value(entry)))

    return result


# --------------------------------------------------------------------------- #
# The write half: surgical, comment-preserving, fail-closed
# --------------------------------------------------------------------------- #

_TOP_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_.-]+):(?P<rest>.*)$")
_ENTRY_KEY_RE = re.compile(r"^  (?P<key>[A-Za-z0-9_.-]+):(?P<rest>\s*)$")
_FIELD_RE = re.compile(r"^    (?P<field>[A-Za-z0-9_]+):(?P<gap>\s*)(?P<value>\S+)(?P<tail>\s*)$")


def _entry_region(lines: Sequence[str], surface: str) -> Tuple[int, int]:
    """``[start, end)`` of ``surfaces.<surface>``'s lines, or raise.

    A region ends at the next entry key (indent 2) or the next top-level key
    (indent 0) - the shape the registry is written in - so a nested mapping
    inside an entry cannot be mistaken for the end of it.
    """
    starts = []
    for index, line in enumerate(lines):
        top = _TOP_KEY_RE.match(line.rstrip("\r\n"))
        if top is not None and top["key"] == SERVED_SECTION:
            starts.append(index)
    if not starts:
        raise ProjectionError(f"the registry declares no top-level '{SERVED_SECTION}:' section")
    if len(starts) > 1:
        raise ProjectionError(
            f"the registry declares '{SERVED_SECTION}:' {len(starts)} times - a duplicate section "
            "cannot be projected into unambiguously"
        )
    section = starts[0]

    found: List[int] = []
    index = section + 1
    while index < len(lines):
        raw = lines[index].rstrip("\r\n")
        if raw and not raw.lstrip().startswith("#"):
            top = _TOP_KEY_RE.match(raw)
            if top is not None and not raw.startswith(" "):
                break  # the next top-level section
            entry = _ENTRY_KEY_RE.match(raw)
            if entry is not None:
                if entry["key"] == surface:
                    found.append(index)
                elif found:
                    break  # the next entry ends the region
        index += 1
    if not found:
        raise ProjectionError(
            f"the registry declares no '{SERVED_SECTION}.{surface}' entry under a '{SERVED_SECTION}:' section"
        )
    if len(found) > 1:
        raise ProjectionError(
            f"'{SERVED_SECTION}.{surface}' is declared {len(found)} times - refusing to project into an ambiguous entry"
        )

    start = found[0]
    end = start + 1
    while end < len(lines):
        raw = lines[end].rstrip("\r\n")
        if raw and not raw.lstrip().startswith("#"):
            if not raw.startswith(" "):
                break
            if _ENTRY_KEY_RE.match(raw):
                break
        end += 1
    return start, end


def _flip_field(lines: List[str], start: int, end: int, surface: str, field_name: str, value: str) -> Optional[str]:
    """Rewrite ``<field>: <value>`` inside the region; return the change line."""
    hits = [
        index
        for index in range(start, end)
        if _FIELD_RE.match(lines[index].rstrip("\r\n"))
        and _FIELD_RE.match(lines[index].rstrip("\r\n"))["field"] == field_name
    ]
    if not hits:
        return None
    if len(hits) > 1:
        raise ProjectionError(
            f"'{SERVED_SECTION}.{surface}' declares '{field_name}' {len(hits)} times - refusing to "
            "guess which one is the declaration"
        )
    index = hits[0]
    line = lines[index]
    ending = line[len(line.rstrip("\r\n")) :]
    match = _FIELD_RE.match(line.rstrip("\r\n"))
    current = match["value"]
    if current == value:
        return None
    lines[index] = f"    {field_name}:{match['gap']}{value}{match['tail']}{ending}"
    return f"{SERVED_SECTION}.{surface}.{field_name}: '{current}' -> '{value}'"


def write_projection(registry_path: Path | str, projection: Projection) -> List[str]:
    """Apply the projection to ``registry_path``; return the changes made.

    Only the unprojected surfaces named by ``projection.findings`` are touched, and
    only their own ``default:`` (plus ``promoted:`` when the entry declares one)
    line. No finding means no write at all - the file is not rewritten with
    identical bytes, so a no-op project is provably a no-op. Everything else,
    comments included, is preserved byte-for-byte, which is asserted by the gate
    (``scripts/check-rollout-projection.sh``) rather than promised here.
    """
    if not projection.findings:
        return []
    registry_path = Path(registry_path)
    try:
        text = registry_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProjectionError(f"cannot read the registry at {registry_path}: {exc}") from exc
    lines = text.splitlines(keepends=True)
    changes: List[str] = []
    for finding in sorted(projection.findings, key=lambda item: item.surface):
        start, end = _entry_region(lines, finding.surface)
        flipped = _flip_field(lines, start, end, finding.surface, "default", SERVED_ON)
        if flipped is None:
            raise ProjectionError(
                f"'{SERVED_SECTION}.{finding.surface}' declares no 'default:' line to project into "
                f"(lines {start + 1}-{end}) - refusing to write a projection this run cannot locate"
            )
        changes.append(flipped)
        promoted = _flip_field(lines, start, end, finding.surface, "promoted", "true")
        if promoted:
            changes.append(promoted)
    registry_path.write_text("".join(lines), encoding="utf-8")
    return changes


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _resolve(root: Path, value: Optional[str], default_rel: Path) -> Path:
    if value:
        return Path(value)
    return Path(root) / default_rel


def build_projection(root: Path, live_state: Optional[str], registry: Optional[str]) -> Tuple[Projection, Path, Path]:
    live_path = _resolve(root, live_state, LIVE_STATE_REL)
    registry_path = _resolve(root, registry, REGISTRY_REL)
    registry_doc = load_yaml_document(registry_path, what="feature-flag registry")
    if live_path.is_file():
        live_doc = load_yaml_document(live_path, what="rollout live state")
    else:
        live_doc = {"flags": {}}
    projection = plan(
        live_doc,
        registry_doc,
        live_state_rel=str(live_path),
        registry_rel=str(registry_path),
    )
    return projection, live_path, registry_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m infra.rollout.projection",
        description=(
            "Project a promotion's live state into the served surface declaration the console "
            "reads (issue #967), and report an unprojected promotion by name."
        ),
    )
    parser.add_argument("--root", default=str(REPO_ROOT), help="repo root (or a sandbox declaring one)")
    parser.add_argument("--live-state", default=None, help="override infra/rollout/live-state.yaml")
    parser.add_argument("--registry", default=None, help="override infra/feature-flags/registry.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="report only (the default)")
    mode.add_argument("--write", action="store_true", help="apply the projection, surgically")
    parser.add_argument("--json", action="store_true", help="emit the plan as JSON instead of lines")
    args = parser.parse_args(argv)

    try:
        projection, _live_path, registry_path = build_projection(
            Path(args.root), args.live_state, args.registry
        )
        changes: List[str] = []
        if args.write:
            changes = write_projection(registry_path, projection)
            if changes:
                # Re-plan from what the file now declares, so the exit code reports
                # the state after the write rather than the state before it.
                projection, _live_path, registry_path = build_projection(
                    Path(args.root), args.live_state, args.registry
                )
    except ProjectionError as exc:
        print(f"projection: CANNOT-ASSESS {exc}")
        return EXIT_CANNOT_ASSESS

    if args.json:
        payload = projection.as_dict()
        payload["changes"] = changes
        print(json.dumps(payload, indent=2, sort_keys=False))
        return EXIT_OK if projection.ok else EXIT_FINDING

    for line in projection.render():
        print(line)
    if args.write:
        if changes:
            for change in changes:
                print(f"PROJECTED {change}")
            print(f"projection: wrote {len(changes)} declaration line(s) to {registry_path}")
        else:
            print(
                f"projection: already projected - 0 declaration line(s) to write, "
                f"{registry_path} unchanged"
            )
    return EXIT_OK if projection.ok else EXIT_FINDING


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
