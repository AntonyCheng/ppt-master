"""Pydantic request and response schemas for the public API."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from .models import JobStatus, UserRole


class UserOut(BaseModel):
    id: UUID
    username: str
    email: EmailStr
    display_name: str
    role: UserRole
    is_active: bool = True
    deletion_pending: bool = False


class AdminUserCreateIn(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$")
    password: str = Field(min_length=8, max_length=256)
    password_confirmation: str = Field(min_length=8, max_length=256)


class AdminUserUpdateIn(BaseModel):
    role: UserRole | None = None
    is_active: bool | None = None


class LoginIn(BaseModel):
    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$",
    )
    password: str = Field(min_length=8, max_length=256)


class InviteIn(BaseModel):
    email: EmailStr


class InviteOut(BaseModel):
    id: UUID
    email: EmailStr
    token: str
    expires_at: datetime


class RegisterIn(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    username: str = Field(
        min_length=3,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{2,63}$",
    )
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=8, max_length=256)


class ProjectCreateIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class ProjectOut(BaseModel):
    id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class TemplateOut(BaseModel):
    id: UUID
    name: str
    original_filename: str
    status: str
    page_count: int | None
    metadata: dict
    error: str | None
    created_at: datetime
    updated_at: datetime


class JobCreateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    model: str | None = Field(default=None, max_length=255)
    template_id: UUID | None = None


class JobOut(BaseModel):
    id: UUID
    project_id: UUID
    base_job_id: UUID | None
    template_id: UUID | None
    template_name: str | None
    status: JobStatus
    prompt: str
    model: str | None
    error: str | None
    cancellation_requested: bool
    created_at: datetime


class ModelOut(BaseModel):
    id: str
    is_default: bool = False


class ModelCatalogOut(BaseModel):
    model_id: str
    source: str
    provider_id: UUID | None = None
    provider_display_name: str | None = None
    is_available: bool
    is_default: bool


class ModelCatalogDefaultIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=255)


class ProviderModelIn(BaseModel):
    model_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=160)
    is_active: bool = True
    is_default: bool = False


class ProviderModelUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    is_active: bool | None = None
    is_default: bool | None = None


class ProviderIn(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=1024)
    api_key: str = Field(min_length=1, max_length=4096)
    is_active: bool = True


class ProviderUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=1024)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    is_active: bool | None = None


class ProviderModelOut(BaseModel):
    id: UUID
    model_id: str
    display_name: str
    is_active: bool
    is_default: bool


class ProviderOut(BaseModel):
    id: UUID
    slug: str
    display_name: str
    base_url: str
    api_key_hint: str
    is_active: bool
    models: list[ProviderModelOut]


class JobEventOut(BaseModel):
    id: int
    event_type: str
    payload: dict
    created_at: datetime


class ArtifactOut(BaseModel):
    id: UUID
    kind: str
    filename: str
    content_type: str
    size_bytes: int
    created_at: datetime


class EditorSlideOut(BaseModel):
    """Describe one SVG source page available to the browser editor."""

    id: UUID
    filename: str
    size_bytes: int


class EditorSlideSaveIn(BaseModel):
    """Carry one complete, browser-edited SVG document."""

    content: str = Field(min_length=1, max_length=3_000_000)
