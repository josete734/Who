"""CRUD router for orgs / teams / memberships.

Persistence uses a tiny in-process registry by default so the router is
importable without a live DB; production deployments swap the registry
for a real SQLAlchemy-backed store. The shape of the HTTP API is the
contract that matters here.
"""
from __future__ import annotations

import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.tenancy.dependencies import current_org, require_role
from app.tenancy.models import Membership, Org, Role, Team


router = APIRouter(prefix="/api/orgs", tags=["orgs"])


# ---------------------------------------------------------------------------
# In-memory registry (test/dev). Swap for DB in prod via DI override.
# ---------------------------------------------------------------------------
_ORGS: Dict[uuid.UUID, Org] = {}
_TEAMS: Dict[uuid.UUID, Team] = {}
_MEMBERSHIPS: Dict[uuid.UUID, Membership] = {}


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class MembershipIn(BaseModel):
    user_id: str = Field(min_length=1)
    role: Role
    team_id: Optional[uuid.UUID] = None


# ---------------------------------------------------------------------------
# Orgs
# ---------------------------------------------------------------------------
@router.post("", response_model=Org, status_code=status.HTTP_201_CREATED)
def create_org(payload: OrgIn) -> Org:
    if any(o.slug == payload.slug for o in _ORGS.values()):
        raise HTTPException(status_code=409, detail="slug already exists")
    org = Org(name=payload.name, slug=payload.slug)
    _ORGS[org.id] = org
    return org


@router.get("", response_model=List[Org])
def list_orgs() -> List[Org]:
    return list(_ORGS.values())


@router.get("/{org_id}", response_model=Org)
def get_org(org_id: uuid.UUID) -> Org:
    org = _ORGS.get(org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="org not found")
    return org


@router.delete(
    "/{org_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.OWNER))],
)
def delete_org(org_id: uuid.UUID) -> None:
    if org_id not in _ORGS:
        raise HTTPException(status_code=404, detail="org not found")
    _ORGS.pop(org_id, None)
    for tid, t in list(_TEAMS.items()):
        if t.org_id == org_id:
            _TEAMS.pop(tid, None)
    for mid, m in list(_MEMBERSHIPS.items()):
        if m.org_id == org_id:
            _MEMBERSHIPS.pop(mid, None)


# ---------------------------------------------------------------------------
# Teams
# ---------------------------------------------------------------------------
@router.post(
    "/{org_id}/teams",
    response_model=Team,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def create_team(org_id: uuid.UUID, payload: TeamIn) -> Team:
    if org_id not in _ORGS:
        raise HTTPException(status_code=404, detail="org not found")
    team = Team(org_id=org_id, name=payload.name)
    _TEAMS[team.id] = team
    return team


@router.get("/{org_id}/teams", response_model=List[Team])
def list_teams(org_id: uuid.UUID, _o: uuid.UUID = Depends(current_org)) -> List[Team]:
    return [t for t in _TEAMS.values() if t.org_id == org_id]


# ---------------------------------------------------------------------------
# Memberships
# ---------------------------------------------------------------------------
@router.post(
    "/{org_id}/memberships",
    response_model=Membership,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def add_membership(org_id: uuid.UUID, payload: MembershipIn) -> Membership:
    if org_id not in _ORGS:
        raise HTTPException(status_code=404, detail="org not found")
    m = Membership(
        org_id=org_id,
        user_id=payload.user_id,
        role=payload.role,
        team_id=payload.team_id,
    )
    _MEMBERSHIPS[m.id] = m
    return m


@router.get("/{org_id}/memberships", response_model=List[Membership])
def list_memberships(org_id: uuid.UUID) -> List[Membership]:
    return [m for m in _MEMBERSHIPS.values() if m.org_id == org_id]


@router.delete(
    "/{org_id}/memberships/{membership_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_role(Role.ADMIN))],
)
def remove_membership(org_id: uuid.UUID, membership_id: uuid.UUID) -> None:
    m = _MEMBERSHIPS.get(membership_id)
    if m is None or m.org_id != org_id:
        raise HTTPException(status_code=404, detail="membership not found")
    _MEMBERSHIPS.pop(membership_id, None)
