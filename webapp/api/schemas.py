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


class ProjectMaterialOut(BaseModel):
    id: UUID
    original_filename: str
    content_type: str
    size_bytes: int
    status: str
    metadata: dict
    error: str | None
    created_at: datetime
    updated_at: datetime


class ProjectUpdateIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)


class OutlineSlideIn(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    purpose: str = Field(default="", max_length=500)
    kind: str = Field(default="内容页", max_length=64)
    notes: str = Field(default="", max_length=2_000)


class ProjectCreativeStateUpdateIn(BaseModel):
    stage: str | None = Field(default=None, max_length=32)
    requirements: dict | None = None
    outline: list[OutlineSlideIn] | None = None
    notes_enabled: bool | None = None
    selected_template_id: UUID | None = None


class ProjectCreativeOutlineIn(BaseModel):
    """Request a fresh outline from the saved creative requirements."""

    requirements: dict | None = None


class ProjectCreativeStateOut(BaseModel):
    project_id: UUID
    stage: str
    requirements: dict
    outline: list[OutlineSlideIn]
    notes_enabled: bool
    selected_template_id: UUID | None
    updated_at: datetime | None


class PageRefinementMessageOut(BaseModel):
    id: UUID
    job_id: UUID | None
    slide_number: int
    role: str
    content: str
    message_order: int
    created_at: datetime


class PageRefinementIntentIn(BaseModel):
    """Classify one page-scoped chat message before creating a PPT job."""

    slide_number: int = Field(ge=1, le=999)
    slide_title: str = Field(default="当前页面", min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=4_000)
    client_message_id: str | None = Field(default=None, min_length=8, max_length=64)


class PageRefinementIntentOut(BaseModel):
    action: str
    confidence: float = Field(ge=0, le=1)
    normalized_request: str = ""
    reply: str = ""
    clarification_question: str = ""


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
    scope: str = "user"
    is_active: bool = True
    sort_order: int = 0


class TemplateRenameIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)


class AdminTemplateUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    is_active: bool | None = None
    sort_order: int | None = None


class PromptSnippetCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=10_000)
    category: str = Field(default="个人", min_length=1, max_length=64)


class PromptSnippetUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=10_000)
    category: str | None = Field(default=None, min_length=1, max_length=64)


class PromptSnippetOut(BaseModel):
    id: UUID
    name: str
    content: str
    category: str
    used_count: int
    created_at: datetime
    updated_at: datetime
    scope: str = "user"
    is_active: bool = True
    sort_order: int = 0


class AdminPromptSnippetCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=10_000)
    category: str = Field(default="平台", min_length=1, max_length=64)
    is_active: bool = True
    sort_order: int = 0


class AdminPromptSnippetUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    content: str | None = Field(default=None, min_length=1, max_length=10_000)
    category: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None
    sort_order: int | None = None


class JobCreateIn(BaseModel):
    prompt: str = Field(min_length=1, max_length=20_000)
    model: str | None = Field(default=None, max_length=255)
    template_id: UUID | None = None
    base_job_id: UUID | None = None
    target_slide_number: int | None = Field(default=None, ge=1, le=999)
    conversation_message: str | None = Field(default=None, min_length=1, max_length=4_000)
    client_message_id: str | None = Field(default=None, min_length=8, max_length=64)


class JobOut(BaseModel):
    id: UUID
    project_id: UUID
    base_job_id: UUID | None
    target_slide_number: int | None
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
