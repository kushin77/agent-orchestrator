"""The projection's own controls (issue #967).

The gate of record for this coupling is ``scripts/check-rollout-projection.sh``
(it drives a genuine sandboxed promotion and asserts the reporting, the serving
and the refusal). These tests are the fast half: the mapping, the surgical write
and each refusal, plus the two facts about the COMMITTED tree that must hold at
landing - every recorded promotion is projected, and no service/surface pair
drifts apart.
"""

from __future__ import annotations

import os

import pytest
import yaml

from infra.rollout.projection import (
    ProjectionError,
    declares_on,
    plan,
    promoted_flags,
    write_projection,
)

SURFACE = "org_chart"
SERVICE_FLAG = "services.org_chart"
SURFACE_FLAG = "surfaces.org_chart"


def _registry(repo_root: str) -> dict:
    with open(os.path.join(repo_root, "infra", "feature-flags", "registry.yaml"), encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _live_state(repo_root: str) -> dict:
    path = os.path.join(repo_root, "infra", "rollout", "live-state.yaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _promotion(flag: str, stage: str = "canary") -> dict:
    return {
        "schema_version": 1,
        "flags": {
            flag: {
                "stage": stage,
                "from_stage": "off",
                "since": "2026-09-18T00:00:00Z",
                "audit_record": "audit/probe.md",
                "policy": "low-risk-auto-approve",
            }
        },
    }


# --------------------------------------------------------------------------- #
# 1. the reporting half
# --------------------------------------------------------------------------- #


def test_an_unprojected_promotion_is_reported_by_name(repo_root: str) -> None:
    """The gap itself: a promotion the declaration does not reflect must FAIL, by name."""
    projection = plan(_promotion(SERVICE_FLAG), _registry(repo_root))
    assert projection.ok is False, "an unprojected promotion must not read as clean"
    assert [finding.surface for finding in projection.findings] == [SURFACE]
    line = projection.render()[0]
    assert "1 unprojected" in line, line
    finding = projection.findings[0].line()
    assert finding.startswith(f"UNPROJECTED {SERVICE_FLAG} -> declaration surfaces.{SURFACE}.default"), finding
    assert "while live-state records stage 'canary'" in finding, finding


def test_a_projected_promotion_is_clean(repo_root: str) -> None:
    """The honest other half: once the declaration reflects it, nothing fires."""
    registry = _registry(repo_root)
    registry["surfaces"][SURFACE] = dict(registry["surfaces"][SURFACE], default="on", promoted=True)
    projection = plan(_promotion(SERVICE_FLAG), registry)
    assert projection.ok is True
    assert projection.findings == []
    assert "projection: OK" in projection.render()


def test_a_surfaces_flag_projects_onto_its_own_surface(repo_root: str) -> None:
    """``surfaces.<name>`` is the same join as ``services.<name>`` (the console's own key)."""
    projection = plan(_promotion(SURFACE_FLAG), _registry(repo_root))
    assert [finding.surface for finding in projection.findings] == [SURFACE]


def test_an_off_entry_owes_nothing(repo_root: str) -> None:
    """An entry at ``off`` is not a promotion (live-state omits it, or records it as off)."""
    document = _promotion(SERVICE_FLAG)
    document["flags"][SERVICE_FLAG]["stage"] = "off"
    projection = plan(document, _registry(repo_root))
    assert projection.promoted == {}
    assert projection.ok is True


def test_a_promotion_with_no_served_partner_is_named_not_skipped(repo_root: str) -> None:
    """An exemption is reported BY NAME with its reason - never silently skipped."""
    projection = plan(_promotion("ci_cd.verify_trigger"), _registry(repo_root))
    assert projection.ok is True, "a ci_cd promotion is an exemption, not a finding"
    assert [flag for flag, _reason in projection.exempt] == ["ci_cd.verify_trigger"]
    assert "EXEMPT ci_cd.verify_trigger (" in "\n".join(projection.render())


def test_a_service_with_no_surface_row_is_named_not_skipped(repo_root: str) -> None:
    """A service promotion whose surface does not exist is reported, with the reason."""
    projection = plan(_promotion("services.registry"), _registry(repo_root))
    assert projection.ok is True
    assert [flag for flag, _reason in projection.no_partner] == ["services.registry"]
    assert "NO-SERVED-SURFACE services.registry (" in "\n".join(projection.render())


def test_a_declared_on_surface_with_no_promotion_is_an_observation(repo_root: str) -> None:
    """The reverse direction is reported, not refused (it gates promotion -> declaration)."""
    projection = plan({"flags": {}}, _registry(repo_root))
    assert projection.ok is True
    assert "fleet_projection" in [surface for surface, _declared in projection.ahead]


def test_an_unreadable_declaration_is_cannot_assess(repo_root: str) -> None:
    """A registry with no ``surfaces`` section cannot be projected into: refuse, never green."""
    with pytest.raises(ProjectionError) as excinfo:
        plan({"flags": {}}, {"services": {}})
    assert "surfaces" in str(excinfo.value)


def test_a_non_mapping_live_state_is_refused() -> None:
    with pytest.raises(ProjectionError):
        promoted_flags(["not", "a", "mapping"])


# --------------------------------------------------------------------------- #
# 2. the write half
# --------------------------------------------------------------------------- #


def _sandbox_registry(repo_root: str, tmp_path) -> str:
    source = os.path.join(repo_root, "infra", "feature-flags", "registry.yaml")
    with open(source, encoding="utf-8") as fh:
        text = fh.read()
    target = tmp_path / "registry.yaml"
    target.write_text(text, encoding="utf-8")
    return str(target)


def test_the_write_is_surgical_and_comment_preserving(repo_root: str, tmp_path) -> None:
    """Only the named entry's own two lines move; the rest of the document is untouched."""
    path = _sandbox_registry(repo_root, tmp_path)
    original = open(path, encoding="utf-8").read()
    projection = plan(_promotion(SERVICE_FLAG), _registry(repo_root))
    changes = write_projection(path, projection)

    assert changes == [
        f"surfaces.{SURFACE}.default: 'off' -> 'on'",
        f"surfaces.{SURFACE}.promoted: 'false' -> 'true'",
    ], changes
    written = open(path, encoding="utf-8").read()
    before = original.splitlines()
    after = written.splitlines()
    assert len(after) == len(before), "the write changed the line count"
    assert written != original
    # Exactly the two declaration lines of THIS entry moved, and nothing else.
    # (Asserting on the indices rather than on a text replace: the document
    # already carries other entries whose `default: on` a replace would hit.)
    differing = [index for index, (was, now) in enumerate(zip(before, after)) if was != now]
    assert len(differing) == 2, [before[index] for index in differing]
    assert differing[0] + 1 == differing[1], "the two moved lines must be adjacent"
    assert before[differing[0]] == "    default: off" and after[differing[0]] == "    default: on"
    assert before[differing[1]] == "    promoted: false" and after[differing[1]] == "    promoted: true"
    assert before[differing[0] - 1] == f"  {SURFACE}:", before[differing[0] - 1]
    assert "code-enforced rather than advisory." in written, "the write dropped a comment"


def test_the_write_is_idempotent(repo_root: str, tmp_path) -> None:
    path = _sandbox_registry(repo_root, tmp_path)
    write_projection(path, plan(_promotion(SERVICE_FLAG), _registry(repo_root)))
    once = open(path, encoding="utf-8").read()
    again = write_projection(path, plan({"flags": {}}, yaml.safe_load(once)))
    assert again == [], "a second project must be a no-op"
    assert open(path, encoding="utf-8").read() == once


def test_the_write_refuses_when_the_default_line_is_absent(repo_root: str, tmp_path) -> None:
    """Fail closed: no locatable ``default:`` line means CANNOT-ASSESS, not a guess."""
    path = _sandbox_registry(repo_root, tmp_path)
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    out: list[str] = []
    inside_section = False
    inside_entry = False
    dropped = 0
    for line in lines:
        bare = line.rstrip("\r\n")
        if bare == "surfaces:":
            inside_section = True
        elif inside_section and bare and not bare.startswith(" ") and ":" in bare:
            inside_section = False
        elif inside_section and bare == f"  {SURFACE}:":
            inside_entry = True
        elif inside_entry and bare.startswith("  ") and not bare.startswith("   "):
            inside_entry = False
        if inside_entry and bare.startswith("    default:"):
            dropped += 1
            continue
        out.append(line)
    assert dropped == 1, "the mutation anchor moved: expected exactly one default line"
    open(path, "w", encoding="utf-8").write("".join(out))

    with pytest.raises(ProjectionError) as excinfo:
        write_projection(path, plan(_promotion(SERVICE_FLAG), _registry(repo_root)))
    assert f"surfaces.{SURFACE}" in str(excinfo.value)
    assert "default:" in str(excinfo.value)


def test_the_write_refuses_when_the_entry_is_absent(repo_root: str, tmp_path) -> None:
    path = _sandbox_registry(repo_root, tmp_path)
    document = yaml.safe_load(open(path, encoding="utf-8"))
    document["surfaces"] = {
        name: entry for name, entry in document["surfaces"].items() if name != SURFACE
    }
    open(path, "w", encoding="utf-8").write(yaml.safe_dump(document, sort_keys=False))

    with pytest.raises(ProjectionError):
        write_projection(path, plan(_promotion(SERVICE_FLAG), _registry(repo_root)))


# --------------------------------------------------------------------------- #
# 3. the served surface - proven through the console's own reader
# --------------------------------------------------------------------------- #


def test_the_projection_is_what_the_console_reads(repo_root: str, tmp_path) -> None:
    """The projected declaration turns the console's reader ON; the shipped one stays OFF."""
    import sys

    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from portal.server import fleet

    path = _sandbox_registry(repo_root, tmp_path)
    assert fleet.read_surface_default(tmp_path, registry_path=path, surface=SURFACE) == "off"
    write_projection(path, plan(_promotion(SERVICE_FLAG), _registry(repo_root)))
    assert fleet.read_surface_default(tmp_path, registry_path=path, surface=SURFACE) == "on"
    assert declares_on(yaml.safe_load(open(path, encoding="utf-8"))["surfaces"][SURFACE])


# --------------------------------------------------------------------------- #
# 4. the committed tree, at landing
# --------------------------------------------------------------------------- #


def test_the_committed_tree_is_projected(repo_root: str) -> None:
    """Nothing the live state records may be left unprojected in the committed tree."""
    projection = plan(_live_state(repo_root), _registry(repo_root))
    assert projection.ok is True, "\n".join(projection.render())
    assert projection.findings == []
    assert projection.drift == []


def test_no_declared_promotion_is_unaccounted_for(repo_root: str) -> None:
    """Every non-off live-state entry is classified: finding, no-partner or exempt."""
    projection = plan(_live_state(repo_root), _registry(repo_root))
    classified = (
        {finding.flag for finding in projection.findings}
        | {flag for flag, _reason in projection.no_partner}
        | {flag for flag, _reason in projection.exempt}
    )
    assert classified == set(projection.promoted), "\n".join(projection.render())
