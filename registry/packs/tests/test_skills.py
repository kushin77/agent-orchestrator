"""Skill Studio backend tests — skill-level pack granularity (issue #640).

Covers the workbook-9 acceptance criteria:

* a ``skill`` is a FIRST-CLASS pack artifact type with its own closed category
  (schema + catalog vocabulary, code-native contents validation);
* the author -> test -> publish lifecycle, where **publish REQUIRES eval
  evidence** — refused when there is none, when it is unevaluated, and when it
  is not green (the verdict is the workbook-8 harness's, never a re-scored
  copy);
* skill-level install/upgrade/**rollback** with drift detection, and a catalog
  **searchable by skill**;
* the two named tests: publish refused without eval evidence, and rollback
  restores the previous skill version.

The mutation-control target is the eval-evidence gate in
``packs/skills.py`` (``_require_green_evidence``): disabling it makes
``test_publish_refused_without_eval_evidence`` fail.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import packhelpers  # noqa: E402

from packs.installer import Installer  # noqa: E402
from packs.pack_events import PackEventLog  # noqa: E402
from packs.registry import PackRegistry  # noqa: E402
from packs.skills import (  # noqa: E402
    SKILL_CATEGORIES,
    SKILL_LIFECYCLE_STATES,
    SkillAlreadyExistsError,
    SkillError,
    SkillLifecycleError,
    SkillNotFoundError,
    SkillPublishRefused,
    SkillStudio,
)


def make_env(cases_path=None):
    """Fixture: studio + registry + installer over a fresh temp content root."""
    private_pem, public_pem = packhelpers.keypair()
    reg = PackRegistry(event_log=PackEventLog(),
                       schema=packhelpers.load_schema(),
                       catalog=packhelpers.load_catalog())
    root = tempfile.mkdtemp(prefix="ao640-skill-")
    inst = Installer(reg, root, public_pem, event_log=reg.event_log)
    studio = SkillStudio(reg, inst, cases_path=cases_path)
    return private_pem, public_pem, reg, root, inst, studio


def published_skill(studio, skill_id="codemod", version="1.0.0",
                    category="code-authoring", cases_path=None):
    """Author -> test -> publish one green skill; returns the studio."""
    studio.author(skill_id, version, category,
                  content=packhelpers.skill_content(skill_id, version))
    studio.test(skill_id, version,
                path=cases_path or packhelpers.SKILL_CASES_PATH)
    studio.publish(skill_id, version)
    return studio


# ===========================================================================
# 1. A skill is a FIRST-CLASS artifact type with its own closed category
# ===========================================================================

def test_skill_is_a_first_class_artifact_type():
    """`skill` is in the closed artifact-type vocabulary (schema + catalog)."""
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    assert "skill" in schema["definitions"]["artifactType"]["enum"]
    assert "skill" in catalog["artifactTypes"]
    # ...and it has a contents slot of its own.
    assert "skill" in schema["properties"]["contents"]["properties"]


def test_skill_has_its_own_closed_category_vocabulary():
    """The Skill Studio category vocabulary is closed and non-empty."""
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    enum = set(schema["definitions"]["skillCategoryId"]["enum"])
    assert enum == set(catalog["skillCategories"].keys())
    assert enum == set(SKILL_CATEGORIES)
    assert len(enum) >= 4


def test_skill_lifecycle_vocabulary_matches_catalog():
    schema = packhelpers.load_schema()
    catalog = packhelpers.load_catalog()
    enum = set(schema["definitions"]["skillLifecycleState"]["enum"])
    assert enum == set(catalog["skillLifecycleStates"])
    assert enum == set(SKILL_LIFECYCLE_STATES)
    assert enum == {"draft", "tested", "published", "deprecated"}


def test_unknown_skill_category_refused_at_author():
    _, _, _, _, _, studio = make_env()
    try:
        studio.author("codemod", "1.0.0", "not-a-category")
        raise AssertionError("unknown skill category must be refused")
    except SkillError as exc:
        assert "closed Skill Studio category" in str(exc)


def test_skill_artifact_entry_shape_is_schema_valid():
    """A published skill projects to a schema-valid contents.skill entry."""
    import jsonschema
    schema = packhelpers.load_schema()
    entry = packhelpers.skill_entry()
    for field in ("ref", "skillId", "skillVersion", "skillCategory",
                  "skillLifecycle", "evalEvidence", "data", "sha256"):
        assert field in entry, field
    # Validate the entry through the schema's own skillEntry definition,
    # resolved inside the full schema so its internal $refs resolve.
    wrapper = dict(schema)
    wrapper["$ref"] = "#/definitions/skillEntry"
    jsonschema.Draft7Validator(wrapper).validate(entry)


# ===========================================================================
# 2. Author -> test -> publish; publish REQUIRES eval evidence
# ===========================================================================

def test_author_then_test_then_publish_advances_lifecycle():
    _, _, _, _, _, studio = make_env()
    studio.author("codemod", "1.0.0", "code-authoring")
    assert studio.get("codemod", "1.0.0").lifecycle == "draft"
    studio.test("codemod", "1.0.0", path=packhelpers.SKILL_CASES_PATH)
    assert studio.get("codemod", "1.0.0").lifecycle == "tested"
    studio.publish("codemod", "1.0.0")
    assert studio.get("codemod", "1.0.0").lifecycle == "published"
    assert studio.is_installable("codemod", "1.0.0")


def test_publish_refused_without_eval_evidence():
    """ACCEPTANCE: publish is refused when the skill carries no eval evidence.

    This is the mutation-control target: the gate is
    ``SkillStudio._require_green_evidence``.
    """
    _, _, _, _, _, studio = make_env()
    studio.author("codemod", "1.0.0", "code-authoring")
    # authored = draft, never tested -> no evidence exists at all
    assert studio.get("codemod", "1.0.0").eval_evidence is None
    try:
        studio.publish("codemod", "1.0.0")
        raise AssertionError(
            "publish must be refused when the skill has no eval evidence")
    except SkillLifecycleError as exc:
        assert "author -> test -> publish" in str(exc)
    assert studio.get("codemod", "1.0.0").lifecycle == "draft"
    assert studio.current_version("codemod") is None


def test_publish_refused_for_unevaluated_skill_with_zero_cases():
    """A skill whose evidence has zero cases is UNEVALUATED, not green."""
    _, _, _, _, _, studio = make_env()
    studio.author("orphan", "1.0.0", "analysis",
                  content=packhelpers.skill_content("orphan", "1.0.0"))
    # No eval case exists for `orphan@v1`, so the harness scores zero cases.
    evidence = studio.test("orphan", "1.0.0",
                           path=packhelpers.SKILL_CASES_PATH)
    assert evidence["cases"] == 0
    try:
        studio.publish("orphan", "1.0.0")
        raise AssertionError("unevaluated skill must not be publishable")
    except SkillPublishRefused as exc:
        assert "UNEVALUATED" in str(exc)
    assert studio.get("orphan", "1.0.0").lifecycle == "tested"
    assert not studio.is_installable("orphan", "1.0.0")


def test_publish_refused_for_failing_eval_evidence():
    """RED eval evidence (FP+FN) must refuse publish."""
    _, _, _, _, _, studio = make_env()
    studio.author("lint-fixer", "1.0.0", "code-review",
                  content=packhelpers.skill_content("lint-fixer", "1.0.0"))
    evidence = studio.test("lint-fixer", "1.0.0",
                           path=packhelpers.SKILL_CASES_PATH)
    assert evidence["failed"] == 1, evidence
    try:
        studio.publish("lint-fixer", "1.0.0")
        raise AssertionError("failing evidence must refuse publish")
    except SkillPublishRefused as exc:
        assert "not green" in str(exc) or "failed" in str(exc)
    assert not studio.is_installable("lint-fixer", "1.0.0")


def test_publish_refused_when_evidence_does_not_reproduce():
    """Hand-written green counts that the harness does not confirm fail closed.

    Evidence is re-derived through the workbook-8 harness, so a fabricated
    green block cannot be used to publish a skill whose real cases are red.
    """
    _, _, _, _, _, studio = make_env()
    studio.author("lint-fixer", "1.0.0", "code-review",
                  content=packhelpers.skill_content("lint-fixer", "1.0.0"))
    # Attach a hand-written "green" block without running the harness.
    studio.get("lint-fixer", "1.0.0").record_eval_evidence(
        packhelpers.skill_evidence(cases=5, passed=5, failed=0,
                                   eval_id="lint-fixer@1.0.0"))
    try:
        studio.publish("lint-fixer", "1.0.0")
        raise AssertionError(
            "fabricated green evidence must not survive the harness re-check")
    except SkillPublishRefused as exc:
        assert "lint-fixer@1.0.0" in str(exc)
    assert not studio.is_installable("lint-fixer", "1.0.0")


def test_publish_records_evidence_in_the_event_log():
    _, _, reg, _, _, studio = make_env()
    published_skill(studio)
    events = [e for e in reg.event_log.records()
              if e["event"] == "publish" and e["pack"] == "codemod"]
    assert events, "publish must append an event"
    assert events[-1]["detail"]["evalEvidence"]["failed"] == 0


def test_duplicate_skill_revision_is_immutable():
    _, _, _, _, _, studio = make_env()
    studio.author("codemod", "1.0.0", "code-authoring")
    try:
        studio.author("codemod", "1.0.0", "code-authoring")
        raise AssertionError("duplicate authored revision must be refused")
    except SkillAlreadyExistsError:
        pass


def test_unpublished_skill_cannot_be_bundled_into_a_pack():
    _, _, _, _, _, studio = make_env()
    studio.author("codemod", "1.0.0", "code-authoring")
    pack = {"id": "p", "version": "1.0.0", "contents": {}}
    try:
        studio.bundle_into_pack(pack, "codemod", "1.0.0")
        raise AssertionError("a draft skill must not reach a pack manifest")
    except SkillLifecycleError:
        pass


# ===========================================================================
# 3. Skill-level install / upgrade / rollback + drift detection
# ===========================================================================

def test_skill_install_is_skill_level_and_scoped():
    private_pem, _, reg, root, _, studio = make_env()
    published_skill(studio)
    # bundle the published skill into a signed pack, publish it, then install
    doc = packhelpers.skill_pack_doc(private_pem)
    reg.publish(doc)
    result = studio.install("acme", "codemod", "1.0.0")
    assert result["skillId"] == "codemod"
    assert result["skillVersion"] == "1.0.0"
    # the skill has its own tree, separate from the pack's own artifacts
    skill_path = os.path.join(root, "skills", "codemod", "1.0.0",
                              "codemod.skill")
    assert os.path.exists(skill_path)
    assert studio.active_version("acme", "codemod") == "1.0.0"
    # consumed from the harness evidence
    assert studio.get("codemod", "1.0.0").eval_evidence["failed"] == 0


def test_skill_install_refused_for_unpublished_skill():
    _, _, _, _, _, studio = make_env()
    studio.author("codemod", "1.0.0", "code-authoring")
    studio.test("codemod", "1.0.0", path=packhelpers.SKILL_CASES_PATH)
    try:
        studio.install("acme", "codemod", "1.0.0")
        raise AssertionError("a tested-but-unpublished skill must not install")
    except SkillLifecycleError as exc:
        assert "only published" in str(exc)


def test_skill_drift_detection_after_install():
    private_pem, _, reg, root, _, studio = make_env()
    published_skill(studio)
    reg.publish(packhelpers.skill_pack_doc(private_pem))
    studio.install("acme", "codemod", "1.0.0")
    assert studio.verify_installed("acme", "codemod")["ok"] is True
    # tamper with the materialized skill content
    path = os.path.join(root, "skills", "codemod", "1.0.0", "codemod.skill")
    with open(path, "ab") as fh:
        fh.write(b"\n# tampered\n")
    try:
        studio.verify_installed("acme", "codemod")
        raise AssertionError("tampered skill content must be detected")
    except SkillError as exc:
        assert "drift" in str(exc)


def test_upgrade_moves_to_the_new_published_version():
    private_pem, _, reg, root, _, studio = make_env()
    published_skill(studio, version="1.0.0")
    published_skill(studio, version="2.0.0")
    reg.publish(packhelpers.skill_pack_doc(private_pem, pack_id="worker-a",
                                           version="1.0.0",
                                           skill_version="1.0.0"))
    reg.publish(packhelpers.skill_pack_doc(private_pem, pack_id="worker-a",
                                           version="2.0.0",
                                           skill_version="2.0.0"))
    studio.install("acme", "codemod", "1.0.0")
    studio.upgrade("acme", "codemod", "2.0.0")
    assert studio.active_version("acme", "codemod") == "2.0.0"
    assert studio.previous_version("acme", "codemod") == "1.0.0"


def test_upgrade_refused_when_the_new_skill_is_not_bundled():
    """The signature gate is inherited: an unbundled skill cannot install.

    A skill's content is only trustworthy if a signed pack carries it, so a
    published skill that no pack bundles is refused rather than materialized
    unsigned — the fail-closed behaviour the pack installer already owns.
    """
    private_pem, _, reg, _, _, studio = make_env()
    published_skill(studio, version="1.0.0")
    published_skill(studio, version="2.0.0")
    reg.publish(packhelpers.skill_pack_doc(private_pem, pack_id="worker-a",
                                           skill_version="1.0.0"))
    studio.install("acme", "codemod", "1.0.0")
    try:
        studio.upgrade("acme", "codemod", "2.0.0")
        raise AssertionError("an unbundled skill must not be installable")
    except SkillError as exc:
        assert "not bundled by any published pack" in str(exc)
    # the previous version stays active — the upgrade never took effect
    assert studio.active_version("acme", "codemod") == "1.0.0"


def test_rollback_restores_the_previous_skill_version():
    """ACCEPTANCE: rollback restores the previous skill version."""
    private_pem, _, reg, root, _, studio = make_env()
    published_skill(studio, version="1.0.0")
    published_skill(studio, version="2.0.0")
    # the pack must carry the version being installed; publish a pack per rev
    reg.publish(packhelpers.skill_pack_doc(private_pem, pack_id="worker-a",
                                           skill_version="1.0.0"))
    # v2 is bundled by a second pack version so it is signature-verifiable too
    doc2 = packhelpers.skill_pack_doc(private_pem, pack_id="worker-a",
                                      version="2.0.0", skill_version="2.0.0")
    reg.publish(doc2)
    studio.install("acme", "codemod", "1.0.0")
    assert studio.active_version("acme", "codemod") == "1.0.0"

    studio.upgrade("acme", "codemod", "2.0.0")
    assert studio.active_version("acme", "codemod") == "2.0.0"

    result = studio.rollback("acme", "codemod")
    assert result == {"skillId": "codemod", "tenantId": "acme",
                      "from": "2.0.0", "to": "1.0.0"}
    # the ACTIVE version is the previous one again...
    assert studio.active_version("acme", "codemod") == "1.0.0"
    assert studio.previous_version("acme", "codemod") is None
    # ...the pointer file agrees...
    with open(os.path.join(root, "skills", "codemod", "active.acme")) as fh:
        assert fh.read().strip() == "1.0.0"
    # ...the restored content re-verifies against the v1 manifest hash...
    skill = studio.get("codemod", "1.0.0")
    path = os.path.join(root, "skills", "codemod", "1.0.0", "codemod.skill")
    assert os.path.exists(path)
    import hashlib
    with open(path, "rb") as fh:
        assert hashlib.sha256(fh.read()).hexdigest() == skill.content_sha256
    assert studio.verify_installed("acme", "codemod")["skillVersion"] == "1.0.0"
    # ...and the rollback is on the append-only log.
    kinds = [e["event"] for e in reg.event_log.records()]
    assert "rollback" in kinds


def test_rollback_refused_without_a_previous_version():
    private_pem, _, reg, _, _, studio = make_env()
    published_skill(studio, version="1.0.0")
    reg.publish(packhelpers.skill_pack_doc(private_pem, skill_version="1.0.0"))
    studio.install("acme", "codemod", "1.0.0")
    try:
        studio.rollback("acme", "codemod")
        raise AssertionError("rollback without a previous version must fail")
    except SkillError as exc:
        assert "no previous version" in str(exc)


# ===========================================================================
# 4. Catalog searchable by skill
# ===========================================================================

def test_catalog_is_searchable_by_skill_category():
    _, _, _, _, _, studio = make_env()
    published_skill(studio, skill_id="codemod", category="code-authoring")
    published_skill(studio, skill_id="reviewer", category="code-review")
    rows = studio.search_skills(category="code-authoring")
    assert [r["skillId"] for r in rows] == ["codemod"]
    assert rows[0]["skillCategory"] == "code-authoring"
    assert [r["skillId"] for r in studio.search_skills(category="code-review")
            ] == ["reviewer"]
    assert studio.search_skills(category="data") == []


def test_catalog_search_by_text_and_published_only():
    _, _, _, _, _, studio = make_env()
    published_skill(studio, skill_id="codemod")
    studio.author("sketchy", "1.0.0", "analysis",
                  description="not yet evaluated")
    assert [r["skillId"] for r in studio.search_skills(text="codem")
            ] == ["codemod"]
    # only the published skill shows under published_only
    assert [r["skillId"] for r in studio.search_skills(published_only=True)
            ] == ["codemod"]


def test_catalog_by_skill_projects_bundled_skills():
    private_pem, _, reg, _, _, studio = make_env()
    published_skill(studio)
    reg.publish(packhelpers.skill_pack_doc(private_pem))
    rows = studio.catalog_by_skill()
    assert len(rows) == 1
    assert rows[0]["skillId"] == "codemod"
    assert rows[0]["pack"] == "worker-platform"
    assert rows[0]["evalEvidence"]["failed"] == 0


def test_skill_categories_are_exposed_as_a_catalog_facet():
    _, _, _, _, _, studio = make_env()
    assert studio.skill_categories() == list(SKILL_CATEGORIES)


def test_unknown_skill_raises_not_found():
    _, _, _, _, _, studio = make_env()
    try:
        studio.get("nope", "1.0.0")
        raise AssertionError("unknown skill must raise")
    except SkillNotFoundError:
        pass
