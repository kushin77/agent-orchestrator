"""Granting and revoking role bindings, holding the no-lockout invariant.

An Org whose last role-admin binding is revoked has no way back through any
API: every route that could re-grant the admin permission requires the
permission itself (the provisioning CLI is an operator tool with database
access). It is reachable by entirely ordinary means - an administrator
revoking their own role, or two administrators revoking each other.

Revocation therefore refuses to leave an Org with nobody holding the Org's
role-admin permission (``Org.role_admin_permission``, default ``roles:manage``;
wildcards count, so ``*:*`` confers it). The check is keyed to the Org's own
declared permission rather than a hardcoded string, because a tenant that
brings its own preset pack names role administration in its own vocabulary
(saas-rbac #125) - an invariant keyed to a hardcoded ``roles:manage`` silently
never runs for such a tenant.

Both operations are idempotent: re-granting a role the subject already holds
returns the existing binding, and revoking one it does not hold is a no-op, so
a caller retrying an uncertain request never has to tell "already done" from
"conflict".
"""

from __future__ import annotations

from rbac.model import DEFAULT_ROLE_ADMIN_PERMISSION, role_grants


class UnknownRoleError(ValueError):
    """No role with the requested key exists in this Org."""

    def __init__(self, role_key: str) -> None:
        super().__init__(
            f'no role with key "{role_key}" exists in this org'
        )
        self.role_key = role_key


class LastAdministratorError(RuntimeError):
    """Revocation refused: this binding is the last one holding role admin."""

    def __init__(self, permission: str = DEFAULT_ROLE_ADMIN_PERMISSION) -> None:
        super().__init__(
            f'refusing to revoke the last binding that grants "{permission}" '
            "in this org: nobody could grant it back through an API. "
            "Grant it to someone else first."
        )
        self.permission = permission


def grant_role(
    store,
    org_id: str,
    subject: str,
    role_key: str,
    *,
    subject_type: str = "user",
    team_id: str | None = None,
):
    """Grant ``role_key`` to ``subject`` at the given scope of ``org_id``.

    Idempotent: re-granting a role the subject already holds at the same scope
    returns the existing binding.
    """
    role = store.find_role_by_key(org_id, role_key)
    if role is None:
        raise UnknownRoleError(role_key)

    for binding in store.bindings_for_subject(org_id, subject):
        if binding.role_id == role.id and binding.team_id == team_id:
            return binding

    return store.add_binding(
        org_id=org_id,
        subject=subject,
        subject_type=subject_type,
        role_id=role.id,
        team_id=team_id,
    )


def revoke_role(
    store,
    org_id: str,
    subject: str,
    role_key: str,
    *,
    team_id: str | None = None,
) -> bool:
    """Revoke ``role_key`` from ``subject`` at the given scope of ``org_id``.

    Returns False when the subject did not hold it (idempotent no-op). Raises
    ``LastAdministratorError`` rather than removing an Org's only remaining
    holder of the role-admin permission.
    """
    org = store.org(org_id)
    role = store.find_role_by_key(org_id, role_key)
    if role is None:
        raise UnknownRoleError(role_key)

    target = None
    for binding in store.bindings_for_subject(org_id, subject):
        if binding.role_id == role.id and binding.team_id == team_id:
            target = binding
            break
    if target is None:
        return False

    admin_permission = (
        org.role_admin_permission if org is not None else DEFAULT_ROLE_ADMIN_PERMISSION
    )

    # Only bindings whose role carries the role-admin permission can strand an
    # Org, so only they pay for the count. Wildcards count (*:* confers it).
    if role_grants(role, admin_permission):
        remaining = 0
        for binding in store.bindings_in_org(org_id):
            if binding.id == target.id:
                continue
            other = store.role_by_id(binding.role_id)
            if other is not None and role_grants(other, admin_permission):
                remaining += 1
        if remaining == 0:
            raise LastAdministratorError(admin_permission)

    store.delete_binding(target.id)
    return True
