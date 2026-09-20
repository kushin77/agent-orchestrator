#!/usr/bin/env python3
"""One declared ladder, proved by mutation (issue #1494, residual row R5 of #1458).

THE FAILURE THIS PINS
---------------------
The platform's model-tier ladder was re-declared as a literal in five modules
(`gateway/providers/contract.py`, `governance/authority/model.py`,
`identity/onboarding/model.py`, `integrations/hermes/mapping.py`,
`portal/server/chat.py`), a sixth shape in `gateway/limits/fingerprint.py` and a
seventh in `e2e/wiring.py`, and the FinOps tier names were restated in two more
(`registry/chat/labels.py`, `fleet/channel.py`). The copies AGREED — which is exactly
why nothing failed when one of them drifted. Two lists that agree today are not one
list, and this suite exists so that a rename in the authority is FOLLOWED rather
than merely agreed with.

HOW THIS PROVES ONE LADDER RATHER THAN COMPARING TWO
----------------------------------------------------
Not by asserting that two literals are equal — that passes for two lists that happen
to match, which is the state that failed. The authority is MUTATED on a fixture tree
(a rung is renamed) and the reader is re-read there: a reader with its own copy
cannot follow, so the mutation is what makes the assertion able to fail.
`test_a_consumer_that_keeps_its_own_copy_cannot_follow` drives the same comparison
with a module that reports a stale ladder, so the comparison's own failure path is
shown to exist (GR-12: a check that cannot fail is a formality).

The readers, and where each value comes from now:

| consumer | before | now |
|---|---|---|
| `gateway/providers/contract.py` (`TIERS`) | a literal four-tuple | `registry.profiles.tiers.authority()` |
| `gateway/limits/fingerprint.py` (`CANONICAL_TIERS`) | a literal frozenset | the authority |
| `governance/authority/model.py` (`TIERS`, `TIER_RANK`) | a literal tuple + its own rank map | the authority + `rank()` |
| `identity/onboarding/model.py` (`MODEL_TIERS`) | a literal four-tuple | the authority |
| `integrations/hermes/mapping.py` (`MODEL_TIERS`) | a literal four-tuple | the authority |
| `portal/server/chat.py` (`TIERS`) | a literal "pinned from the gateway" | the authority |
| `e2e/wiring.py` (the team configs' `tier_models`) | a comprehension over a literal | the authority |
| `registry/chat/labels.py` (`TIERS`) | a literal three-tuple (`flash`/`pro`/`auditor`) | `governance/finops/policy.json`, via `governance/finops/chooser.py` |
| `fleet/channel.py` (`MODEL_TIERS`) | a literal three-tuple | the same policy |

`governance/cto-overlay/overlay.py` declares a THIRD vocabulary — evidence tiers
(`experimental`/`standard`/`critical`) — which is a different concept and is out of
scope by name. `test_the_evidence_tiers_are_a_different_vocabulary` says so, so that
"out of scope" is asserted rather than assumed.

Run:  python3 -m pytest registry/profiles/tests -q
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILES_DIR = os.path.dirname(HERE)
REPO_ROOT = Path(PROFILES_DIR).parents[1]
CATALOG = os.path.join(PROFILES_DIR, "catalog.yaml")

for _extra in (str(REPO_ROOT), str(REPO_ROOT / "gateway"), str(REPO_ROOT / "fleet")):
    if _extra not in sys.path:
        sys.path.insert(0, _extra)


def _load_by_path(name: str, relative: str):
    """Load a module from this tree by its file, exactly as the check does."""
    spec = importlib.util.spec_from_file_location(name, str(REPO_ROOT / relative))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tiers = _load_by_path("tier_vocabulary_reader", "registry/profiles/tiers.py")

LADDER = ("LOW", "MED", "HIGH", "MAX")

#: (label, how to read it, the attribute it declares) — every site that used to
#: carry the ladder. `e2e/wiring.py` declares it inside a function rather than as a
#: module value, so it is covered by `scripts/check-tier-vocabulary.sh`'s scanner
#: (which refuses the literal) rather than by an attribute read here.
LADDER_CONSUMERS = (
    ("gateway/providers/contract.py", "providers.contract", "TIERS"),
    ("gateway/limits/fingerprint.py", "gateway/limits/fingerprint.py", "CANONICAL_TIERS"),
    ("governance/authority/model.py", "governance.authority.model", "TIERS"),
    ("identity/onboarding/model.py", "identity.onboarding.model", "MODEL_TIERS"),
    ("integrations/hermes/mapping.py", "integrations.hermes.mapping", "MODEL_TIERS"),
    ("portal/server/chat.py", "portal.server.chat", "TIERS"),
)

FINOPS_CONSUMERS = (
    ("registry/chat/labels.py", "registry/chat/labels.py", "TIERS"),
    ("fleet/channel.py", "fleet.channel", "MODEL_TIERS"),
)


def _read(target: str, attribute: str):
    """Read an attribute from a module named by a dotted name or a path."""
    if target.endswith(".py"):
        module = _load_by_path(f"consumer_{Path(target).stem}", target)
    else:
        module = importlib.import_module(target)
    return getattr(module, attribute)


def _mutated_catalog(path: Path, *, rename_from: str = "MAX", rename_to: str = "MAXQ") -> str:
    """The authority with one rung renamed — the mutation no copy can survive."""
    text = path.read_text(encoding="utf-8")
    mutated = text.replace(f"\n  {rename_from}:", f"\n  {rename_to}:", 1)
    assert mutated != text, "the mutation did not match the authority"
    path.write_text(mutated, encoding="utf-8")
    return mutated


def test_the_authority_is_the_catalog():
    """The reader answers the catalog's `tiers:` keys, in declaration order."""
    declared = tuple((yaml.safe_load(open(CATALOG, encoding="utf-8")).get("tiers") or {}).keys())
    assert declared == LADDER, f"the catalog declares {declared}"
    assert tiers.authority() == declared


def test_rank_is_the_declaration_order():
    """`rank()` is the ladder, cheapest first — not a second hand-kept ordering."""
    assert tiers.rank() == {tier: index for index, tier in enumerate(tiers.authority())}


def test_the_reader_follows_a_mutated_authority(tmp_path):
    """A renamed rung moves the reader: it READS the authority, it does not copy it."""
    tree = tmp_path / "mutant"
    (tree / "registry" / "profiles").mkdir(parents=True)
    shutil.copy(CATALOG, tree / "registry" / "profiles" / "catalog.yaml")
    shutil.copy(os.path.join(PROFILES_DIR, "tiers.py"),
                tree / "registry" / "profiles" / "tiers.py")
    assert tiers.authority(tree) == LADDER
    _mutated_catalog(tree / "registry" / "profiles" / "catalog.yaml")
    tiers.clear_cache()
    assert tiers.authority(tree) == ("LOW", "MED", "HIGH", "MAXQ")
    assert tiers.rank(tree)["MAXQ"] == 3
    tiers.clear_cache()


def test_a_consumer_that_keeps_its_own_copy_cannot_follow(tmp_path):
    """THE CONTROL: the comparison above can fail.

    A module carrying the old literal answers the old ladder no matter what the
    authority says — which is precisely the defect this issue removed, and the
    reason an equality between two literals proves nothing.
    """
    tree = tmp_path / "mutant"
    (tree / "registry" / "profiles").mkdir(parents=True)
    shutil.copy(CATALOG, tree / "registry" / "profiles" / "catalog.yaml")
    shutil.copy(os.path.join(PROFILES_DIR, "tiers.py"),
                tree / "registry" / "profiles" / "tiers.py")
    _mutated_catalog(tree / "registry" / "profiles" / "catalog.yaml")
    tiers.clear_cache()

    stale = tree / "stale_consumer.py"
    stale.write_text('TIERS = ("LOW", "MED", "HIGH", "MAX")\n', encoding="utf-8")
    copied = _load_by_path("stale_consumer", str(stale))
    told = tiers.authority(tree)
    assert told == ("LOW", "MED", "HIGH", "MAXQ"), told
    assert copied.TIERS != told, "a copy that still equals the authority proves nothing"
    tiers.clear_cache()


@pytest.mark.parametrize(
    "broken, needle",
    [
        ("absent", "cannot be read"),
        ("empty", "empty"),
        ("duplicate", "more than once"),
        ("not-a-mapping", "not a mapping"),
        ("no-tiers", "declares no 'tiers'"),
        ("not-yaml", "not valid YAML"),
    ],
)
def test_an_unreadable_authority_is_refused_by_name(tmp_path, broken, needle):
    """An authority that cannot be read is REFUSED, by name — never an empty ladder.

    An empty vocabulary is not a platform with no tiers: it is an unreadable
    authority, and every membership check downstream would judge by accident.
    """
    tree = tmp_path / broken
    (tree / "registry" / "profiles").mkdir(parents=True)
    path = tree / "registry" / "profiles" / "catalog.yaml"
    bodies = {
        "absent": None,
        "empty": "tiers: {}\n",
        "duplicate": "tiers:\n  LOW: {model: flash}\n  LOW: {model: pro}\n",
        "not-a-mapping": "tiers: [LOW, MED]\n",
        "no-tiers": "memoryScopes: {}\n",
        "not-yaml": "tiers: [\n",
    }
    if bodies[broken] is not None:
        path.write_text(bodies[broken], encoding="utf-8")
    tiers.clear_cache()
    with pytest.raises(tiers.TierVocabularyRefused) as refused:
        tiers.authority(tree)
    assert needle in str(refused.value), str(refused.value)
    tiers.clear_cache()


@pytest.mark.parametrize("source, target, attribute", LADDER_CONSUMERS)
def test_every_ladder_consumer_reads_the_authority(source, target, attribute):
    """Each site's value IS the authority's value — imported, not grepped."""
    expected = set(tiers.authority())
    got = set(_read(target, attribute))
    assert got == expected, f"{source} declares {sorted(got)}, the authority {sorted(expected)}"
    assert "registry.profiles.tiers" in (REPO_ROOT / source).read_text(encoding="utf-8"), (
        f"{source} does not name the reader — equal today is not the same as derived"
    )


def test_the_tier_rank_consumer_reads_the_authority():
    """`TIER_RANK` is `rank()` — not a second hand-kept ordering beside the ladder."""
    model = importlib.import_module("governance.authority.model")
    assert dict(model.TIER_RANK) == tiers.rank()


@pytest.mark.parametrize("source, target, attribute", FINOPS_CONSUMERS)
def test_every_finops_consumer_reads_the_policy(source, target, attribute):
    """The FinOps names come from `governance/finops/policy.json`, not from a copy."""
    chooser = importlib.import_module("governance.finops.chooser")
    declared = tuple(chooser.vocabulary(chooser.load_policy())[0])
    assert declared == ("flash", "pro", "auditor")
    assert tuple(_read(target, attribute)) == declared
    assert "governance.finops" in (REPO_ROOT / source).read_text(encoding="utf-8"), (
        f"{source} does not name the policy's reader"
    )


def test_the_evidence_tiers_are_a_different_vocabulary():
    """`governance/cto-overlay` is OUT OF SCOPE and stayed that way.

    Its tiers grade EVIDENCE, not routing cost. Folding them into the ladder would
    be the silent widening this issue's authority records as a boundary, so the
    non-relationship is asserted here rather than left to a comment.
    """
    overlay = _load_by_path("cto_overlay_probe", "governance/cto-overlay/overlay.py")
    assert overlay.TIERS == ("experimental", "standard", "critical")
    assert not set(overlay.TIERS) & set(tiers.authority())
