"""The policy registry's contract — one row schema, one file per domain (#1763).

No network, no repository mutation: every fixture is staged under ``tmp_path``,
except the two assertions that read the REAL repository root to prove the
declared ``gdc`` and ``isolation`` domains are actually registered.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from governance.policy import registry
from governance.policy.registry import PolicyRegistry, PolicyRow

REPO_ROOT = Path(__file__).resolve().parents[3]

DOMAINS_RELATIVE = Path("governance") / "policy" / "domains"


def _stage_domain(root: Path, name: str, body: str) -> Path:
    """Write ``governance/policy/domains/<name>.yaml`` under a fixture root."""
    directory = root / DOMAINS_RELATIVE
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _rows_by_domain(root: Path) -> dict[str, PolicyRow]:
    return {row.domain: row for row in PolicyRegistry(root).rows()}


# -- the row schema -------------------------------------------------------- #

def test_row_carries_exactly_the_declared_four_fields():
    names = [field.name for field in dataclasses.fields(PolicyRow)]
    assert set(names) == {
        "domain",
        "source_file",
        "enforcement_point",
        "control_plane_visible",
    }
    assert len(names) == 4


def test_rows_are_policy_rows():
    rows = PolicyRegistry(REPO_ROOT).rows()
    assert rows, "the real repository declares at least the gdc and isolation domains"
    assert all(isinstance(row, PolicyRow) for row in rows)


# -- the real repository --------------------------------------------------- #

def test_real_repo_registers_gdc_and_isolation():
    """The two domains #1763's Done line requires are actually registered."""
    rows = _rows_by_domain(REPO_ROOT)
    assert "gdc" in rows
    assert "isolation" in rows

    gdc = rows["gdc"]
    assert gdc.source_file == "governance/conformance/policy.yaml"
    assert gdc.control_plane_visible is True

    isolation = rows["isolation"]
    assert isolation.control_plane_visible is True


# -- the honesty rule ------------------------------------------------------ #

def test_absent_source_file_is_still_emitted_and_names_the_missing_path(tmp_path):
    missing = "governance/nowhere/absent-policy.yaml"
    _stage_domain(
        tmp_path,
        "ghost",
        "domain: ghost\n"
        f"source_file: {missing}\n"
        'enforcement_point: "a gate that does not exist"\n'
        "control_plane_visible: true\n",
    )

    rows = _rows_by_domain(tmp_path)
    assert "ghost" in rows, "an absent source is never a silently dropped domain"
    row = rows["ghost"]
    assert row.control_plane_visible is False
    assert missing in row.enforcement_point


def test_undecodable_declaration_is_emitted_as_a_refusal_row(tmp_path):
    _stage_domain(tmp_path, "broken", "domain: broken\n  : : not valid yaml : :\n")

    row = _rows_by_domain(tmp_path)["broken"]
    assert row.control_plane_visible is False
    assert row.domain == "broken"
    assert "broken.yaml" in row.enforcement_point


def test_declaration_without_a_source_file_is_refused_by_name(tmp_path):
    _stage_domain(tmp_path, "vague", "domain: vague\ncontrol_plane_visible: true\n")

    row = _rows_by_domain(tmp_path)["vague"]
    assert row.control_plane_visible is False
    assert "source_file" in row.enforcement_point


def test_domain_key_disagreeing_with_the_filename_is_refused(tmp_path):
    source = tmp_path / "governance" / "real.yaml"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("declared: true\n", encoding="utf-8")
    _stage_domain(
        tmp_path,
        "actual",
        "domain: different\n"
        "source_file: governance/real.yaml\n"
        "control_plane_visible: true\n",
    )

    row = _rows_by_domain(tmp_path)["actual"]
    assert row.control_plane_visible is False
    assert "different" in row.enforcement_point


# -- the extension contract ------------------------------------------------ #

def test_one_new_file_registers_a_domain_with_no_code_change(tmp_path):
    """The contract every sibling lane (#1765-#1768) depends on."""
    source = tmp_path / "infra" / "cloudflare" / "ingress.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("# edge policy\n", encoding="utf-8")

    assert PolicyRegistry(tmp_path).rows() == [], "no domains staged yet"

    _stage_domain(
        tmp_path,
        "cloudflare",
        "domain: cloudflare\n"
        "source_file: infra/cloudflare/ingress.py\n"
        'enforcement_point: "deploy-time edge provisioning"\n'
        "control_plane_visible: true\n",
    )

    rows = _rows_by_domain(tmp_path)
    assert set(rows) == {"cloudflare"}
    row = rows["cloudflare"]
    assert row.source_file == "infra/cloudflare/ingress.py"
    assert row.enforcement_point == "deploy-time edge provisioning"
    assert row.control_plane_visible is True


def test_discovery_is_sorted_and_never_cached(tmp_path):
    source = tmp_path / "declared.yaml"
    source.write_text("x: 1\n", encoding="utf-8")
    body = (
        "domain: {name}\n"
        "source_file: declared.yaml\n"
        'enforcement_point: "the declaring gate"\n'
        "control_plane_visible: true\n"
    )
    _stage_domain(tmp_path, "zeta", body.format(name="zeta"))
    _stage_domain(tmp_path, "alpha", body.format(name="alpha"))

    registry_instance = PolicyRegistry(tmp_path)
    assert [row.domain for row in registry_instance.rows()] == ["alpha", "zeta"]

    # A new file appears on the NEXT call with no re-instantiation: nothing cached.
    _stage_domain(tmp_path, "mu", body.format(name="mu"))
    assert [row.domain for row in registry_instance.rows()] == ["alpha", "mu", "zeta"]


def test_visibility_is_fail_closed(tmp_path):
    source = tmp_path / "declared.yaml"
    source.write_text("x: 1\n", encoding="utf-8")
    _stage_domain(
        tmp_path,
        "silent",
        "domain: silent\n"
        "source_file: declared.yaml\n"
        'enforcement_point: "the declaring gate"\n'
        "control_plane_visible: nonsense\n",
    )

    assert _rows_by_domain(tmp_path)["silent"].control_plane_visible is False


# -- the alias ------------------------------------------------------------- #

def test_aggregate_is_the_rows_alias():
    instance = PolicyRegistry(REPO_ROOT)
    assert instance.aggregate() == instance.rows()
