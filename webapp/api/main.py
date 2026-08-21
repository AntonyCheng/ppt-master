"""FastAPI entry point for the multi-user PPT Master web service."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import SessionLocal, get_db
from .deps import get_current_user, get_owned_project, require_admin
from .models import (
    Artifact,
    ArtifactKind,
    AuthSession,
    Invitation,
    Job,
    JobEvent,
    JobStatus,
    ModelAccessPolicy,
    Project,
    Provider,
    ProviderModel,
    SystemSetting,
    User,
    UserRole,
)
from .schemas import (
    ArtifactOut,
    AdminUserCreateIn,
    AdminUserUpdateIn,
    EditorSlideOut,
    EditorSlideSaveIn,
    InviteIn,
    InviteOut,
    JobCreateIn,
    JobEventOut,
    JobOut,
    LoginIn,
    ModelCatalogDefaultIn,
    ModelCatalogOut,
    ModelOut,
    ProviderIn,
    ProviderModelIn,
    ProviderModelOut,
    ProviderModelUpdateIn,
    ProviderOut,
    ProviderUpdateIn,
    ProjectCreateIn,
    ProjectOut,
    RegisterIn,
    UserOut,
)
from .security import (
    hash_password,
    invitation_expiry,
    new_token,
    normalize_email,
    normalize_username,
    session_expiry,
    token_hash,
    verify_password,
)
from .provider_config import encrypt_api_key, key_hint

settings = get_settings()
FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
logger = logging.getLogger(__name__)


def _workspace_path(project: Project) -> Path:
    """Resolve a project workspace below the configured root."""

    root = settings.workspace_root.resolve()
    path = (root / project.workspace_relpath).resolve()
    if root not in path.parents:
        raise RuntimeError("Project workspace is outside WORKSPACE_ROOT")
    return path


def _editor_artifact_path(project: Project, artifact: Artifact) -> Path:
    """Resolve one SVG artifact while preserving the project directory boundary."""

    project_root = _workspace_path(project)
    path = (project_root / artifact.relative_path).resolve()
    if project_root not in path.parents or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found")
    return path


def _validate_editor_svg(content: str) -> None:
    """Reject active or structurally invalid SVG before storing browser edits."""

    if re.search(r"<!DOCTYPE|<!ENTITY", content, flags=re.IGNORECASE):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG 不支持 DTD 或实体声明")
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"SVG 格式无效：{exc}") from exc
    if root.tag.rsplit("}", 1)[-1] != "svg":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "编辑内容必须是 SVG 文档")
    for element in root.iter():
        tag = element.tag.rsplit("}", 1)[-1].lower()
        if tag in {"script", "foreignobject"}:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG 不允许脚本或嵌入页面")
        for key, value in element.attrib.items():
            name = key.rsplit("}", 1)[-1].lower()
            if name.startswith("on") or (
                name == "href" and value.lstrip().lower().startswith("javascript:")
            ):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "SVG 包含不支持的活动内容")


def _user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        is_active=user.is_active,
        deletion_pending=user.deletion_requested_at is not None,
    )


def _provider_out(provider: Provider, models: list[ProviderModel]) -> ProviderOut:
    return ProviderOut(
        id=provider.id, slug=provider.slug, display_name=provider.display_name,
        base_url=provider.base_url, api_key_hint=provider.api_key_hint, is_active=provider.is_active,
        models=[ProviderModelOut(id=model.id, model_id=model.model_id, display_name=model.display_name, is_active=model.is_active, is_default=model.is_default) for model in models],
    )


async def _model_catalog(db: AsyncSession) -> list[ModelCatalogOut]:
    """Return the merged environment and administrator-managed model catalog."""

    default_setting = await db.get(SystemSetting, "default_model_id")
    if default_setting is not None:
        default_model = default_setting.value or None
    else:
        legacy_default = (
            await db.execute(
                select(Provider, ProviderModel)
                .join(ProviderModel, ProviderModel.provider_id == Provider.id)
                .where(ProviderModel.is_default.is_(True))
                .order_by(Provider.display_name, ProviderModel.display_name)
                .limit(1)
            )
        ).first()
        default_model = (
            f"{legacy_default[0].slug}/{legacy_default[1].model_id}"
            if legacy_default
            else settings.configured_model
        )

    catalog: list[ModelCatalogOut] = []
    known_model_ids: set[str] = set()
    for model_id in settings.configured_models:
        provider_slug = model_id.partition("/")[0]
        catalog.append(
            ModelCatalogOut(
                model_id=model_id,
                source="environment",
                provider_display_name=provider_slug,
                is_available=True,
                is_default=model_id == default_model,
            )
        )
        known_model_ids.add(model_id)

    managed = await db.execute(
        select(Provider, ProviderModel)
        .join(ProviderModel, ProviderModel.provider_id == Provider.id)
        .order_by(Provider.display_name, ProviderModel.display_name)
    )
    for provider, provider_model in managed.all():
        model_id = f"{provider.slug}/{provider_model.model_id}"
        if model_id in known_model_ids:
            continue
        catalog.append(
            ModelCatalogOut(
                model_id=model_id,
                source="managed",
                provider_id=provider.id,
                provider_display_name=provider.display_name,
                is_available=provider.is_active and provider_model.is_active,
                is_default=model_id == default_model,
            )
        )
        known_model_ids.add(model_id)
    if not any(item.is_default for item in catalog):
        active_model = next((item for item in catalog if item.is_available), None)
        if active_model:
            active_model.is_default = True
    return catalog


async def _set_default_model(db: AsyncSession, model_id: str | None) -> None:
    """Persist an explicit global default, including an explicit no-default state."""

    setting = await db.get(SystemSetting, "default_model_id")
    if setting is None:
        setting = SystemSetting(key="default_model_id", value=model_id or "")
        db.add(setting)
    else:
        setting.value = model_id or ""


async def _clear_default_if_selected(db: AsyncSession, model_ids: list[str]) -> None:
    """Clear the global default only when an operation makes it unavailable."""

    selected = set(model_ids)
    if any(item.is_default and item.model_id in selected for item in await _model_catalog(db)):
        await _set_default_model(db, None)


async def _ensure_models_are_idle(db: AsyncSession, model_ids: list[str]) -> None:
    """Prevent lifecycle changes that would invalidate a queued or running job."""

    if not model_ids:
        return
    active_job = (
        await db.execute(
            select(Job.id)
            .where(
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                Job.model.in_(model_ids),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "该模型仍有排队或执行中的任务，任务结束后才能停用或删除。",
        )


def _project_out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        title=project.title,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        project_id=job.project_id,
        base_job_id=job.base_job_id,
        status=job.status,
        prompt=job.prompt,
        model=job.model,
        error=job.error,
        cancellation_requested=job.cancellation_requested,
        created_at=job.created_at,
    )


def _artifact_out(artifact: Artifact) -> ArtifactOut:
    return ArtifactOut(
        id=artifact.id,
        kind=artifact.kind.value,
        filename=Path(artifact.relative_path).name,
        content_type=artifact.content_type,
        size_bytes=artifact.size_bytes,
        created_at=artifact.created_at,
    )


async def _get_project_job(
    project: Project,
    job_id: UUID,
    db: AsyncSession,
) -> Job:
    """Return a job only when it belongs to the authorized project."""

    job = (
        await db.execute(select(Job).where(Job.id == job_id, Job.project_id == project.id))
    ).scalar_one_or_none()
    if not job:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job


async def _bootstrap_admin() -> None:
    """Create the configured first administrator when it does not exist."""

    if not settings.admin_username or not settings.admin_email or not settings.admin_password:
        return
    username = normalize_username(settings.admin_username)
    email = normalize_email(settings.admin_email)
    async with SessionLocal() as db:
        existing = (
            await db.execute(
                select(User).where(or_(User.username == username, User.email == email))
            )
        ).scalar_one_or_none()
        if existing:
            return
        db.add(
            User(
                username=username,
                email=email,
                password_hash=hash_password(settings.admin_password),
                display_name="Administrator",
                role=UserRole.ADMIN,
            )
        )
        await db.commit()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Initialize the workspace root and optional bootstrap account."""

    settings.workspace_root.mkdir(parents=True, exist_ok=True)
    await _bootstrap_admin()
    yield


app = FastAPI(
    title="PPT生成智能体 Web API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs" if settings.app_env != "production" else None,
    redoc_url=None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.public_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-Requested-With"],
)
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/api/v1/health")
async def health() -> dict[str, str]:
    """Return a dependency-free health response for Compose health checks."""

    return {"status": "ok", "environment": settings.app_env}


@app.post("/api/v1/auth/login", response_model=UserOut)
async def login(payload: LoginIn, response: Response, db: AsyncSession = Depends(get_db)) -> UserOut:
    """Authenticate a user and set a revocable HttpOnly browser session."""

    username = normalize_username(payload.username)
    user = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    raw_token = new_token()
    db.add(AuthSession(user_id=user.id, token_hash=token_hash(raw_token), expires_at=session_expiry()))
    await db.commit()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    return _user_out(user)


@app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Revoke every browser session for the authenticated user."""

    sessions = await db.execute(select(AuthSession).where(AuthSession.user_id == user.id))
    for session in sessions.scalars():
        await db.delete(session)
    await db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")


@app.get("/api/v1/auth/me", response_model=UserOut)
async def current_user(user: User = Depends(get_current_user)) -> UserOut:
    """Return the active user profile."""

    return _user_out(user)


@app.post("/api/v1/invites", response_model=InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invitation(
    payload: InviteIn,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> InviteOut:
    """Issue a one-time invitation token for a specific email address."""

    token = new_token()
    invitation = Invitation(
        email=normalize_email(str(payload.email)),
        token_hash=token_hash(token),
        created_by=admin.id,
        expires_at=invitation_expiry(),
    )
    db.add(invitation)
    await db.commit()
    await db.refresh(invitation)
    return InviteOut(
        id=invitation.id,
        email=invitation.email,
        token=token,
        expires_at=invitation.expires_at,
    )


@app.post("/api/v1/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: AsyncSession = Depends(get_db)) -> UserOut:
    """Redeem an invitation and create the invited user account."""

    now = datetime.now(UTC)
    stmt = (
        select(Invitation)
        .where(Invitation.token_hash == token_hash(payload.token))
        .where(Invitation.used_at.is_(None))
        .where(Invitation.expires_at > now)
        .with_for_update()
    )
    invitation = (await db.execute(stmt)).scalar_one_or_none()
    if not invitation:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invitation is invalid or expired")
    username = normalize_username(payload.username)
    existing = (
        await db.execute(
            select(User).where(
                or_(User.email == invitation.email, User.username == username)
            )
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "The invitation or username is already in use")
    user = User(
        username=username,
        email=invitation.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    db.add(user)
    await db.flush()
    invitation.used_by = user.id
    invitation.used_at = now
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


@app.get("/api/v1/admin/users", response_model=list[UserOut])
async def admin_list_users(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[UserOut]:
    """List accounts for the administrator console."""

    del admin
    users = (await db.execute(select(User).order_by(User.created_at.desc()))).scalars().all()
    return [_user_out(user) for user in users]


@app.post("/api/v1/admin/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def admin_create_user(payload: AdminUserCreateIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> UserOut:
    """Create an account directly instead of issuing an invitation."""

    del admin
    username = normalize_username(payload.username)
    if payload.password != payload.password_confirmation:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "两次输入的密码不一致")
    email = f"{username}@accounts.pptmaster.example"
    existing = (await db.execute(select(User).where(User.username == username))).scalar_one_or_none()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "账号已存在")
    user = User(
        username=username,
        email=email,
        display_name=username,
        password_hash=hash_password(payload.password),
        role=UserRole.MEMBER,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _user_out(user)


async def _ensure_admin_survives(target: User, db: AsyncSession, *, next_role: UserRole | None = None, next_active: bool | None = None, deleting: bool = False) -> None:
    removes_admin = target.role is UserRole.ADMIN and (deleting or next_role is UserRole.MEMBER or next_active is False)
    if not removes_admin:
        return
    active_admins = (await db.execute(select(func.count(User.id)).where(User.role == UserRole.ADMIN, User.is_active.is_(True), User.deletion_requested_at.is_(None)))).scalar_one()
    if active_admins <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "必须保留至少一个启用的管理员")


async def _purge_deleted_user(target: User, db: AsyncSession) -> None:
    """Delete a deactivated account and every record that references it."""

    workspace_root = settings.workspace_root.resolve()
    user_workspace = (workspace_root / str(target.id)).resolve()
    if workspace_root not in user_workspace.parents:
        raise RuntimeError("User workspace is outside WORKSPACE_ROOT")

    invitations = (
        await db.execute(
            select(Invitation).where(
                or_(
                    Invitation.created_by == target.id,
                    Invitation.used_by == target.id,
                )
            )
        )
    ).scalars().all()
    projects = (
        await db.execute(select(Project).where(Project.owner_id == target.id))
    ).scalars().all()
    for invitation in invitations:
        await db.delete(invitation)
    for project in projects:
        await db.delete(project)
    await db.delete(target)
    await db.commit()

    if user_workspace.exists():
        try:
            shutil.rmtree(user_workspace)
        except OSError:
            logger.exception("Failed to remove deleted user workspace: %s", user_workspace)


@app.patch("/api/v1/admin/users/{user_id}", response_model=UserOut)
async def admin_update_user(user_id: UUID, payload: AdminUserUpdateIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> UserOut:
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    await _ensure_admin_survives(target, db, next_role=payload.role, next_active=payload.is_active)
    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active
    await db.commit()
    await db.refresh(target)
    return _user_out(target)


@app.delete("/api/v1/admin/users/{user_id}", response_model=UserOut, status_code=status.HTTP_202_ACCEPTED)
async def admin_delete_user(user_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> UserOut:
    """Deactivate an account and request cancellation before deferred removal."""

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if target.id == admin.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "不能删除当前登录的管理员")
    await _ensure_admin_survives(target, db, deleting=True)
    target.is_active = False
    target.deletion_requested_at = datetime.now(UTC)
    jobs = (await db.execute(select(Job).join(Project).where(Project.owner_id == target.id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])))).scalars().all()
    for job in jobs:
        job.cancellation_requested = True
        if job.status is JobStatus.QUEUED:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(UTC)
    await db.commit()
    response = _user_out(target)
    if not jobs:
        await _purge_deleted_user(target, db)
    return response


@app.get("/api/v1/admin/providers", response_model=list[ProviderOut])
async def admin_list_providers(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[ProviderOut]:
    del admin
    providers = (await db.execute(select(Provider).order_by(Provider.display_name))).scalars().all()
    models = (await db.execute(select(ProviderModel).order_by(ProviderModel.display_name))).scalars().all()
    by_provider: dict[UUID, list[ProviderModel]] = {}
    for model in models:
        by_provider.setdefault(model.provider_id, []).append(model)
    return [_provider_out(provider, by_provider.get(provider.id, [])) for provider in providers]


@app.post("/api/v1/admin/providers", response_model=ProviderOut, status_code=status.HTTP_201_CREATED)
async def admin_create_provider(payload: ProviderIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderOut:
    del admin
    provider = Provider(slug=payload.slug, display_name=payload.display_name.strip(), base_url=payload.base_url.rstrip("/"), api_key_ciphertext=encrypt_api_key(payload.api_key), api_key_hint=key_hint(payload.api_key), is_active=payload.is_active)
    db.add(provider)
    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Provider 标识已存在") from exc
    await db.refresh(provider)
    return _provider_out(provider, [])


@app.patch("/api/v1/admin/providers/{provider_id}", response_model=ProviderOut)
async def admin_update_provider(provider_id: UUID, payload: ProviderUpdateIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderOut:
    del admin
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    provider_models = (
        await db.execute(select(ProviderModel).where(ProviderModel.provider_id == provider.id))
    ).scalars().all()
    model_ids = [f"{provider.slug}/{item.model_id}" for item in provider_models]
    if payload.is_active is False and provider.is_active:
        await _ensure_models_are_idle(db, model_ids)
        await _clear_default_if_selected(db, model_ids)
    if payload.display_name is not None: provider.display_name = payload.display_name.strip()
    if payload.base_url is not None: provider.base_url = payload.base_url.rstrip("/")
    if payload.is_active is not None: provider.is_active = payload.is_active
    if payload.api_key is not None:
        provider.api_key_ciphertext, provider.api_key_hint = encrypt_api_key(payload.api_key), key_hint(payload.api_key)
    await db.commit()
    return _provider_out(provider, provider_models)


@app.delete("/api/v1/admin/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_provider(provider_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> None:
    """Delete one managed provider after its active jobs have completed."""

    del admin
    provider = await db.get(Provider, provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    provider_models = (
        await db.execute(select(ProviderModel).where(ProviderModel.provider_id == provider.id))
    ).scalars().all()
    model_ids = [f"{provider.slug}/{item.model_id}" for item in provider_models]
    await _ensure_models_are_idle(db, model_ids)
    await _clear_default_if_selected(db, model_ids)
    policies = (
        await db.execute(
            select(ModelAccessPolicy).where(ModelAccessPolicy.model_id.in_(model_ids))
        )
    ).scalars().all()
    for policy in policies:
        await db.delete(policy)
    await db.delete(provider)
    await db.commit()


@app.post("/api/v1/admin/providers/{provider_id}/models", response_model=ProviderModelOut, status_code=status.HTTP_201_CREATED)
async def admin_create_model(provider_id: UUID, payload: ProviderModelIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderModelOut:
    del admin
    if not await db.get(Provider, provider_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    model = ProviderModel(
        provider_id=provider_id,
        model_id=payload.model_id.strip(),
        display_name=payload.display_name.strip(),
        is_active=payload.is_active,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    return ProviderModelOut(id=model.id, model_id=model.model_id, display_name=model.display_name, is_active=model.is_active, is_default=model.is_default)


@app.patch("/api/v1/admin/models/{model_id}", response_model=ProviderModelOut)
async def admin_update_model(model_id: UUID, payload: ProviderModelUpdateIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> ProviderModelOut:
    del admin
    model = await db.get(ProviderModel, model_id)
    if not model: raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    provider = await db.get(Provider, model.provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    catalog_id = f"{provider.slug}/{model.model_id}"
    if payload.is_active is False and model.is_active:
        await _ensure_models_are_idle(db, [catalog_id])
        await _clear_default_if_selected(db, [catalog_id])
    if payload.display_name is not None: model.display_name = payload.display_name.strip()
    if payload.is_active is not None: model.is_active = payload.is_active
    await db.commit()
    return ProviderModelOut(id=model.id, model_id=model.model_id, display_name=model.display_name, is_active=model.is_active, is_default=model.is_default)


@app.delete("/api/v1/admin/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def admin_delete_model(model_id: UUID, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> None:
    """Delete one managed model after its active jobs have completed."""

    del admin
    model = await db.get(ProviderModel, model_id)
    if not model:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "模型不存在")
    provider = await db.get(Provider, model.provider_id)
    if not provider:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Provider 不存在")
    catalog_id = f"{provider.slug}/{model.model_id}"
    await _ensure_models_are_idle(db, [catalog_id])
    await _clear_default_if_selected(db, [catalog_id])
    policy = await db.get(ModelAccessPolicy, catalog_id)
    if policy:
        await db.delete(policy)
    await db.delete(model)
    await db.commit()


@app.get("/api/v1/admin/model-catalog", response_model=list[ModelCatalogOut])
async def admin_list_model_catalog(admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[ModelCatalogOut]:
    """List every model the platform knows, including disabled entries."""

    del admin
    return await _model_catalog(db)


@app.patch("/api/v1/admin/model-catalog/default", response_model=list[ModelCatalogOut])
async def admin_update_default_model(payload: ModelCatalogDefaultIn, admin: User = Depends(require_admin), db: AsyncSession = Depends(get_db)) -> list[ModelCatalogOut]:
    """Set the single platform-wide model used by every generation job."""

    del admin
    catalog = await _model_catalog(db)
    entry = next((item for item in catalog if item.model_id == payload.model_id), None)
    if not entry or not entry.is_available:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只能激活当前可用的模型")
    await _set_default_model(db, payload.model_id)
    await db.commit()
    return await _model_catalog(db)


@app.get("/api/v1/projects", response_model=list[ProjectOut])
async def list_projects(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    """List projects that belong to the authenticated user."""

    stmt = select(Project).where(Project.owner_id == user.id).order_by(Project.updated_at.desc())
    projects = (await db.execute(stmt)).scalars().all()
    return [_project_out(project) for project in projects]


@app.post("/api/v1/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateIn,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    """Create a user-owned project directory and database record."""

    project = Project(owner_id=user.id, title=payload.title.strip(), workspace_relpath="pending")
    db.add(project)
    await db.flush()
    project.workspace_relpath = f"{user.id}/{project.id}"
    _workspace_path(project).mkdir(parents=True, exist_ok=False)
    await db.commit()
    await db.refresh(project)
    return _project_out(project)


@app.get("/api/v1/projects/{project_id}", response_model=ProjectOut)
async def get_project(project: Project = Depends(get_owned_project)) -> ProjectOut:
    """Return a project owned by the authenticated user."""

    return _project_out(project)


@app.delete("/api/v1/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Delete an owned project, its jobs, and its isolated workspace."""

    active_job = (
        await db.execute(
            select(Job.id)
            .where(
                and_(
                    Job.project_id == project.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "当前对话仍有任务在执行，请等待完成后再删除。",
        )

    workspace_path = _workspace_path(project)
    await db.delete(project)
    await db.commit()
    if workspace_path.exists():
        try:
            shutil.rmtree(workspace_path)
        except OSError:
            logger.exception("Failed to remove deleted project workspace: %s", workspace_path)


@app.post(
    "/api/v1/projects/{project_id}/jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_job(
    payload: JobCreateIn,
    project: Project = Depends(get_owned_project),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    """Queue a project generation job for the isolated runner service."""

    active_model = next(
        (item for item in await _model_catalog(db) if item.is_available and item.is_default),
        None,
    )
    if not active_model:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "OpenCode 尚未配置模型。请先设置 PPTMASTER_DEFAULT_MODEL 及对应提供方的凭据。",
        )
    if payload.model and payload.model.strip() != active_model.model_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "模型由管理员统一配置，不能在任务中单独选择")
    model = active_model.model_id

    # Lock the conversation row while choosing its base revision. This keeps
    # two browser tabs from starting concurrent edits against the same version.
    locked_project = (
        await db.execute(select(Project).where(Project.id == project.id).with_for_update())
    ).scalar_one()
    active_job = (
        await db.execute(
            select(Job.id)
            .where(
                and_(
                    Job.project_id == locked_project.id,
                    Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active_job:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "当前对话仍有任务在执行，请等待完成后再继续修改。",
        )
    base_job = (
        await db.execute(
            select(Job)
            .where(Job.project_id == locked_project.id, Job.status == JobStatus.SUCCEEDED)
            .order_by(Job.finished_at.desc(), Job.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    job = Job(
        project_id=locked_project.id,
        base_job_id=base_job.id if base_job else None,
        submitted_by=user.id,
        prompt=payload.prompt.strip(),
        model=model,
    )
    locked_project.updated_at = datetime.now(UTC)
    db.add(job)
    await db.commit()
    await db.refresh(job)
    from runner.celery_app import celery_app

    celery_app.send_task("runner.execute_job", args=[str(job.id)])
    return _job_out(job)


@app.get("/api/v1/models", response_model=list[ModelOut])
async def list_models(user: User = Depends(get_current_user)) -> list[ModelOut]:
    """List the server-approved OpenCode models available to the signed-in user."""

    del user
    async with SessionLocal() as db:
        catalog = await _model_catalog(db)
    return [
        ModelOut(id=item.model_id, is_default=item.is_default)
        for item in catalog
        if item.is_available and item.is_default
    ]


@app.get("/api/v1/projects/{project_id}/jobs", response_model=list[JobOut])
async def list_jobs(
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[JobOut]:
    """List recent generation jobs for one project owned by the current user."""

    stmt = select(Job).where(Job.project_id == project.id).order_by(Job.created_at.desc())
    jobs = (await db.execute(stmt)).scalars().all()
    return [_job_out(job) for job in jobs]


@app.post(
    "/api/v1/projects/{project_id}/jobs/{job_id}/cancel",
    response_model=JobOut,
)
async def cancel_job(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> JobOut:
    """Request cancellation for one queued or running job owned by the user."""

    job = await _get_project_job(project, job_id, db)
    if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
        return _job_out(job)
    job.cancellation_requested = True
    if job.status is JobStatus.QUEUED:
        job.status = JobStatus.CANCELLED
        job.finished_at = datetime.now(UTC)
        db.add(JobEvent(job_id=job.id, event_type="status", payload={"status": "cancelled"}))
    else:
        db.add(JobEvent(job_id=job.id, event_type="agent", payload={"text": "正在中止 OpenCode 任务..."}))
    await db.commit()
    await db.refresh(job)
    return _job_out(job)


@app.get(
    "/api/v1/projects/{project_id}/jobs/{job_id}/events",
    response_model=list[JobEventOut],
)
async def list_job_events(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[JobEventOut]:
    """Replay all persisted events for an authorized generation job."""

    await _get_project_job(project, job_id, db)
    stmt = select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
    events = (await db.execute(stmt)).scalars().all()
    return [
        JobEventOut(
            id=event.id,
            event_type=event.event_type,
            payload=event.payload,
            created_at=event.created_at,
        )
        for event in events
    ]


@app.get("/api/v1/projects/{project_id}/jobs/{job_id}/events/stream")
async def stream_job_events(
    request: Request,
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream persisted job events so reconnecting clients can resume safely."""

    await _get_project_job(project, job_id, db)
    last_event_id = int(request.headers.get("Last-Event-ID", "0") or 0)

    async def event_stream() -> AsyncIterator[str]:
        nonlocal last_event_id
        while True:
            async with SessionLocal() as stream_db:
                stmt = (
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.id > last_event_id)
                    .order_by(JobEvent.id)
                )
                events = (await stream_db.execute(stmt)).scalars().all()
                current_job = await stream_db.get(Job, job_id)
                latest_editor_export = (
                    await stream_db.execute(
                        select(JobEvent)
                        .where(
                            JobEvent.job_id == job_id,
                            JobEvent.event_type == "editor_export",
                        )
                        .order_by(JobEvent.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                editor_export_active = (
                    latest_editor_export is not None
                    and str(latest_editor_export.payload.get("status", ""))
                    in {"queued", "exporting"}
                )
            for event in events:
                last_event_id = event.id
                data = json.dumps(
                    {
                        "id": event.id,
                        "event_type": event.event_type,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                    },
                    ensure_ascii=False,
                )
                yield f"id: {event.id}\nevent: job-event\ndata: {data}\n\n"
            if current_job and current_job.status.value in {"succeeded", "failed", "cancelled"}:
                if not editor_export_active:
                    yield "event: complete\ndata: {}\n\n"
                    return
            if await request.is_disconnected():
                return
            yield ": keepalive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get(
    "/api/v1/projects/{project_id}/jobs/{job_id}/artifacts",
    response_model=list[ArtifactOut],
)
async def list_job_artifacts(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[ArtifactOut]:
    """List artifacts produced by an authorized job."""

    await _get_project_job(project, job_id, db)
    stmt = select(Artifact).where(Artifact.job_id == job_id).order_by(Artifact.created_at)
    artifacts = (await db.execute(stmt)).scalars().all()
    return [_artifact_out(artifact) for artifact in artifacts]


@app.get("/api/v1/projects/{project_id}/jobs/{job_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    job_id: UUID,
    artifact_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Download one artifact after project and job ownership checks."""

    await _get_project_job(project, job_id, db)
    artifact = (
        await db.execute(
            select(Artifact).where(Artifact.id == artifact_id, Artifact.job_id == job_id)
        )
    ).scalar_one_or_none()
    if not artifact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact not found")
    project_root = _workspace_path(project)
    artifact_path = (project_root / artifact.relative_path).resolve()
    if project_root not in artifact_path.parents or not artifact_path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Artifact file not found")
    return FileResponse(
        artifact_path,
        media_type=artifact.content_type,
        filename=Path(artifact.relative_path).name,
    )


@app.get(
    "/api/v1/projects/{project_id}/jobs/{job_id}/editor/slides",
    response_model=list[EditorSlideOut],
)
async def list_editor_slides(
    job_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> list[EditorSlideOut]:
    """List SVG authoring pages for one finished, user-owned task."""

    job = await _get_project_job(project, job_id, db)
    if job.status is not JobStatus.SUCCEEDED:
        raise HTTPException(status.HTTP_409_CONFLICT, "任务完成后才可以进入手动编辑")
    slides = (
        await db.execute(
            select(Artifact)
            .where(Artifact.job_id == job.id, Artifact.kind == ArtifactKind.SVG)
            .order_by(Artifact.relative_path)
        )
    ).scalars().all()
    return [
        EditorSlideOut(
            id=slide.id,
            filename=Path(slide.relative_path).name,
            size_bytes=slide.size_bytes,
        )
        for slide in slides
    ]


async def _get_editor_slide(
    project: Project,
    job_id: UUID,
    artifact_id: UUID,
    db: AsyncSession,
) -> Artifact:
    await _get_project_job(project, job_id, db)
    artifact = (
        await db.execute(
            select(Artifact).where(
                Artifact.id == artifact_id,
                Artifact.job_id == job_id,
                Artifact.kind == ArtifactKind.SVG,
            )
        )
    ).scalar_one_or_none()
    if artifact is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "SVG 页面不存在")
    return artifact


@app.get("/api/v1/projects/{project_id}/jobs/{job_id}/editor/slides/{artifact_id}")
async def get_editor_slide(
    job_id: UUID,
    artifact_id: UUID,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Serve one editable SVG inline after task ownership verification."""

    artifact = await _get_editor_slide(project, job_id, artifact_id, db)
    content = _editor_artifact_path(project, artifact).read_text(encoding="utf-8")
    return Response(content, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


@app.put("/api/v1/projects/{project_id}/jobs/{job_id}/editor/slides/{artifact_id}")
async def save_editor_slide(
    job_id: UUID,
    artifact_id: UUID,
    payload: EditorSlideSaveIn,
    project: Project = Depends(get_owned_project),
    db: AsyncSession = Depends(get_db),
) -> EditorSlideOut:
    """Persist one browser-edited SVG source page for a finished task."""

    active = (
        await db.execute(
            select(Job.id)
            .where(
                Job.project_id == project.id,
                Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if active:
        raise HTTPException(status.HTTP_409_CONFLICT, "当前对话仍有任务在执行，暂不能手动保存")
    artifact = await _get_editor_slide(project, job_id, artifact_id, db)
    _validate_editor_svg(payload.content)
    artifact_path = _editor_artifact_path(project, artifact)
    artifact_path.write_text(payload.content, encoding="utf-8")
    artifact.size_bytes = artifact_path.stat().st_size
    db.add(
        JobEvent(
            job_id=job_id,
            event_type="editor_export",
            payload={
                "status": "queued",
                "text": "已保存 SVG，正在导出新版演示文稿。",
            },
        )
    )
    await db.commit()
    from runner.celery_app import celery_app

    celery_app.send_task("runner.export_editor_revision", args=[str(job_id)])
    return EditorSlideOut(
        id=artifact.id,
        filename=Path(artifact.relative_path).name,
        size_bytes=artifact.size_bytes,
    )


@app.get("/")
async def frontend() -> FileResponse:
    """Serve the built React application when present in the image."""

    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Frontend build is not available")
    return FileResponse(index)


@app.get("/{frontend_path:path}", include_in_schema=False)
async def frontend_route(frontend_path: str) -> FileResponse:
    """Serve built static files, then fall back to the SPA shell for app routes."""

    if frontend_path.startswith("api/"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    # Vite copies public files such as the application logo to the dist root.
    # They must be served as files before treating an unknown path as an SPA route.
    static_file = (FRONTEND_DIST / frontend_path).resolve()
    if FRONTEND_DIST.resolve() in static_file.parents and static_file.is_file():
        return FileResponse(static_file)
    return await frontend()
    ModelOut,
