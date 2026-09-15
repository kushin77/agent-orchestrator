"""Pytest bootstrap: make ``integrations.erp.api`` importable from any working dir.

``integrations/`` has no ``__init__.py`` (it is a namespace package), so the
package is importable once the *repository root* is on ``sys.path``. Pytest's
rootdir handling usually arranges that, but "usually" is not a property a suite
should rest on — the same reasoning as ``identity/rbac/tests/conftest.py`` and
``integrations/erp/auth/tests/conftest.py``, which insert their own root rather
than assuming one.

The fixtures here are deliberately thin: everything the suite drives comes from
:mod:`integrations.erp.api.fixtures`, because a test that builds its own surface
would test a surface nobody serves.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
# integrations/erp/api/tests -> .../api -> .../erp -> integrations -> <repo>
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_HERE))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from integrations.erp.api import fixtures  # noqa: E402
from integrations.erp.api.surface import Surface  # noqa: E402
from integrations.erp.auth.model import Principal  # noqa: E402


@pytest.fixture()
def root() -> Path:
    """The repository root, from the package's own resolution."""
    return fixtures.repository_root()


@pytest.fixture()
def model():
    return fixtures.model()


@pytest.fixture()
def declarations(model):
    return fixtures.declarations(model)


@pytest.fixture()
def world(declarations):
    """A surface, its store and a clerk principal — the same rig the gate drives."""
    documents = fixtures.seeded_store(declarations.model)
    surface = Surface(
        model=declarations.model,
        documents=documents,
        role_map=declarations.role_map,
        policy_set=declarations.policy_set,
        rbac_store=declarations.rbac_store,
        team=declarations.team,
        root=fixtures.repository_root(),
        request_ids=_counter(),
    )
    principal = Principal(
        tenant=declarations.tenant,
        subject=fixtures.PLATFORM_SUBJECT,
        roles=("ERP Clerk",),
    )
    return _World(surface, documents, principal)


class _World:
    """A tiny request helper: ``world.get(...)`` returns the envelope, not an exception."""

    def __init__(self, surface, documents, principal) -> None:
        self.surface = surface
        self.documents = documents
        self.principal = principal

    def request(self, method: str, path: str, body=None, principal=None):
        return self.surface.handle(
            method, path, principal=self.principal if principal is None else principal, body=body
        )

    def get(self, path: str, principal=None):
        return self.request("GET", path, principal=principal)

    def post(self, path: str, body=None, principal=None):
        return self.request("POST", path, body, principal=principal)

    def put(self, path: str, body=None, principal=None):
        return self.request("PUT", path, body, principal=principal)

    def delete(self, path: str, principal=None):
        return self.request("DELETE", path, principal=principal)

    def principal_as(self, role: str) -> Principal:
        return Principal(
            tenant=self.principal.tenant, subject=self.principal.subject, roles=(role,)
        )


def _counter():
    """Deterministic request ids, so a transcript is comparable between runs."""
    state = {"n": 0}

    def next_id() -> str:
        state["n"] += 1
        return f"req-{state['n']:04d}"

    return next_id


@pytest.fixture()
def world_for():
    """A factory: ``world_for("ERP Auditor")`` gives a surface whose bindings are that role's."""

    def build(role: str = "ERP Clerk", permissions=None) -> _World:
        loaded = fixtures.model()
        decl = fixtures.declarations(loaded, role=role, permissions=permissions)
        documents = fixtures.seeded_store(loaded)
        surface = Surface(
            model=loaded,
            documents=documents,
            role_map=decl.role_map,
            policy_set=decl.policy_set,
            rbac_store=decl.rbac_store,
            team=decl.team,
            root=fixtures.repository_root(),
            request_ids=_counter(),
        )
        return _World(
            surface,
            documents,
            Principal(tenant=decl.tenant, subject=fixtures.PLATFORM_SUBJECT, roles=(role,)),
        )

    return build
