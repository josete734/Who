"""Role-based access policy.

`can(user, action, resource)` is the single decision function. It is a
pure helper: callers pass already-resolved dicts/objects with `role` and
`org_id` attributes, so the policy is trivially unit-testable.

Action grammar: ``<domain>.<verb>`` where domain is one of
``case|settings|audit|admin|webhooks`` and verb is ``read|write|keys``.
"""
from __future__ import annotations

from typing import Any, Mapping

from .models import Role


# Higher number = more privilege.
ROLE_RANK: dict[str, int] = {
    Role.VIEWER.value: 1,
    Role.INVESTIGATOR.value: 2,
    Role.ADMIN.value: 3,
    Role.OWNER.value: 4,
}


# Minimum role required for each action.
_ACTION_MIN_ROLE: dict[str, str] = {
    "case.read": Role.VIEWER.value,
    "case.write": Role.INVESTIGATOR.value,
    "settings.write": Role.ADMIN.value,
    "audit.read": Role.ADMIN.value,
    "admin.keys": Role.OWNER.value,
    "webhooks.write": Role.ADMIN.value,
}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _role_value(user: Any) -> str | None:
    role = _get(user, "role")
    if role is None:
        return None
    if hasattr(role, "value"):
        return role.value
    return str(role)


def can(user: Any, action: str, resource: Any | None = None) -> bool:
    """Return True iff ``user`` may perform ``action`` on ``resource``.

    ``user`` is expected to expose ``role`` and ``org_id``. ``resource``
    may expose ``org_id`` for cross-tenant isolation. Unknown actions
    deny by default (fail-closed).
    """
    if user is None:
        return False

    min_role = _ACTION_MIN_ROLE.get(action)
    if min_role is None:
        return False

    user_role = _role_value(user)
    if user_role is None or user_role not in ROLE_RANK:
        return False

    if ROLE_RANK[user_role] < ROLE_RANK[min_role]:
        return False

    # Org isolation: if the resource declares an org_id, it must match.
    if resource is not None:
        res_org = _get(resource, "org_id")
        usr_org = _get(user, "org_id")
        if res_org is not None and usr_org is not None and res_org != usr_org:
            return False

    return True
