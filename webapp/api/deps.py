"""FastAPI dependency functions for authenticated and authorized requests."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .database import get_db
from .models import AuthSession, Project, User, UserRole
from .security import token_hash


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    """Resolve the current user from a revocable HttpOnly session cookie."""

    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")

    stmt = (
        select(User)
        .join(AuthSession, AuthSession.user_id == User.id)
        .where(AuthSession.token_hash == token_hash(token))
        .where(AuthSession.expires_at > datetime.now(UTC))
        .where(User.is_active.is_(True))
    )
    user = (await db.execute(stmt)).scalar_one_or_none()
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require the application administrator role."""

    if user.role is not UserRole.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator role required")
    return user


async def get_owned_project(
    project_id: UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    """Return a project only when it belongs to the current user."""

    stmt = select(Project).where(Project.id == project_id, Project.owner_id == user.id)
    project = (await db.execute(stmt)).scalar_one_or_none()
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project
