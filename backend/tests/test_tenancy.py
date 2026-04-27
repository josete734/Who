"""Tenancy policy & dependency tests."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.tenancy.dependencies import current_org, require_role
from app.tenancy.models import Role
from app.tenancy.policy import ROLE_RANK, can


ORG_A = uuid.uuid4()
ORG_B = uuid.uuid4()


def _user(role: Role | str, org_id: uuid.UUID = ORG_A) -> SimpleNamespace:
    return SimpleNamespace(role=role, org_id=org_id)


# ---------------------------------------------------------------------------
# policy.can
# ---------------------------------------------------------------------------
def test_can_viewer_can_read_case() -> None:
    assert can(_user(Role.VIEWER), "case.read") is True


def test_can_viewer_cannot_write_case() -> None:
    assert can(_user(Role.VIEWER), "case.write") is False


def test_can_investigator_writes_case() -> None:
    assert can(_user(Role.INVESTIGATOR), "case.write") is True


def test_can_admin_settings_write() -> None:
    assert can(_user(Role.ADMIN), "settings.write") is True
    assert can(_user(Role.INVESTIGATOR), "settings.write") is False


def test_can_audit_read_requires_admin() -> None:
    assert can(_user(Role.ADMIN), "audit.read") is True
    assert can(_user(Role.VIEWER), "audit.read") is False


def test_can_admin_keys_owner_only() -> None:
    assert can(_user(Role.OWNER), "admin.keys") is True
    assert can(_user(Role.ADMIN), "admin.keys") is False


def test_can_webhooks_write_admin() -> None:
    assert can(_user(Role.ADMIN), "webhooks.write") is True
    assert can(_user(Role.VIEWER), "webhooks.write") is False


def test_can_unknown_action_denies() -> None:
    assert can(_user(Role.OWNER), "secrets.exfiltrate") is False


def test_can_no_user_denies() -> None:
    assert can(None, "case.read") is False


def test_can_cross_org_resource_denied() -> None:
    user = _user(Role.OWNER, ORG_A)
    resource = SimpleNamespace(org_id=ORG_B)
    assert can(user, "case.read", resource) is False


def test_can_same_org_resource_allowed() -> None:
    user = _user(Role.INVESTIGATOR, ORG_A)
    resource = SimpleNamespace(org_id=ORG_A)
    assert can(user, "case.write", resource) is True


def test_can_accepts_string_role() -> None:
    assert can({"role": "admin", "org_id": ORG_A}, "settings.write") is True


def test_role_rank_monotonic() -> None:
    assert (
        ROLE_RANK["viewer"]
        < ROLE_RANK["investigator"]
        < ROLE_RANK["admin"]
        < ROLE_RANK["owner"]
    )


# ---------------------------------------------------------------------------
# dependencies
# ---------------------------------------------------------------------------
def _app_with_dep(role: Role | str | None, org_id: uuid.UUID | None = ORG_A) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def _inject(request, call_next):
        if role is not None or org_id is not None:
            request.state.api_key = SimpleNamespace(
                id=uuid.uuid4(), org_id=org_id, role=role
            )
        return await call_next(request)

    @app.get("/admin", dependencies=[])
    def admin_route(_: str = __import__(
        "fastapi"
    ).Depends(require_role(Role.ADMIN))):
        return {"ok": True}

    @app.get("/org")
    def org_route(o: uuid.UUID = __import__("fastapi").Depends(current_org)):
        return {"org_id": str(o)}

    return app


def test_require_role_allows_admin() -> None:
    client = TestClient(_app_with_dep(Role.ADMIN))
    assert client.get("/admin").status_code == 200


def test_require_role_blocks_viewer() -> None:
    client = TestClient(_app_with_dep(Role.VIEWER))
    assert client.get("/admin").status_code == 403


def test_current_org_missing_api_key_401() -> None:
    client = TestClient(_app_with_dep(role=None, org_id=None))
    assert client.get("/org").status_code == 401


def test_current_org_returns_uuid() -> None:
    client = TestClient(_app_with_dep(Role.VIEWER, ORG_A))
    r = client.get("/org")
    assert r.status_code == 200
    assert r.json()["org_id"] == str(ORG_A)


def test_require_role_unknown_raises() -> None:
    with pytest.raises(ValueError):
        require_role("godmode")


# ---------------------------------------------------------------------------
# orgs router smoke
# ---------------------------------------------------------------------------
def test_orgs_router_create_and_list() -> None:
    from app.routers.orgs_router import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    r = client.post("/api/orgs", json={"name": "Acme", "slug": "acme-test-xyz"})
    assert r.status_code == 201, r.text
    org_id = r.json()["id"]

    r2 = client.get(f"/api/orgs/{org_id}")
    assert r2.status_code == 200
    assert r2.json()["slug"] == "acme-test-xyz"
