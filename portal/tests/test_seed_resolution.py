"""Deterministic seed resolution — the roster suite's order trap (issue #794).

`portal/tests/test_live_registry_telemetry.py` compares the console's roster
against the *seed the live registry resolves*. Which revision that is must not
depend on the order the seeds directory happens to hand back its entries:
`Path.glob` yields the filesystem (`scandir`) order, which differs between
checkouts of the **same commit**, so a selector built on it makes a correct
build go red in a long-lived worktree and green in a fresh one (#794 measured
the two verdicts; #642 hit the same trap).

Measured on this checkout: the live store yields `paperclip.1.0.0.yaml` first,
so a first-entry selector resolves the *older* revision while the console uses
`1.1.0` — the two revisions differ in `capabilitySet`, which is the assertion
that went red.

These tests drive the one resolver the loader and the roster suite share —
`portal.server.livestore.resolve_seed_path` — and they **provoke** the property
that matters instead of hoping for it: `scandir` order is *forced* to hand the
older revision first, and that order is asserted to be the hostile one, so a
selector that took the first, or the string-greatest, entry fails here. The
ordering is forced rather than produced by writing files in a chosen order
because this box's `scandir` order is **not** the write order — measured in the
temp directory the control uses, where writing `1.1.0` first still read back as
`1.0.0` first. A control that relied on the filesystem to be hostile would pass
on this box while proving nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from conftest import REPO_ROOT

import test_live_registry_telemetry as live_registry_suite
import test_purebliss_team as purebliss_suite
from portal.server.livestore import (
    RegistryDriftError,
    RegistrySnapshot,
    resolve_seed_path,
    revision_key,
    seed_paths,
    seed_selection_key,
)

_SEEDS = REPO_ROOT / "registry" / "profiles" / "seeds"

#: ``Path.glob`` as it really is, captured before any test wraps it — so a wrap
#: of a wrap can never delegate to a previous test's stand-in (which would sort
#: twice and quietly stop being the order under test).
_REAL_GLOB = Path.glob

#: The closed tier -> model ladder the registry declares, reduced to the four
#: ids the synthetic seeds below use (the real vocabulary is catalog.yaml).
_CATALOG_YAML = """
schemaVersion: 1
tiers:
  LOW:  { model: flash }
  MED:  { model: flash }
  HIGH: { model: pro }
  MAX:  { model: pro }
"""

#: revision -> (defaultModelTier, capabilitySet) for the synthetic seeds, so a
#: test can tell *which* revision a caller resolved by looking at the content.
_SYNTHETIC_REVISIONS = {
    "1.0.0": ("LOW", ("research",)),
    "1.1.0": ("HIGH", ("research", "module-brief")),
    "1.9.0": ("LOW", ("research",)),
    "1.10.0": ("HIGH", ("research", "module-brief")),
}


def _materialise(directory: Path, revisions: tuple[str, ...]) -> Path:
    """Write one ``paperclip.<revision>.yaml`` seed per revision, in that order.

    The write order is the parameter: two checkouts of identical content differ
    in nothing else, and on a filesystem whose `scandir` order follows creation
    order that alone decides what a first-entry selector sees.
    """
    directory.mkdir(parents=True, exist_ok=True)
    for revision in revisions:
        tier, capabilities = _SYNTHETIC_REVISIONS[revision]
        (directory / f"paperclip.{revision}.yaml").write_text(
            yaml.safe_dump(
                {
                    "id": "paperclip",
                    "version": revision,
                    "owner": "platform/purebliss",
                    "defaultModelTier": tier,
                    "capabilitySet": list(capabilities),
                },
                sort_keys=False,
            ),
            encoding="utf-8",
        )
    return directory


def _temp_repo_root(root: Path, revisions: tuple[str, ...]) -> Path:
    """A minimal checkout: ``registry/profiles/{catalog.yaml,seeds/}``."""
    profiles = root / "registry" / "profiles"
    _materialise(profiles / "seeds", revisions)
    (profiles / "catalog.yaml").write_text(_CATALOG_YAML, encoding="utf-8")
    return root


def _glob_in(revision_order: str):
    """A ``Path.glob`` stand-in that yields matches in the given revision order."""

    def _glob(self, pattern):
        return iter(
            sorted(
                _REAL_GLOB(self, pattern),
                key=seed_selection_key,
                reverse=revision_order == "highest-first",
            )
        )

    return _glob


# -- the premise -----------------------------------------------------------

def test_the_live_registry_publishes_more_than_one_revision_of_a_profile():
    """The choice the resolver makes is REAL, not a one-candidate formality."""
    published = seed_paths(_SEEDS, "paperclip.*.yaml")
    assert [path.name for path in published] == [
        "paperclip.1.0.0.yaml",
        "paperclip.1.1.0.yaml",
    ]
    first = yaml.safe_load(published[0].read_text(encoding="utf-8"))
    live = yaml.safe_load(resolve_seed_path("paperclip", _SEEDS).read_text(encoding="utf-8"))
    # The two revisions disagree on capabilities, so resolving the wrong one
    # really does change the roster assertion (it is what went red in #794).
    assert first["capabilitySet"] != live["capabilitySet"]


# -- the fix ---------------------------------------------------------------

def test_the_live_revision_is_the_highest_published_one():
    resolved = resolve_seed_path("paperclip", _SEEDS)
    assert resolved == _SEEDS / "paperclip.1.1.0.yaml"
    assert resolved == max(seed_paths(_SEEDS, "paperclip.*.yaml"), key=seed_selection_key)
    # And the console under test agrees: one rule, one answer.
    snapshot = RegistrySnapshot(REPO_ROOT)
    assert snapshot.profile("paperclip").version == "1.1.0"


def test_selection_ignores_the_order_the_directory_yields_entries(monkeypatch):
    """Order independence, provoked in both directions.

    Under 'lowest-first' the first entry IS the older revision — asserted, so
    the control cannot become vacuous — which is precisely what a checkout is
    free to produce and what `next(glob(...))` would resolve to. The answer
    must not move.
    """
    expected = _SEEDS / "paperclip.1.1.0.yaml"

    for revision_order in ("lowest-first", "highest-first"):
        monkeypatch.setattr(Path, "glob", _glob_in(revision_order))
        assert resolve_seed_path("paperclip", _SEEDS) == expected, revision_order

    monkeypatch.setattr(Path, "glob", _glob_in("lowest-first"))
    assert next(Path(_SEEDS).glob("paperclip.*.yaml")).name == "paperclip.1.0.0.yaml", (
        "CONTROL NOT ARMED: the order under test does not put the older revision "
        "first, so a first-entry selector would pass this test by luck"
    )
    assert resolve_seed_path("paperclip", _SEEDS) == expected


def test_the_same_seeds_written_in_opposite_order_resolve_alike(tmp_path):
    """Two directories, identical seeds, opposite WRITE order: one answer.

    This is the shape of the defect — the same commit checked out twice — and
    the answer must not move. On a filesystem that hands entries back in
    creation order, `next(glob(...))` picks opposite revisions in the two
    directories; see
    `test_selection_ignores_the_order_the_directory_yields_entries` for the
    forced-order form, which does not depend on the filesystem at all.
    """
    older_first = _materialise(tmp_path / "seeds-written-older-first", ("1.0.0", "1.1.0"))
    newer_first = _materialise(tmp_path / "seeds-written-newer-first", ("1.1.0", "1.0.0"))

    assert [path.name for path in seed_paths(older_first)] == [
        path.name for path in seed_paths(newer_first)
    ]
    assert {path.name: path.read_bytes() for path in older_first.glob("*.yaml")} == {
        path.name: path.read_bytes() for path in newer_first.glob("*.yaml")
    }
    assert resolve_seed_path("paperclip", older_first).name == "paperclip.1.1.0.yaml"
    assert resolve_seed_path("paperclip", newer_first).name == "paperclip.1.1.0.yaml"


def test_revision_order_is_semantic_not_the_string_order_of_the_filename(tmp_path):
    """``1.10.0`` outranks ``1.9.0``; as strings it is the other way round.

    A rule spelled ``sorted(glob(...))[-1]`` resolves ``1.9.0`` and publishes
    the older revision as live — armed below, on the filenames themselves.
    """
    assert max(["1.9.0", "1.10.0"]) == "1.9.0", (
        "CONTROL NOT ARMED: string order already prefers the newer revision here"
    )
    assert revision_key("1.10.0") > revision_key("1.9.0")

    seeds = _materialise(tmp_path / "seeds", ("1.9.0", "1.10.0"))
    assert sorted(path.name for path in seeds.glob("*.yaml"))[-1] == "paperclip.1.9.0.yaml", (
        "CONTROL NOT ARMED: filename order no longer picks the older revision"
    )
    assert seed_paths(seeds)[-1].name == "paperclip.1.10.0.yaml"
    assert resolve_seed_path("paperclip", seeds).name == "paperclip.1.10.0.yaml"


def test_the_loader_resolves_through_the_shared_rule(tmp_path, monkeypatch):
    """``RegistrySnapshot`` — the code under test — obeys the same rule.

    Asserted on the semver pair AND under a hostile entry order, so a loader
    that trusted string order or `scandir` order fails here rather than in a
    long-lived worktree.
    """
    root = _temp_repo_root(tmp_path / "repo", ("1.9.0", "1.10.0"))
    resolved = resolve_seed_path("paperclip", root / "registry" / "profiles" / "seeds")
    assert resolved.name == "paperclip.1.10.0.yaml"
    assert RegistrySnapshot(root).profile("paperclip").version == "1.10.0"

    monkeypatch.setattr(Path, "glob", _glob_in("lowest-first"))
    assert RegistrySnapshot(root).profile("paperclip").version == "1.10.0"


# -- one resolver, not two -------------------------------------------------

def test_the_roster_helpers_resolve_through_the_loader_resolver(tmp_path, monkeypatch):
    """Neither roster helper keeps a private copy of the rule.

    ``_seed`` and ``_registry_tier_model`` each restated it once (``next(glob())``,
    then ``sorted(glob())[-1]``) while the console used its own — the drift that
    made the roster suite compare the projection against the wrong seed. Pointed
    at synthetic seeds under the hostile order, both must still resolve the
    HIGHEST revision, so a re-inlined selector fails here.
    """
    seeds = _materialise(tmp_path / "seeds", ("1.0.0", "1.1.0"))
    catalog = tmp_path / "catalog.yaml"
    catalog.write_text(_CATALOG_YAML, encoding="utf-8")

    monkeypatch.setattr(Path, "glob", _glob_in("lowest-first"))
    assert next(Path(seeds).glob("paperclip.*.yaml")).name == "paperclip.1.0.0.yaml"
    monkeypatch.setattr(live_registry_suite, "_SEEDS", seeds)
    monkeypatch.setattr(purebliss_suite, "_SEEDS", seeds)
    monkeypatch.setattr(purebliss_suite, "_CATALOG", catalog)

    resolved = live_registry_suite._seed("paperclip")
    assert resolved["version"] == "1.1.0"
    assert resolved["capabilitySet"] == ["research", "module-brief"]
    # HIGH -> "pro" through the real ladder; 1.0.0 would have been LOW -> "flash".
    assert purebliss_suite._registry_tier_model("paperclip") == "pro"


# -- fail closed -----------------------------------------------------------

def test_an_unpublished_profile_is_refused_by_name(tmp_path):
    seeds = _materialise(tmp_path / "seeds", ("1.0.0",))
    with pytest.raises(RegistryDriftError, match="no-such-profile"):
        resolve_seed_path("no-such-profile", seeds)
