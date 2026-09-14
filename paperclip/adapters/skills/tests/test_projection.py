"""The MCP surface is projected from ``gateway/mcp/``, never duplicated."""

from __future__ import annotations

import json
from pathlib import Path

from paperclip.adapters.skills import projection

PKG = Path("paperclip/adapters/skills")


def test_projection_findings_clean(repo_root: Path) -> None:
    assert projection.projection_findings(repo_root) == []


def test_callable_set_matches_the_authority(repo_root: Path) -> None:
    names = projection.callable_tools(repo_root)
    assert names == tuple(sorted(names))
    assert set(names) == set(projection.allowlist_vocabulary(repo_root))
    assert set(names) == set(projection.load_projection(repo_root).tools)


def test_projection_declares_the_tool_authority(repo_root: Path) -> None:
    view = projection.load_projection(repo_root)
    assert view.source_function == "gateway.mcp.tools.build_registry"
    assert view.derived_from == ("gateway/mcp/tools.py", "gateway/mcp/model.py")


def test_derivation_is_deterministic(repo_root: Path) -> None:
    first = projection.render_projection(projection.build_projection(repo_root))
    second = projection.render_projection(projection.build_projection(repo_root))
    assert first == second
    assert first == (repo_root / projection.PROJECTION_PATH).read_text(encoding="utf-8")


def test_callable_but_unprojected_tool_is_a_finding(scratch_root: Path) -> None:
    path = scratch_root / projection.PROJECTION_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    dropped = data["tools"].pop(0)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    findings = projection.projection_findings(scratch_root)
    assert any(dropped in finding for finding in findings)


def test_projected_but_uncallable_tool_is_a_finding(scratch_root: Path) -> None:
    path = scratch_root / projection.PROJECTION_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    data["tools"] = sorted(data["tools"] + ["platform.not-a-tool"])
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    findings = projection.projection_findings(scratch_root)
    assert any("platform.not-a-tool" in finding for finding in findings)


def test_changing_the_authority_makes_the_projection_stale(scratch_root: Path) -> None:
    tools_py = scratch_root / "gateway" / "mcp" / "tools.py"
    model_py = scratch_root / "gateway" / "mcp" / "model.py"
    anchor = '        "platform.whoami": _platform_whoami,\n'
    text = tools_py.read_text(encoding="utf-8")
    assert text.count(anchor) == 1
    tools_py.write_text(text.replace(anchor, "", 1), encoding="utf-8")
    anchor = '    "platform.whoami",\n'
    text = model_py.read_text(encoding="utf-8")
    assert text.count(anchor) == 1
    model_py.write_text(text.replace(anchor, "", 1), encoding="utf-8")

    findings = projection.projection_findings(scratch_root)
    assert any("platform.whoami" in finding for finding in findings)

    projection.write_projection(scratch_root)
    assert projection.projection_findings(scratch_root) == []
