"""Common service API contracts shared by agents and features."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "healthy"
    version: str = ""


class CapabilitiesResponse(BaseModel):
    name: str = ""
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""
