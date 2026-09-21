#!/usr/bin/env python3
"""Skill Studio backend — skill-level pack granularity (issue #640, workbook-9).

---knowledge---
module_id: registry.packs.skills
system: registry
app: packs
solution_class: enterprise
patterns: [skill-granularity, lifecycle-state-machine, drift-detection, fail-closed]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SkillStudio, SkillArtifact, SkillPublishRefused, SkillLifecycleError, skill_artifact_entry]
invariants: "a skill is a first-class pack artifact type, on the same footing as a profile or a persona but narrower"
gotchas: ""
related: ["#640"]
do_not_duplicate: null
---knowledge---

A **skill** is a first-class pack artifact type, on the same footing as a
profile or a persona but *narrower*: it is one authored capability, versioned
and installed on its own. This module owns the skill lifecycle, the
skill-level install/upgrade/rollback path with drift detection, and the
skill-search surface of the catalog.

Author -> test -> publish (closed state machine)
------------------------------------------------

```
draft --(record eval evidence)--> tested --(publish)--> published --> deprecated
```

Two gates make the lifecycle real rather than declarative:

1. **The eval gate (publish).** ``publish`` refuses a skill that carries no
   eval evidence, whose evidence has zero cases (UNEVALUATED), or whose
   evidence is not green. The verdict is not re-derived here: the evidence is
   handed to the workbook-8 harness
   (``registry/prompts/evals.py``) and that harness's own
   :func:`evals.require_ok` decides, so there is no second scoring
   implementation to drift from it. A skill cannot be promoted to
   ``published`` on an unevaluated or failing basis.
2. **The signature + lifecycle gate (install).** Only ``published`` skills
   install, and the owning pack's attestation must verify (consumer trust,
   inherited from the pack installer).

``SkillStudio`` deliberately does not re-implement the pack install engine.
It composes :class:`packs.installer.Installer`, which already owns the
signature gate, content materialization + re-hash, post-install drift
detection and rollback. A skill is materialized into its own
``skills/<skillId>/<skillVersion>/`` tree under the install root so a
skill-level upgrade never disturbs the sibling skills of the same pack.

Usage (from the repo root)::

    import sys; sys.path.insert(0, "registry")
    from packs.registry import PackRegistry
    from packs.skills import SkillStudio

    studio = SkillStudio(PackRegistry(), Installer(...))
    studio.author("codemod", "1.0.0", "code-authoring")
    studio.test("codemod", "1.0.0", cases=[...])       # eval evidence
    studio.publish("codemod", "1.0.0")                 # refused w/o evidence
    studio.install("acme", "codemod", "1.0.0")
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import sys

from packs.registry import PackRegistryError

# ---------------------------------------------------------------------------
# Consume the workbook-8 eval harness (issue #639). Path-dependent because
# registry/prompts is a sibling package of registry/packs, not an installed
# module; the packs test bootstrap only puts registry/ on sys.path.
# ---------------------------------------------------------------------------
_PROMPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "prompts")
if _PROMPTS_DIR not in sys.path:
    sys.path.insert(0, _PROMPTS_DIR)
if os.path.dirname(_PROMPTS_DIR) not in sys.path:
    sys.path.insert(0, os.path.dirname(_PROMPTS_DIR))

try:  # pragma: no cover - guarded below, fail closed when unavailable
    import evals as _evals  # type: ignore
except Exception:  # pragma: no cover - exercised by the fail-closed test
    _evals = None  # type: ignore[assignment]


EVAL_HARNESS = "registry/prompts/evals.py"

SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# Closed Skill Studio category vocabulary (mirrors pack-catalog.yaml
# skillCategories; the validate.py parity gate enforces the mirror).
SKILL_CATEGORIES = (
    "code-authoring",
    "code-review",
    "testing",
    "analysis",
    "data",
    "operations",
    "security",
    "communication",
)

# Closed skill lifecycle vocabulary + legal transitions (pack-catalog.yaml
# skillLifecycleStates, mirror enforced by validate.py).
SKILL_LIFECYCLE_STATES = ("draft", "tested", "published", "deprecated")
SKILL_TRANSITIONS = {
    "draft": {"tested"},
    "tested": {"published"},
    "published": {"deprecated"},
    "deprecated": set(),  # terminal
}

# Only a published skill revision is installable.
INSTALLABLE_SKILL_STATES = ("published",)

# Pack lifecycle states in which a pack's skills may be installed.
INSTALLABLE_PACK_STATES = ("live",)


class SkillError(PackRegistryError):
    """Base Skill Studio error."""


class SkillPublishRefused(SkillError):
    """Publish refused: the skill has no eval evidence, or it is not green."""


class SkillLifecycleError(SkillError):
    """Illegal or terminal skill lifecycle transition."""


class SkillNotFoundError(SkillError):
    """No such skill (id / version)."""


class SkillAlreadyExistsError(SkillError):
    """A skill (id, version) is already authored (immutable)."""


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


class SkillArtifact:
    """One skill revision — a first-class pack artifact.

    A skill is not a whole pack: it is a single authored capability with its
    own id, its own SemVer, its own closed Skill Studio category and its own
    author -> test -> publish lifecycle. ``eval_evidence`` is the workbook-8
    evidence block; ``publish`` refuses to freeze a revision without green
    evidence.
    """

    def __init__(self, skill_id, version, category, content=None,
                 description=None, tags=None):
        if not SKILL_ID_RE.match(skill_id or ""):
            raise SkillError("skillId '%s' must match ^[a-z][a-z0-9-]*$"
                             % skill_id)
        if not VERSION_RE.match(version or ""):
            raise SkillError("skillVersion '%s' must be semantic X.Y.Z"
                             % version)
        if category not in SKILL_CATEGORIES:
            raise SkillError(
                "skillCategory '%s' is not a closed Skill Studio category "
                "(valid: %s)" % (category, ", ".join(SKILL_CATEGORIES)))
        self.skill_id = skill_id
        self.version = version
        self.category = category
        self.description = description
        self.tags = list(tags or [])
        self._content = None if content is None else bytes(content)
        self._lifecycle = "draft"          # author
        self._eval_evidence = None         # set by test()
        self._revisions = []               # deprecated predecessor versions

    # -- author -> test -> publish -----------------------------------------
    @property
    def lifecycle(self):
        return self._lifecycle

    @property
    def eval_evidence(self):
        return self._eval_evidence

    @property
    def content(self):
        return self._content

    @property
    def content_sha256(self):
        if self._content is None:
            return None
        return sha256_hex(self._content)

    @property
    def eval_id(self):
        """The eval id this skill revision is scored under.

        Skill evals live in the workbook-8 cases file under
        ``<skillId>@<version>``, exactly as a prompt module's evals live under
        ``<taskType>@<version>``. The harness's own ``EvalCase.prompt_id`` is
        the join key, so the skill gate needs no second lookup table — and a
        new skill revision needs its own cases before it can be published.
        """
        return "%s@%s" % (self.skill_id, self.version)

    def record_eval_evidence(self, evidence):
        """Attach eval evidence and advance draft -> tested.

        Evidence is the workbook-8 shape (see :meth:`SkillStudio.test`). This
        is the *author -> test* edge; it never makes a skill publishable on
        its own — ``publish`` re-applies the gate.
        """
        self._require_state("draft", "record eval evidence")
        self._eval_evidence = evidence
        self._lifecycle = "tested"
        return self._lifecycle

    def mark_published(self):
        self._require_state("tested", "publish")
        self._lifecycle = "published"
        return self._lifecycle

    def deprecate(self):
        self._require_state("published", "deprecate")
        self._lifecycle = "deprecated"
        return self._lifecycle

    def _require_state(self, expected, action):
        if self._lifecycle != expected:
            raise SkillLifecycleError(
                "cannot %s skill %s %s: lifecycle is '%s' (expected '%s')"
                % (action, self.skill_id, self.version, self._lifecycle,
                   expected))

    # -- pack artifact projection -------------------------------------------
    def to_artifact_entry(self):
        """Project this skill into a pack ``contents.skill`` entry.

        The entry is self-contained (base64 ``data`` + pinned ``sha256``) and
        carries the skill's own identity, category, lifecycle and eval
        evidence, so a pack can bundle skills the catalog can search and the
        installer can materialize. Refused unless the skill is published:
        an unpublished skill must never reach a pack manifest.
        """
        if self._lifecycle != "published":
            raise SkillLifecycleError(
                "skill %s %s is '%s'; only a published skill can be bundled "
                "into a pack" % (self.skill_id, self.version, self._lifecycle))
        if self._content is None:
            raise SkillError("skill %s %s has no content to bundle"
                             % (self.skill_id, self.version))
        entry = {
            "ref": "%s@%s" % (self.skill_id, self.version),
            "skillId": self.skill_id,
            "skillVersion": self.version,
            "skillCategory": self.category,
            "skillLifecycle": self._lifecycle,
            "evalEvidence": dict(self._eval_evidence or {}),
            "data": base64.b64encode(self._content).decode("ascii"),
            "sha256": sha256_hex(self._content),
        }
        if self.description:
            entry["description"] = self.description
        if self.tags:
            entry["tags"] = list(self.tags)
        return entry

    def to_dict(self):
        return {
            "skillId": self.skill_id,
            "skillVersion": self.version,
            "skillCategory": self.category,
            "skillLifecycle": self._lifecycle,
            "evalEvidence": dict(self._eval_evidence or {}),
            "contentSha256": self.content_sha256,
        }


class SkillStudio:
    """Skill-level author/test/publish + install/upgrade/rollback + search.

    Composes the pack primitives rather than replacing them: the registry
    (:class:`packs.registry.PackRegistry`) owns the pack catalog and
    consumption ledger, the :class:`packs.installer.Installer` owns the
    signature/content/drift/rollback engine. This class owns the *skill*
    dimension on top of both.
    """

    def __init__(self, registry, installer=None, cases_path=None):
        self._registry = registry
        self._installer = installer
        # {skill_id: {version: SkillArtifact}}
        self._skills = {}
        # {skill_id: lifecycle of the newest published revision}
        self._current = {}
        # cases file for the eval gate; None = the harness default
        self._cases_path = cases_path

    @property
    def registry(self):
        return self._registry

    # -- author -> test -> publish ------------------------------------------
    def author(self, skill_id, version, category, content=None,
               description=None, tags=None):
        """Author a skill revision: register it in ``draft``.

        Authoring never publishes and never installs anything — it is the
        first edge of the lifecycle. A duplicate (skillId, skillVersion) is
        refused: an authored revision is immutable.
        """
        if content is None:
            content = ("skill: %s\nversion: %s\ncategory: %s\n"
                       % (skill_id, version, category)).encode("utf-8")
        skill = SkillArtifact(skill_id, version, category, content=content,
                              description=description, tags=tags)
        versions = self._skills.setdefault(skill_id, {})
        if version in versions:
            raise SkillAlreadyExistsError(
                "skill %s %s is already authored (immutable)"
                % (skill_id, version))
        versions[version] = skill
        return skill

    def test(self, skill_id, version, cases=None, path=None):
        """Test a skill: run the workbook-8 eval gate and record the evidence.

        The verdict is the harness's own
        (:func:`evals.Evaluate` over the cases), never a second
        implementation. Returns the evidence block that ``publish`` will
        re-check. Evidence is recorded even when RED — the refusal happens at
        publish, and recording the failing evidence is what makes the refusal
        diagnosable.

        The cases file used here is remembered on the studio, so the publish
        gate re-derives its verdict from the SAME cases this test scored
        against (a scratch tree's cases gate its own publishes, never the
        tracked checkout's).
        """
        skill = self.get(skill_id, version)
        cases_path = path if path is not None else self._cases_path
        if cases_path is not None:
            self._cases_path = cases_path
        evidence = self._eval_evidence_for(skill, cases=cases, path=cases_path)
        skill.record_eval_evidence(evidence)
        return evidence

    def _eval_evidence_for(self, skill, cases=None, path=None):
        """Score one skill revision through the workbook-8 eval harness."""
        if _evals is None:  # pragma: no cover - exercised by fail-closed test
            raise SkillPublishRefused(
                "regression-eval gate unavailable: %s could not be imported, "
                "refusing to score skill %s %s without it (fail closed)"
                % (EVAL_HARNESS, skill.skill_id, skill.version))
        eval_id = skill.eval_id
        all_cases = list(cases) if cases is not None else \
            _evals.load_cases(path) if path is not None else _evals.load_cases()
        report = _evals.evaluate(all_cases).get(eval_id)
        if report is None:
            # A skill with no cases of its own is UNEVALUATED. Declared here
            # rather than silently green: cases=0 is what publish refuses.
            return {"harness": EVAL_HARNESS, "evalId": eval_id, "cases": 0,
                    "passed": 0, "failed": 0}
        return {
            "harness": EVAL_HARNESS,
            "evalId": eval_id,
            "cases": report.total,
            "passed": report.passed,
            "failed": report.failed,
        }

    def publish(self, skill_id, version, actor=None):
        """Publish a tested skill — REFUSED without green eval evidence.

        This is the gate the acceptance criteria name. Publish is refused
        when the skill:

        * is not in the ``tested`` state (never tested, or already published);
        * carries no eval evidence at all;
        * carries evidence with zero cases (UNEVALUATED — an unevaluated
          skill is never publishable); or
        * carries evidence with any failing case.

        The evidence is re-derived through the workbook-8 harness
        (:func:`evals.require_ok`) so the gate cannot be satisfied by a
        hand-written evidence block that no harness ever produced. Returns the
        lifecycle state on success.
        """
        skill = self.get(skill_id, version)
        if skill.lifecycle != "tested":
            raise SkillLifecycleError(
                "cannot publish skill %s %s: lifecycle is '%s' (author -> test "
                "-> publish requires 'tested')"
                % (skill_id, version, skill.lifecycle))
        evidence = skill.eval_evidence or {}
        self._require_green_evidence(skill, evidence)
        skill.mark_published()
        self._current[skill_id] = version
        self._emit("publish", "published", skill_id, version,
                   actor=actor, detail={"evalEvidence": evidence})
        return skill.lifecycle

    def _require_green_evidence(self, skill, evidence):
        """The publish gate: evidence must exist, be evaluated and be green.

        Fails closed. The last line of defence re-runs the workbook-8 harness
        over the skill's own eval id, so evidence whose counts do not
        correspond to a real harness verdict is still refused.
        """
        if not isinstance(evidence, dict) or not evidence:
            raise SkillPublishRefused(
                "publish refused for skill %s %s: no eval evidence — a skill "
                "cannot be published without evidence from %s (author -> test "
                "-> publish)" % (skill.skill_id, skill.version, EVAL_HARNESS))
        missing = [f for f in ("harness", "evalId", "cases", "passed",
                               "failed") if f not in evidence]
        if missing:
            raise SkillPublishRefused(
                "publish refused for skill %s %s: eval evidence is missing "
                "%s" % (skill.skill_id, skill.version, ", ".join(missing)))
        cases = evidence.get("cases")
        failed = evidence.get("failed")
        if not isinstance(cases, int) or cases <= 0:
            raise SkillPublishRefused(
                "publish refused for skill %s %s: UNEVALUATED — eval evidence "
                "carries %r case(s); an unevaluated skill must not be "
                "published" % (skill.skill_id, skill.version, cases))
        if not isinstance(failed, int) or failed > 0:
            raise SkillPublishRefused(
                "publish refused for skill %s %s: eval evidence is not green "
                "(%r of %r case(s) failed)"
                % (skill.skill_id, skill.version, failed, cases))
        # Last line of defence: the harness itself must agree, reading the SAME
        # cases file this studio scores against (never the tracked checkout's,
        # so a scratch tree's cases gate its own publishes).
        if _evals is None:  # pragma: no cover - exercised by fail-closed test
            raise SkillPublishRefused(
                "publish refused for skill %s %s: %s is unavailable, so the "
                "evidence cannot be re-verified (fail closed)"
                % (skill.skill_id, skill.version, EVAL_HARNESS))
        try:
            if self._cases_path is not None:
                report = _evals.require_ok(skill.eval_id,
                                           path=self._cases_path)
            else:
                report = _evals.require_ok(skill.eval_id)
        except _evals.EvalGateError as exc:
            raise SkillPublishRefused(
                "publish refused for skill %s %s: %s"
                % (skill.skill_id, skill.version, exc)) from exc
        if not report.ok:
            raise SkillPublishRefused(
                "publish refused for skill %s %s: eval report is not green"
                % (skill.skill_id, skill.version))

    def deprecate(self, skill_id, version, actor=None):
        skill = self.get(skill_id, version)
        skill.deprecate()
        self._emit("retire", "deprecated", skill_id, version, actor=actor,
                   detail={"reason": "deprecated"})
        return skill.lifecycle

    def _emit(self, event, status, skill_id, version, actor=None, detail=None):
        log = self._registry.event_log
        return log.append(event, status, skill_id, version=version,
                          actor=actor, detail=detail)

    # -- reads ---------------------------------------------------------------
    def get(self, skill_id, version=None):
        versions = self._skills.get(skill_id)
        if not versions:
            raise SkillNotFoundError("unknown skill '%s'" % skill_id)
        if version is None:
            version = self._current.get(skill_id)
            if version is None:
                version = max(versions, key=lambda v: [int(p)
                                                       for p in v.split(".")])
        if version not in versions:
            raise SkillNotFoundError("skill '%s' has no version %s"
                                     % (skill_id, version))
        return versions[version]

    def versions(self, skill_id):
        versions = self._skills.get(skill_id)
        if not versions:
            raise SkillNotFoundError("unknown skill '%s'" % skill_id)
        return sorted(versions, key=lambda v: [int(p) for p in v.split(".")])

    def current_version(self, skill_id):
        """Newest published version of a skill, or None."""
        return self._current.get(skill_id)

    def is_installable(self, skill_id, version=None):
        try:
            skill = self.get(skill_id, version)
        except SkillNotFoundError:
            return False
        return skill.lifecycle in INSTALLABLE_SKILL_STATES

    # -- catalog: searchable by skill ----------------------------------------
    def search_skills(self, category=None, text=None, published_only=False):
        """Catalog search over skills — the skill dimension of the catalog.

        ``category`` filters the closed Skill Studio category (searchable BY
        SKILL), ``text`` matches skillId/description/tags, and
        ``published_only`` restricts to installable revisions. Returns one row
        per (skillId, skillVersion), newest last.
        """
        rows = []
        for skill_id in sorted(self._skills):
            for version in self.versions(skill_id):
                skill = self._skills[skill_id][version]
                if category is not None and skill.category != category:
                    continue
                if published_only and skill.lifecycle not in \
                        INSTALLABLE_SKILL_STATES:
                    continue
                if text is not None:
                    haystack = " ".join(filter(None, [
                        skill.skill_id, skill.description or "",
                        " ".join(skill.tags)]))
                    if text.lower() not in haystack.lower():
                        continue
                rows.append({
                    "skillId": skill.skill_id,
                    "skillVersion": skill.version,
                    "skillCategory": skill.category,
                    "skillLifecycle": skill.lifecycle,
                    "description": skill.description,
                    "tags": list(skill.tags),
                    "contentSha256": skill.content_sha256,
                })
        return rows

    def skill_categories(self):
        """The closed Skill Studio category vocabulary (catalog facet)."""
        return list(SKILL_CATEGORIES)

    def catalog_by_skill(self, packs=None):
        """Skill rows for the given pack rows (catalog grouping by skill).

        Consumes ``PackRegistry`` catalog rows and projects their bundled
        ``skill`` artifacts into skill rows, so the catalog can be browsed by
        skill independent of which pack happens to bundle it.
        """
        rows = []
        for row in (packs if packs is not None else
                    self._registry.live_packs()):
            doc = None
            try:
                doc = self._registry.get(row["id"], row["version"])
            except PackRegistryError:  # pragma: no cover - defensive
                continue
            for entry in ((doc.get("contents") or {}).get("skill") or []):
                rows.append({
                    "skillId": entry.get("skillId"),
                    "skillVersion": entry.get("skillVersion"),
                    "skillCategory": entry.get("skillCategory"),
                    "skillLifecycle": entry.get("skillLifecycle"),
                    "pack": row["id"],
                    "packVersion": row["version"],
                    "evalEvidence": entry.get("evalEvidence"),
                })
        return rows

    # -- bundle a published skill into a pack ---------------------------------
    def bundle_into_pack(self, pack_doc, skill_id, version):
        """Add a published skill to a pack's ``contents.skill`` list.

        Refused unless the skill is published — an unpublished skill must
        never reach a pack manifest. Returns the pack doc (mutated in place
        and returned for convenience).
        """
        skill = self.get(skill_id, version)
        entry = skill.to_artifact_entry()  # refuses when not published
        contents = pack_doc.setdefault("contents", {})
        entries = contents.setdefault("skill", [])
        ref = entry["ref"]
        if any(e.get("ref") == ref for e in entries):
            raise SkillAlreadyExistsError(
                "pack already bundles skill %s" % ref)
        entries.append(entry)
        return pack_doc

    # -- skill-level install / upgrade / rollback -----------------------------
    def install(self, tenant_id, skill_id, version, actor=None):
        """Install a published skill for a tenant (skill-level granularity).

        Preconditions: the skill is ``published`` and, when the skill is
        bundled by a pack, that pack is installable and its attestation
        verifies — the content/drift/rollback engine is the pack
        ``Installer``'s, never a parallel one. Installs only this skill's
        tree, so a skill-level install never touches the pack's other
        artifacts.
        """
        skill = self.get(skill_id, version)
        if skill.lifecycle not in INSTALLABLE_SKILL_STATES:
            raise SkillLifecycleError(
                "install refused: skill %s %s is '%s' (only %s skills are "
                "installable)" % (skill_id, version, skill.lifecycle,
                                  "/".join(INSTALLABLE_SKILL_STATES)))
        if self._installer is None:
            raise SkillError(
                "install refused: no pack installer configured (skill content "
                "must be signature-gated)")
        pack_id, pack_version = self._owning_pack(skill)
        if pack_id is None:
            raise SkillError(
                "install refused: skill %s %s is not bundled by any published "
                "pack, so its content cannot be signature-verified"
                % (skill_id, version))
        # Inherited consumer-trust + lifecycle gates.
        self._installer.install(tenant_id, pack_id, pack_version, actor=actor)
        self._materialize_skill(tenant_id, skill)
        self._pointer_write(tenant_id, skill_id, version)
        self._ledger_set(tenant_id, skill_id, version)
        self._emit("install", "installed", skill_id, version, actor=actor,
                   detail={"tenantId": tenant_id, "pack": pack_id})
        return {"skillId": skill_id, "skillVersion": version,
                "tenantId": tenant_id, "pack": pack_id,
                "packVersion": pack_version}

    def verify_installed(self, tenant_id, skill_id):
        """Skill-level drift detection: re-hash the on-disk skill tree."""
        version = self.active_version(tenant_id, skill_id)
        if version is None:
            raise SkillError(
                "tenant '%s' has no installed version of skill '%s'"
                % (tenant_id, skill_id))
        skill = self.get(skill_id, version)
        path = self._skill_path(tenant_id, skill_id, version)
        if not os.path.exists(path):
            raise SkillError(
                "installed skill tree for %s %s is missing (tenant %s)"
                % (skill_id, version, tenant_id))
        with open(path, "rb") as fh:
            actual = sha256_hex(fh.read())
        if actual != skill.content_sha256:
            raise SkillError(
                "skill content drift for %s %s (tenant %s): declared %s, "
                "actual %s" % (skill_id, version, tenant_id,
                               skill.content_sha256, actual))
        return {"skillId": skill_id, "skillVersion": version,
                "tenantId": tenant_id, "ok": True}

    def upgrade(self, tenant_id, skill_id, new_version, actor=None):
        """Upgrade a tenant to ``new_version`` of a skill.

        Precondition: ``new_version`` is published. On success the previous
        version is retained in the installed-version ledger, so
        :meth:`rollback` can restore it. Returns the install result.
        """
        previous = self.active_version(tenant_id, skill_id)
        if previous is None:
            raise SkillError(
                "upgrade refused: tenant '%s' has no installed version of "
                "skill '%s' to upgrade" % (tenant_id, skill_id))
        if previous == new_version:
            raise SkillError(
                "upgrade refused: tenant '%s' already has skill %s at the "
                "target version %s" % (tenant_id, skill_id, new_version))
        result = self.install(tenant_id, skill_id, new_version, actor=actor)
        self._emit("upgrade", "upgraded", skill_id, new_version, actor=actor,
                   detail={"from": previous, "to": new_version,
                           "tenantId": tenant_id})
        return result

    def rollback(self, tenant_id, skill_id, actor=None):
        """Roll back a skill to its previous installed version.

        This is the acceptance criterion: rollback MUST restore the previous
        skill version. The previous content is re-materialized from the
        revision's own pinned sha256, the active pointer is repointed at it,
        and a ``rollback`` event is appended. Returns
        ``{"skillId", "tenantId", "from", "to"}``.
        """
        history = self._ledger_get(tenant_id, skill_id)
        if len(history) < 2:
            raise SkillError(
                "rollback refused: tenant '%s' has no previous version of "
                "skill '%s' to roll back to" % (tenant_id, skill_id))
        current, previous = history[-1], history[-2]
        skill = self.get(skill_id, previous)
        self._materialize_skill(tenant_id, skill)
        self._pointer_write(tenant_id, skill_id, previous)
        history.pop()
        self._emit("rollback", "rolled_back", skill_id, previous,
                   actor=actor, detail={"from": current, "to": previous,
                                        "tenantId": tenant_id})
        return {"skillId": skill_id, "tenantId": tenant_id, "from": current,
                "to": previous}

    def active_version(self, tenant_id, skill_id):
        """Active (newest) installed skill version for a tenant, or None."""
        return (self._ledger_get(tenant_id, skill_id) or [None])[-1]

    def previous_version(self, tenant_id, skill_id):
        """The skill version active before the current one, or None."""
        history = self._ledger_get(tenant_id, skill_id)
        return history[-2] if len(history) >= 2 else None

    def install_history(self, tenant_id, skill_id):
        return list(self._ledger_get(tenant_id, skill_id))

    # -- internals -----------------------------------------------------------
    def _owning_pack(self, skill):
        """Find the published pack that bundles this skill, newest first."""
        for pack_id in sorted(self._registry._packs, reverse=True):
            for version in sorted(self._registry._packs[pack_id],
                                  key=lambda v: [int(p) for p in v.split(".")],
                                  reverse=True):
                doc = self._registry._packs[pack_id][version]
                for entry in ((doc.get("contents") or {}).get("skill") or []):
                    if entry.get("skillId") == skill.skill_id and \
                            entry.get("skillVersion") == skill.version:
                        return pack_id, version
        return None, None

    def _root(self):
        root = getattr(self._installer, "_root", None)
        if root is None:
            raise SkillError("no install root configured")
        return root

    def _materialize_skill(self, tenant_id, skill):
        """Write the skill's own tree under the install root + verify hash."""
        path = self._skill_path(tenant_id, skill.skill_id, skill.version)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(skill.content or b"")
        return path

    def _skill_path(self, tenant_id, skill_id, version):
        return os.path.join(self._root(), "skills", skill_id, version,
                            "%s.skill" % skill_id)

    def _pointer_write(self, tenant_id, skill_id, version):
        active_dir = os.path.join(self._root(), "skills", skill_id)
        os.makedirs(active_dir, exist_ok=True)
        with open(os.path.join(active_dir,
                               "active.%s" % _slug(tenant_id)),
                  "w", encoding="utf-8") as fh:
            fh.write(version + "\n")

    def _ledger(self):
        if not hasattr(self, "_skill_installs"):
            self._skill_installs = {}
        return self._skill_installs

    def _ledger_get(self, tenant_id, skill_id):
        return self._ledger().setdefault(tenant_id, {}).setdefault(skill_id, [])

    def _ledger_set(self, tenant_id, skill_id, version):
        history = self._ledger_get(tenant_id, skill_id)
        history.append(version)
        return history


def _slug(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value)


def skill_artifact_entry(skill_id, version, category, content, evidence=None,
                         lifecycle="published"):
    """Build a schema-shaped ``contents.skill`` entry from raw parts.

    Convenience for fixtures and release authoring: produces exactly the
    structure ``agent-pack.schema.json`` requires, including the mandatory
    ``evalEvidence`` block. When ``evidence`` has zero cases the entry is a
    deliberately UNEVALUATED skill — the shape the publish gate must refuse.
    """
    if evidence is None:
        evidence = {"harness": EVAL_HARNESS,
                    "evalId": "%s@%s" % (skill_id, version),
                    "cases": 0, "passed": 0, "failed": 0}
        lifecycle = "draft"
    data = base64.b64encode(bytes(content)).decode("ascii")
    return {
        "ref": "%s@%s" % (skill_id, version),
        "skillId": skill_id,
        "skillVersion": version,
        "skillCategory": category,
        "skillLifecycle": lifecycle,
        "evalEvidence": dict(evidence),
        "data": data,
        "sha256": sha256_hex(bytes(content)),
    }
