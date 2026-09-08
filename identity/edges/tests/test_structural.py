"""Structural guarantee - the edge performs NO authorization (authn never
authz, AC #2).

The public edge may authenticate (authN) and apply the route *allowlist*
(publication), but it must never contain authorization logic: no import of
the rbac engine and no call to scope-resolution / permission / guard /
decision symbols.  Authorization happens inside, after scope resolution, in
the downstream #38 REST server composing ``identity/rbac`` (issue #12).

Two layers of proof:

1. **Static (AST)** - every runtime module under ``identity/edges`` is parsed
   and asserted to contain no import of ``rbac``/``identity.rbac`` and no
   call/attribute reference to the authorization vocabulary.  This is an
   honest structural check that fails on real code, not prose.
2. **Behavioral** - a request that the backend denies (403) is relayed
   untouched and the edge never fabricates an allow/deny from the identity or
   role snapshot it forwards.
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

from identity.edges.edge import PublicEdge
from identity.edges.tests._support import StubBackend, accepting_verifier

#: Runtime modules of this lane (everything under identity/edges except tests).
_EDGES_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Authorization vocabulary that must never appear as an import target in the
#: edge's runtime code.
_FORBIDDEN_IMPORTS = {
    "rbac",
    "identity.rbac",
    "rbac.guard",
    "rbac.resolve",
    "rbac.model",
    "rbac.bindings",
}

#: Authorization symbols that must never be *called/used* in the edge's
#: runtime code (scope resolution + permission gates + guard decisions).
_FORBIDDEN_SYMBOLS = {
    "authorize",
    "authorize_tool_use",
    "resolve_scope",
    "guard",
    "guard_required",
    "guard_session",
    "start_agent_session",
    "authorization_denied_payload",
    "Decision",
    "ScopeDeniedError",
    "PermissionDeniedError",
    "permission_granted",
    "effective_permissions",
}


def _runtime_modules() -> list[pathlib.Path]:
    return sorted(
        path
        for path in pathlib.Path(_EDGES_DIR).glob("*.py")
        if path.name not in {"__init__.py"} and not path.name.startswith("_")
    )


@pytest.fixture(scope="module")
def runtime_modules() -> list[pathlib.Path]:
    modules = _runtime_modules()
    assert modules, "no runtime modules found to scan"
    return modules


def _ast_names(tree: ast.AST) -> set[str]:
    """Every imported/loaded/attribute/call name in a module's AST."""
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
                names.add(node.module)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Call):
            pass  # the callee's Name/Attribute is already walked
    return names


def test_runtime_modules_never_import_rbac(runtime_modules):
    for path in runtime_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in {
                        m.split(".")[0] for m in _FORBIDDEN_IMPORTS
                    }, f"{path.name}: imports authorization engine {alias.name!r}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                top = module.split(".")[0]
                assert top != "rbac", (
                    f"{path.name}: imports authorization engine via "
                    f"'from {module} import ...'"
                )
                assert module not in _FORBIDDEN_IMPORTS, (
                    f"{path.name}: imports {module!r}"
                )


def test_runtime_modules_never_use_authorization_symbols(runtime_modules):
    for path in runtime_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _ast_names(tree)
        used = names & _FORBIDDEN_SYMBOLS
        assert not used, (
            f"{path.name}: uses authorization symbol(s) {sorted(used)} - "
            f"the public edge must not authorize"
        )


def test_runtime_modules_may_authenticate_and_allowlist(runtime_modules):
    """The edge's own vocabulary (authN + allowlist) is present and used."""
    all_src = "\n".join(p.read_text(encoding="utf-8") for p in runtime_modules)
    # The edge does authenticate (its job)...
    assert "verify_caller" in all_src
    assert "bearer_token" in all_src
    # ...and does apply the explicit allowlist (publication decision)...
    assert "unknown_route" in all_src
    assert "method_not_allowed" in all_src
    # ...but forwards rather than decides.
    assert "role_snapshot" in all_src


def test_forwarding_never_inspects_role_for_a_decision(routes):
    """Behavioral: the role snapshot is forwarded, never used to allow/deny."""

    def recording_backend(request):
        # The backend (not the edge) is where authorization happens.  It
        # records that it received the role snapshot as context.
        assert request.role_snapshot == ("member",)
        return 200, {"ok": True}

    edge = PublicEdge(
        routes, verifier=accepting_verifier(), backend=recording_backend
    )
    response = edge.handle_request("GET", "/v1/admin/agents",
                                   headers={"authorization": "Bearer t"})
    # The edge relays the backend answer - it made no decision of its own.
    assert response.status == 200


def test_denial_is_relayed_not_redecided(routes):
    denial = {"error": {"code": "authorization_denied", "permission": "roles:manage"}}
    backend = StubBackend(status=403, body=denial)
    edge = PublicEdge(routes, verifier=accepting_verifier(), backend=backend.call)
    response = edge.handle_request("GET", "/v1/admin/agents",
                                   headers={"authorization": "Bearer t"})
    assert response.status == 403
    assert response.body == denial
    # The edge never turned the denial into a 200 and never rewrote it.
    assert response.to_dict()["body"] == denial
