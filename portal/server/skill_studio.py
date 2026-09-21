"""portal.server.skill_studio — the skill-studio serving surface (issue #642, workbook-11).

WHY this exists: the workbook-9 lane already ships the whole author → test →
publish workflow as a library (``registry/packs/skills.SkillStudio``), including
the gate that matters — *publish is refused without green eval evidence derived
from the workbook-8 harness*. A tenant skill author cannot call a Python class,
and a static page would have to re-implement the gate, which is exactly how a
gate becomes a formality. This module is the *server half* of the skill studio:
it exposes the studio's own lifecycle over the console's HTTP surface.

Cannibalize, do not duplicate. Every state, every verdict and every refusal is
the ``SkillStudio``'s own:

* the authored revisions, their lifecycle (``draft``/``tested``/``published``/
  ``deprecated``), their content sha256 and their recorded eval evidence —
  ``SkillStudio.get`` / ``.versions`` / ``.search_skills``;
* the lifecycle transitions — ``SkillStudio.author`` / ``.test`` / ``.publish``;
* every refusal — ``SkillAlreadyExistsError`` (an authored revision is
  immutable), ``SkillLifecycleError`` (publish from a non-``tested`` state),
  ``SkillPublishRefused`` (no evidence, zero cases, or a failing case) — is
  raised by the studio and surfaced here as a 4xx with the studio's own message.
  This adapter re-checks nothing: a publish the studio accepts is accepted, and
  one it refuses is refused with its reason intact.

The adapter owns transport shape and one honesty rule:

* **an unknown skill is absent, not invented.** A revision the studio does not
  hold is a 404; the adapter never returns an empty shell that would read as
  "authored, nothing in it".

The surface ships **feature-flag-gated OFF** (GR-5): the flag is declared in the
portal's own ``portal/config/feature-flags.yaml`` and read here through
``portal.server.config_flags``; while it is off the app refuses every
``/api/skillstudio/*`` route before authentication.


---knowledge---
module_id: portal.server.skill_studio
system: portal
app: server
solution_class: pattern
patterns: [delegate-never-re-derive, refusal-preserved, feature-flag-gated-off]
derives_from: null
owner_sme: platform-sme
tier: L1
interfaces: [SkillStudioSurface, SkillStudioError]
invariants: "an unknown skill is absent (404), never an invented empty shell; the adapter re-checks nothing the studio already refuses"
gotchas: ""
related: ["#642"]
do_not_duplicate: null
---knowledge---
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from portal.server.config_flags import SKILL_STUDIO_SURFACE, surface_enabled

# ``registry/packs`` is a sibling pillar package, not an installed module, and
# the console boots with only the repo root on ``sys.path``. The pillar's own
# root has to be on the path for the duration of that import — but for the
# *duration* only. Doing it at module-import time would be a global side effect:
# a bare ``import portal.server.skill_studio`` would re-order every later import
# in the process, so ``packs``, ``personas``, ``profiles`` and ``prompts`` would
# start resolving out of the pillar's own directory instead of the namespace
# package the caller had already resolved. Importing this module is therefore
# allowed to change nothing but ``sys.modules``.
_PILLAR_ROOT = Path(__file__).resolve().parents[2] / "registry"


@contextlib.contextmanager
def _pillar_root_on_path() -> Iterator[None]:
    """Lend ``registry/`` to ``sys.path`` for the enclosed import, then take it back.

    The entry is removed in ``finally``, and only when this frame is the one
    that put it there — a caller that already had the pillar root on its path
    keeps it exactly as it was. ``registry/packs/skills.py`` bootstraps
    ``registry/prompts`` for its own imports the same way; those entries are
    the pillar's business, not this adapter's.
    """
    entry = str(_PILLAR_ROOT)
    borrowed = entry not in sys.path
    if borrowed:
        sys.path.insert(0, entry)
    try:
        yield
    finally:
        if borrowed:
            try:
                sys.path.remove(entry)
            except ValueError:  # pragma: no cover - a peer took it back first
                pass

#: The schema tag this surface emits.
SCHEMA = "ao.portal-skill-studio/v1"

#: Actions the studio's write surface accepts (the lifecycle's three edges).
ACTION_AUTHOR = "author"
ACTION_TEST = "test"
ACTION_PUBLISH = "publish"
WRITE_ACTIONS = (ACTION_AUTHOR, ACTION_TEST, ACTION_PUBLISH)


class SkillStudioError(Exception):
    """An HTTP-addressable refusal raised by the studio (``error.code``)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _load_skills_module():
    """Import the workbook-9 studio module, borrowing the pillar root to do it.

    ``packs`` is the same module either way; ``registry.packs`` is tried first
    because it is the explicit spelling, and the plain ``packs`` import is the
    convention the pillar's own suites use. Both happen *inside* the path loan:
    ``registry/packs/skills.py`` reaches for ``packs.registry`` and ``evals`` by
    their pillar-relative names while it executes, so the root has to be on the
    path for that window — and off it again once the module is imported.
    """
    with _pillar_root_on_path():
        try:
            from registry.packs import skills as module  # type: ignore
        except ImportError:  # pragma: no cover - the fallback spelling
            from packs import skills as module  # type: ignore

    return module


def _module_for(studio: Any):
    """The module that *owns* ``studio`` — its exception classes live there.

    ``registry.packs.skills`` and ``packs.skills`` are two distinct module
    objects for the same file (the pillar is importable under both bootstraps),
    and their exception classes are therefore distinct too: catching
    ``packs.skills.SkillPublishRefused`` would never match an error raised by a
    ``registry.packs.skills`` studio. Resolving the module from the instance
    makes an injected studio's refusals translatable regardless of which
    spelling the caller imported.
    """
    module_name = getattr(type(studio), "__module__", "")
    module = sys.modules.get(module_name)
    return module if module is not None else _load_skills_module()


class SkillStudioSurface:
    """Projects the workbook-9 ``SkillStudio`` lifecycle over HTTP.

    ``studio`` is the real ``SkillStudio`` (injected, so tests drive a scratch
    tree and the server drives the repo's own). ``enabled`` is resolved from the
    portal's own flag file unless supplied explicitly.
    """

    def __init__(
        self,
        *,
        repo_root: Path | str,
        enabled: Optional[bool] = None,
        config_path: Optional[Path | str] = None,
        studio: Optional[Any] = None,
        studio_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.config_path = (
            Path(config_path) if config_path is not None else None
        )
        if enabled is None:
            enabled = surface_enabled(
                self.repo_root,
                config_path=self.config_path,
                surface=SKILL_STUDIO_SURFACE,
            )
        self.enabled = bool(enabled)
        self._studio = studio
        self._studio_factory = studio_factory

    # -- reads ---------------------------------------------------------------
    def skills(
        self, *, category: Optional[str] = None, text: Optional[str] = None
    ) -> dict:
        """The authored revisions the studio holds, optionally filtered.

        The filter is the studio's own ``search_skills`` (published-only is
        deliberately *not* forced: the studio is an authoring surface, so drafts
        are what an author came to see). Every row carries the lifecycle state
        and the content sha256, and nothing else is derived here.
        """
        studio = self._require_studio()
        rows = studio.search_skills(category=category, text=text)
        # ``search_skills`` returns the studio's own serialized rows (dicts);
        # each one is served verbatim, with the one derived boolean the list
        # view renders added on.
        return {
            "schema": SCHEMA,
            "categories": list(studio.skill_categories()),
            "skills": [
                {**dict(row), "hasEvalEvidence": bool(row.get("evalEvidence"))}
                for row in rows
            ],
        }

    def skill(self, skill_id: str, version: Optional[str] = None) -> dict:
        """One revision (the studio's ``current`` when ``version`` is absent)."""
        studio = self._require_studio()
        artifact = self._get(studio, skill_id, version)
        return {
            "schema": SCHEMA,
            "skill": self._detail(artifact),
        }

    # -- writes (the lifecycle's three edges) --------------------------------
    def author(self, payload: Dict[str, Any]) -> dict:
        """Author a revision — the studio refuses a duplicate (immutable)."""
        studio = self._require_studio()
        skill_id = self._required(payload, "skillId")
        version = self._required(payload, "skillVersion")
        category = self._required(payload, "category")
        content = payload.get("content")
        return self._invoke(
            lambda: studio.author(
                skill_id,
                version,
                category,
                content=content.encode("utf-8") if isinstance(content, str) else content,
                description=payload.get("description"),
                tags=payload.get("tags"),
            ),
            skill_id=skill_id,
            version=version,
        )

    def test(self, payload: Dict[str, Any]) -> dict:
        """Test a revision — the workbook-8 eval harness scores it and records.

        ``cases`` may be supplied as JSON objects (``promptId``/``caseId``/
        ``expected``/``observed``); they are rebuilt into the harness's own
        ``EvalCase`` before the studio sees them, so the adapter never invents a
        second case vocabulary and a client never sends a Python object.
        """
        studio = self._require_studio()
        skill_id = self._required(payload, "skillId")
        version = self._required(payload, "skillVersion")
        cases = self._cases_for(payload.get("cases"), skill_id, version, studio)
        path = payload.get("casesPath")
        return self._invoke(
            lambda: studio.test(skill_id, version, cases=cases, path=path),
            skill_id=skill_id,
            version=version,
        )

    def publish(self, payload: Dict[str, Any]) -> dict:
        """Publish a *tested* revision — REFUSED without green eval evidence.

        The refusal is the studio's; this method only carries its message and
        status. A ``SkillPublishRefused`` becomes a 409 (the revision exists but
        is not in a publishable condition), a ``SkillLifecycleError`` a 409, and
        a missing revision a 404.
        """
        studio = self._require_studio()
        skill_id = self._required(payload, "skillId")
        version = self._required(payload, "skillVersion")
        return self._invoke(
            lambda: studio.publish(skill_id, version, actor=payload.get("actor")),
            skill_id=skill_id,
            version=version,
            result_of=lambda: studio.get(skill_id, version).lifecycle,
        )

    # -- internals -----------------------------------------------------------
    def _require_studio(self) -> Any:
        if self._studio is None:
            factory = self._studio_factory
            if factory is None:

                def factory() -> Any:
                    """The workbook-9 studio over the pillar's own pack registry.

                    ``SkillStudio`` composes ``PackRegistry`` (the pack catalog
                    and consumption ledger) with the signature/drift engine; the
                    studio is what owns the *skill* dimension this surface
                    serves, and it is constructed exactly as the workbook-9
                    lane's own entry point does — no adapter-side wiring.
                    """
                    skills = _load_skills_module()
                    pack_registry = sys.modules.get("packs.registry")
                    if pack_registry is None:
                        import packs.registry as pack_registry  # type: ignore
                    return skills.SkillStudio(pack_registry.PackRegistry())

            self._studio = factory()
        return self._studio

    @staticmethod
    def _required(payload: Dict[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise SkillStudioError(
                400, "invalid_request", f"{field} is required and must be non-empty"
            )
        return value.strip()

    @staticmethod
    def _get(studio: Any, skill_id: str, version: Optional[str]) -> Any:
        try:
            return studio.get(skill_id, version)
        except Exception as exc:
            raise SkillStudioError(
                404, "not_found", f"skill {skill_id!r} has no such revision: {exc}"
            ) from None

    @staticmethod
    def _cases_for(
        raw: Any, skill_id: str, version: str, studio: Any
    ) -> Optional[List[Any]]:
        """Rebuild JSON eval cases into the harness's own ``EvalCase`` objects.

        ``None`` (no cases supplied) is passed through, which lets the studio
        fall back to the tracked cases file. An object that already *is* an
        ``EvalCase`` (a Python caller) is passed through untouched. Anything
        else is refused rather than silently ignored — a case the harness never
        scores must not read as a passing suite.

        The ``EvalCase`` class is taken from the studio's *own* already-imported
        harness (``packs.skills._evals``), never re-imported here: that module
        bootstraps ``registry/prompts`` onto ``sys.path`` itself, so reaching
        for the class directly would duplicate a path contract this adapter does
        not own — and would risk a distinct class object from the one the studio
        compares against.
        """
        if raw is None:
            return None
        if not isinstance(raw, (list, tuple)):
            raise SkillStudioError(400, "invalid_request", "cases must be a list")

        evals = getattr(_module_for(studio), "_evals", None)
        eval_case_cls = getattr(evals, "EvalCase", None)
        if eval_case_cls is None:  # pragma: no cover - the studio fails closed
            raise SkillStudioError(
                503,
                "harness_unavailable",
                "the workbook-8 eval harness is unavailable, so supplied cases "
                "cannot be scored (fail closed)",
            )

        cases: List[Any] = []
        for item in raw:
            if isinstance(item, eval_case_cls):
                cases.append(item)
                continue
            if not isinstance(item, Dict):
                raise SkillStudioError(
                    400, "invalid_request", "each case must be an object"
                )
            # ``promptId`` defaults to the skill's own eval id, which is what the
            # studio scores against — a case for another eval id would belong to
            # a different report and would never be scored here.
            prompt_id = str(item.get("promptId") or f"{skill_id}@{version}")
            cases.append(
                eval_case_cls(
                    prompt_id=prompt_id,
                    case_id=str(item.get("caseId") or prompt_id),
                    expected=frozenset(item.get("expected") or ()),
                    observed=frozenset(item.get("observed") or ()),
                )
            )
        return cases

    def _invoke(
        self,
        call: Callable[[], Any],
        *,
        skill_id: str,
        version: str,
        result_of: Optional[Callable[[], Any]] = None,
    ) -> dict:
        """Run a studio transition and translate its refusals into HTTP errors.

        The studio's exception *types* are mapped to status codes; its messages
        are passed through verbatim, because the message is the evidence an
        author needs (which case failed, which state was wrong). The classes are
        read from the studio's own module, so an injected studio stays
        translatable whichever bootstrap imported it.
        """
        module = _module_for(self._require_studio())
        try:
            result = call()
        except module.SkillAlreadyExistsError as exc:
            raise SkillStudioError(409, "already_exists", str(exc)) from None
        except module.SkillNotFoundError as exc:
            raise SkillStudioError(404, "not_found", str(exc)) from None
        except module.SkillPublishRefused as exc:
            raise SkillStudioError(409, "publish_refused", str(exc)) from None
        except module.SkillLifecycleError as exc:
            raise SkillStudioError(409, "lifecycle_refused", str(exc)) from None
        except module.SkillError as exc:
            raise SkillStudioError(400, "skill_error", str(exc)) from None
        artifact = result_of() if result_of is not None else result
        if not hasattr(artifact, "skill_id"):
            artifact = self._get(self._require_studio(), skill_id, version)
        return {
            "schema": SCHEMA,
            "skillId": skill_id,
            "skillVersion": version,
            "skill": self._detail(artifact),
        }

    @staticmethod
    def _row(artifact: Any) -> dict:
        """The studio's own row, verbatim (its keys, its hashes, its state).

        ``SkillStudio.search_skills`` already returns camelCase rows and
        ``SkillArtifact.to_dict`` returns the same shape, so the adapter serves
        the studio's own keys and adds only ``hasEvalEvidence`` — a boolean the
        list view renders. Nothing is renamed and nothing is recomputed.
        """
        row = dict(artifact.to_dict())
        row["hasEvalEvidence"] = bool(
            getattr(artifact, "eval_evidence", None)
        )
        return row

    @classmethod
    def _detail(cls, artifact: Any) -> dict:
        row = cls._row(artifact)
        row["evalEvidence"] = dict(getattr(artifact, "eval_evidence", None) or {})
        return row
