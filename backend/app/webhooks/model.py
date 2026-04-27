"""Pydantic models mirroring the ``webhooks`` / ``webhook_deliveries`` tables."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, Field, HttpUrl


class Webhook(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    url: HttpUrl
    secret: str
    events: list[str] = Field(default_factory=list)
    enabled: bool = True
    created_at: dt.datetime | None = None


class WebhookIn(BaseModel):
    url: HttpUrl
    secret: str = Field(..., min_length=8, max_length=512)
    events: list[str] = Field(default_factory=list)
    enabled: bool = True


class WebhookOut(BaseModel):
    id: uuid.UUID
    url: str
    events: list[str]
    enabled: bool
    created_at: dt.datetime | None = None


class WebhookDelivery(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    webhook_id: uuid.UUID
    event: str
    status: str = "pending"  # pending|ok|error
    attempts: int = 0
    last_error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime | None = None
