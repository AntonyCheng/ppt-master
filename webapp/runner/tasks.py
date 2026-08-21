"""Queue consumers that launch one restricted worker container per job."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import threading
import time
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import UUID

import docker
from celery.utils.log import get_task_logger
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from api.config import get_settings
from api.models import (
    Artifact,
    ArtifactKind,
    Invitation,
    Job,
    JobEvent,
    JobStatus,
    Project,
    Provider,
    ProviderModel,
    User,
)
from api.provider_config import opencode_config
from .celery_app import celery_app

logger = get_task_logger(__name__)
settings = get_settings()
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_engine = create_engine(settings.sync_database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(_engine, expire_on_commit=False)


def _host_bind_source(root: str | Path, relative_path: str = "") -> str:
    """Join a Docker-host path without resolving it inside the runner container."""

    root_text = str(root).rstrip("/\\")
    relative = PurePosixPath(relative_path.replace("\\", "/"))
    if not root_text:
        raise RuntimeError("Host bind source is not configured")
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("Host bind relative path is invalid")
    return f"{root_text}/{relative.as_posix()}" if relative_path else root_text


def _host_workspace_path(project: Project) -> str:
    """Resolve a safe Docker-host bind source for exactly one project."""

    return _host_bind_source(settings.host_projects_root, project.workspace_relpath)


def _host_job_workspace_path(project: Project, job: Job) -> str:
    """Resolve the Docker-host bind source for exactly one job workspace."""

    return _host_bind_source(_host_workspace_path(project), f"jobs/{job.id}")


def _project_workspace_path(project: Project) -> Path:
    """Resolve the runner-visible workspace used to discover output artifacts."""

    root = settings.workspace_root.resolve()
    path = (root / project.workspace_relpath).resolve()
    if root not in path.parents:
        raise RuntimeError("Project workspace is outside WORKSPACE_ROOT")
    return path


def _job_workspace_path(project: Project, job: Job) -> Path:
    """Resolve one job's isolated workspace below its authorized project root."""

    project_root = _project_workspace_path(project)
    path = (project_root / "jobs" / str(job.id)).resolve()
    if project_root not in path.parents:
        raise RuntimeError("Job workspace is outside the project workspace")
    return path


def _job_workspace_path_by_id(project: Project, job_id: UUID) -> Path:
    """Resolve a historical Job workspace under the same authorized project."""

    project_root = _project_workspace_path(project)
    path = (project_root / "jobs" / str(job_id)).resolve()
    if project_root not in path.parents:
        raise RuntimeError("Base job workspace is outside the project workspace")
    return path


def _prepare_job_workspace(project: Project, job: Job) -> Path:
    """Create one worker workspace and make only that directory worker-writable.

    The runner creates directories as root, while the short-lived worker runs as
    an unprivileged UID. Linux bind mounts can use chown; Docker Desktop bind
    mounts may reject it, so the narrowly scoped job directory falls back to
    mode 0777 instead of weakening the project root.
    """

    path = _job_workspace_path(project, job)
    path.mkdir(parents=True, exist_ok=False)
    worker_uid = settings.worker_uid
    worker_gid = settings.worker_gid
    try:
        if hasattr(os, "chown"):
            os.chown(path, worker_uid, worker_gid)
            path.chmod(0o750)
            return path
    except OSError:
        logger.info("Unable to chown job workspace %s; using writable mode", path)
    try:
        path.chmod(0o777)
    except OSError as exc:
        raise RuntimeError(f"Job workspace is not writable: {path}") from exc
    return path


def _source_project_root(source_root: Path) -> Path:
    """Find the actual authoring root inside a historical job workspace."""

    if (source_root / "svg_output").is_dir():
        return source_root
    candidates = [
        item
        for item in source_root.iterdir()
        if item.is_dir() and (item / "svg_output").is_dir()
    ]
    if len(candidates) != 1:
        raise RuntimeError("Base PPT Master workspace has no unique authoring root")
    return candidates[0]


def _grant_worker_write_access(root: Path) -> None:
    """Make a copied revision writable by exactly the configured worker user."""

    entries = [root, *root.rglob("*")]
    try:
        for entry in entries:
            os.chown(entry, settings.worker_uid, settings.worker_gid)
        return
    except OSError:
        logger.info("Unable to chown copied workspace %s; widening only this job tree", root)
    for entry in entries:
        try:
            if entry.is_dir():
                entry.chmod(0o777)
            else:
                entry.chmod(entry.stat().st_mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        except OSError as exc:
            raise RuntimeError(f"Copied job workspace is not writable: {entry}") from exc


def _grant_editor_write_access(root: Path) -> None:
    """Transfer one completed job workspace to the API user for SVG editing."""

    try:
        web_uid = int(os.environ.get("PPTMASTER_WEB_UID", "10001"))
    except ValueError as exc:
        raise RuntimeError("PPTMASTER_WEB_UID must be numeric") from exc
    entries = [root, *root.rglob("*")]
    try:
        for entry in entries:
            os.chown(entry, web_uid, web_uid)
        return
    except OSError:
        logger.info("Unable to chown editor workspace %s; widening only this job tree", root)
    for entry in entries:
        try:
            if entry.is_dir():
                entry.chmod(0o777)
            else:
                entry.chmod(entry.stat().st_mode | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        except OSError as exc:
            raise RuntimeError(f"Completed job workspace is not editor-writable: {entry}") from exc


def _seed_job_workspace(project: Project, job: Job, destination: Path) -> bool:
    """Copy the last revision into a stable authoring root for this job."""

    if not job.base_job_id:
        return False
    source_root = _job_workspace_path_by_id(project, job.base_job_id)
    if not source_root.is_dir() or not any(source_root.iterdir()):
        raise RuntimeError(f"Base job workspace is empty: {job.base_job_id}")
    source_root = _source_project_root(source_root)
    # Never follow links from generated content during a cross-revision copy.
    if any(item.is_symlink() for item in source_root.rglob("*")):
        raise RuntimeError(f"Base job workspace contains unsupported symlinks: {job.base_job_id}")
    target_root = destination / "ppt-project"
    target_root.mkdir(parents=True, exist_ok=False)
    for source in source_root.iterdir():
        target = target_root / source.name
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            shutil.copy2(source, target)
    return True


def _provider_environment() -> dict[str, str]:
    """Forward only explicitly allowlisted provider credentials to a job worker."""

    names = os.environ.get("PPTMASTER_OPENCODE_ENV_ALLOWLIST", "").split(",")
    environment: dict[str, str] = {}
    for raw_name in names:
        name = raw_name.strip()
        if not name or not _ENV_NAME_RE.fullmatch(name):
            continue
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _load_job(job_id: UUID) -> tuple[Job, Project]:
    """Load a queued job, or persist its cancellation before it starts."""

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            raise RuntimeError(f"Job {job_id} was not found")
        project = db.get(Project, job.project_id)
        if not project:
            raise RuntimeError(f"Project {job.project_id} was not found")
        if job.cancellation_requested or job.status is JobStatus.CANCELLED:
            job.status = JobStatus.CANCELLED
            job.finished_at = datetime.now(UTC)
            db.add(JobEvent(job_id=job.id, event_type="status", payload={"status": "cancelled"}))
            db.commit()
            return job, project
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        db.add(JobEvent(job_id=job.id, event_type="status", payload={"status": "running"}))
        db.commit()
        return job, project


def _record_event(job_id: UUID, event: dict) -> None:
    """Persist one worker event without exposing database credentials to the worker."""

    with SessionLocal() as db:
        db.add(JobEvent(job_id=job_id, event_type=str(event.get("type", "agent")), payload=event))
        db.commit()


def _finish_job(job_id: UUID, succeeded: bool, error: str | None = None) -> None:
    """Persist terminal state and discover the job's exported artifacts."""

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        project = db.get(Project, job.project_id)
        if not project:
            return
        job.status = JobStatus.SUCCEEDED if succeeded else (JobStatus.CANCELLED if job.cancellation_requested else JobStatus.FAILED)
        job.error = error
        job.finished_at = datetime.now(UTC)
        db.add(JobEvent(job_id=job.id, event_type="status", payload={"status": job.status.value}))
        project_workspace = _project_workspace_path(project)
        job_workspace = _job_workspace_path(project, job)
        _discover_job_artifacts(db, job, project, project_workspace, job_workspace)
        _grant_editor_write_access(job_workspace)
        owner_id = project.owner_id
        db.commit()
    _finalize_pending_user_deletion(owner_id)


def _discover_job_artifacts(
    db,
    job: Job,
    project: Project,
    project_workspace: Path,
    job_workspace: Path,
) -> None:
    """Register newly exported SVG and PPTX files for an existing task."""

    try:
        authoring_root = _source_project_root(job_workspace)
        for folder, kind, content_type in (
            ("svg_output", ArtifactKind.SVG, "image/svg+xml"),
            (
                "exports",
                ArtifactKind.PPTX,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
        ):
            pattern = "*.svg" if kind == ArtifactKind.SVG else "*.pptx"
            artifact_dir = authoring_root / folder
            if not artifact_dir.is_dir():
                continue
            for artifact_path in artifact_dir.glob(pattern):
                relative_path = str(artifact_path.relative_to(project_workspace))
                exists = db.execute(
                    select(Artifact).where(
                        Artifact.job_id == job.id,
                        Artifact.relative_path == relative_path,
                    )
                ).scalar_one_or_none()
                if exists:
                    exists.size_bytes = artifact_path.stat().st_size
                    continue
                db.add(
                    Artifact(
                        project_id=project.id,
                        job_id=job.id,
                        kind=kind,
                        relative_path=relative_path,
                        content_type=content_type,
                        size_bytes=artifact_path.stat().st_size,
                    )
                )
    except (OSError, RuntimeError, ValueError) as exc:
        logger.warning("Skipping artifact discovery for job %s: %s", job.id, exc)


def _finalize_pending_user_deletion(user_id: UUID) -> None:
    """Remove a deactivated account only after every worker has stopped."""
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if not user or user.deletion_requested_at is None:
            return
        active = db.execute(select(Job.id).join(Project).where(Project.owner_id == user.id, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))).scalar_one_or_none()
        if active:
            return
        user_root = (settings.workspace_root.resolve() / str(user.id)).resolve()
        root = settings.workspace_root.resolve()
        if root not in user_root.parents:
            raise RuntimeError("User workspace is outside WORKSPACE_ROOT")
        invitations = db.execute(
            select(Invitation).where(
                (Invitation.created_by == user.id) | (Invitation.used_by == user.id)
            )
        ).scalars().all()
        projects = db.execute(select(Project).where(Project.owner_id == user.id)).scalars().all()
        for invitation in invitations:
            db.delete(invitation)
        for project in projects:
            db.delete(project)
        db.delete(user)
        db.commit()
    if user_root.exists():
        try:
            shutil.rmtree(user_root)
        except OSError:
            logger.exception("Failed to remove deleted user workspace: %s", user_root)


def _job_opencode_config(job: Job) -> str | None:
    """Build an ephemeral config for a database-managed model."""
    if not job.model:
        return None
    with SessionLocal() as db:
        result = db.execute(select(Provider, ProviderModel).join(ProviderModel, ProviderModel.provider_id == Provider.id).where(Provider.is_active.is_(True), ProviderModel.is_active.is_(True)))
        for provider, model in result.all():
            if f"{provider.slug}/{model.model_id}" == job.model:
                return json.dumps(opencode_config(provider, model), ensure_ascii=False)
    return None


def _watch_cancellation(job_id: UUID, container, stopped: threading.Event, cancelled: threading.Event) -> None:
    while not stopped.wait(1):
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            should_cancel = not job or job.cancellation_requested
        if should_cancel:
            cancelled.set()
            try:
                container.stop(timeout=10)
            except docker.errors.APIError:
                pass
            return


@celery_app.task(name="runner.execute_job", bind=True)
def execute_job(self, job_id_text: str) -> None:
    """Run a generation job in a short-lived, project-scoped worker container."""

    job_id = UUID(job_id_text)
    job, project = _load_job(job_id)
    if job.status is JobStatus.CANCELLED:
        _finalize_pending_user_deletion(project.owner_id)
        return
    worker_name = f"pptmaster-worker-{str(job.id)[:12]}"
    client = docker.from_env()
    container = None
    worker_error: str | None = None
    try:
        job_workspace = _prepare_job_workspace(project, job)
        is_continuation = _seed_job_workspace(project, job, job_workspace)
        if is_continuation:
            _grant_worker_write_access(job_workspace)
        mounts = [
            docker.types.Mount(
                target="/workspace/project",
                source=_host_job_workspace_path(project, job),
                type="bind",
            )
        ]
        if settings.host_opencode_config_root and settings.host_opencode_config_root.strip():
            mounts.append(
                docker.types.Mount(
                    target="/opt/pptmaster/opencode-config",
                    source=_host_bind_source(settings.host_opencode_config_root),
                    type="bind",
                    read_only=True,
                )
            )
        generated_config = _job_opencode_config(job)
        environment = {
            "PPTMASTER_JOB_ID": str(job.id),
            "PPTMASTER_JOB_PROMPT": job.prompt,
            "PPTMASTER_JOB_MODEL": job.model or "",
            "PPTMASTER_CONTINUE": "1" if is_continuation else "0",
            "PPTMASTER_OPENCODE_IDLE_TIMEOUT_SECONDS": str(settings.opencode_idle_timeout_seconds),
            **_provider_environment(),
        }
        if generated_config:
            environment["PPTMASTER_OPENCODE_CONFIG_JSON"] = generated_config
        container = client.containers.run(
            settings.worker_image,
            command=["python", "-m", "worker.main"],
            name=worker_name,
            detach=True,
            auto_remove=False,
            network=settings.worker_network,
            user=f"{settings.worker_uid}:{settings.worker_gid}",
            environment=environment,
            mounts=mounts,
            mem_limit="6g",
            nano_cpus=4_000_000_000,
            pids_limit=512,
            security_opt=["no-new-privileges:true"],
            read_only=True,
            tmpfs={
                "/home/pptmaster": (
                    "rw,nosuid,size=512m,"
                    f"uid={settings.worker_uid},gid={settings.worker_gid},mode=0755"
                ),
                "/tmp": "rw,noexec,nosuid,size=1g",
            },
            labels={"app": "pptmaster", "job_id": str(job.id)},
        )
        stopped, cancelled = threading.Event(), threading.Event()
        monitor = threading.Thread(target=_watch_cancellation, args=(job.id, container, stopped, cancelled), daemon=True)
        monitor.start()
        for raw in container.logs(stream=True, follow=True):
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                event = {"type": "log", "text": line[:1000]}
            if event.get("type") == "error":
                worker_error = str(event.get("message") or "Worker execution failed")
            _record_event(job.id, event)
        result = container.wait()
        stopped.set()
        monitor.join(timeout=2)
        if cancelled.is_set():
            _finish_job(job.id, succeeded=False, error="任务已取消")
            return
        if result.get("StatusCode") != 0:
            raise RuntimeError(worker_error or f"Worker exited with code {result.get('StatusCode')}")
        _finish_job(job.id, succeeded=True)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Job %s failed", job.id)
        _finish_job(job.id, succeeded=False, error=str(exc))
        raise
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except docker.errors.APIError:
                pass


@celery_app.task(name="runner.export_editor_revision")
def export_editor_revision(job_id_text: str) -> None:
    """Run the native export gates after a browser saves SVG source changes."""

    job_id = UUID(job_id_text)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status is not JobStatus.SUCCEEDED:
            return
        project = db.get(Project, job.project_id)
        if not project:
            return
        job_workspace = _job_workspace_path(project, job)
        if not job_workspace.is_dir():
            logger.warning("Editor export workspace is missing for job %s", job.id)
            return
        _grant_worker_write_access(job_workspace)
        db.add(
            JobEvent(
                job_id=job.id,
                event_type="editor_export",
                payload={
                    "status": "exporting",
                    "text": "正在校验并导出新版演示文稿。",
                },
            )
        )
        db.commit()

    container = None
    try:
        client = docker.from_env()
        container = client.containers.run(
            settings.worker_image,
            command=["python", "-m", "worker.export"],
            name=f"pptmaster-editor-export-{str(job_id)[:12]}",
            detach=True,
            auto_remove=False,
            network=settings.worker_network,
            user=f"{settings.worker_uid}:{settings.worker_gid}",
            mounts=[
                docker.types.Mount(
                    target="/workspace/project",
                    source=_host_job_workspace_path(project, job),
                    type="bind",
                )
            ],
            mem_limit="4g",
            nano_cpus=2_000_000_000,
            pids_limit=512,
            security_opt=["no-new-privileges:true"],
            read_only=True,
            tmpfs={
                "/home/pptmaster": (
                    "rw,nosuid,size=256m,"
                    f"uid={settings.worker_uid},gid={settings.worker_gid},mode=0755"
                ),
                "/tmp": "rw,noexec,nosuid,size=512m",
            },
            labels={"app": "pptmaster", "editor_export_job_id": str(job_id)},
        )
        result = container.wait()
        if result.get("StatusCode") != 0:
            output = container.logs(tail=20).decode("utf-8", errors="replace")
            raise RuntimeError(output[-1800:] or "手动编辑导出失败")
        with SessionLocal() as db:
            refreshed_job = db.get(Job, job_id)
            if not refreshed_job:
                return
            refreshed_project = db.get(Project, refreshed_job.project_id)
            if not refreshed_project:
                return
            _discover_job_artifacts(
                db,
                refreshed_job,
                refreshed_project,
                _project_workspace_path(refreshed_project),
                _job_workspace_path(refreshed_project, refreshed_job),
            )
            newest_pptx = db.execute(
                select(Artifact)
                .where(
                    Artifact.job_id == refreshed_job.id,
                    Artifact.kind == ArtifactKind.PPTX,
                )
                .order_by(Artifact.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if newest_pptx is None:
                raise RuntimeError("未发现导出的新版演示文稿")
            db.add(
                JobEvent(
                    job_id=refreshed_job.id,
                    event_type="artifact",
                    payload={"path": newest_pptx.relative_path},
                )
            )
            db.add(
                JobEvent(
                    job_id=refreshed_job.id,
                    event_type="editor_export",
                    payload={
                        "status": "succeeded",
                        "text": "新版演示文稿已导出，可以下载。",
                    },
                )
            )
            _grant_editor_write_access(_job_workspace_path(refreshed_project, refreshed_job))
            db.commit()
    except Exception:
        logger.exception("Manual editor export failed for job %s", job_id)
        _record_event(
            job_id,
            {
                "type": "editor_export",
                "status": "failed",
                "text": "新版演示文稿导出失败，请检查本页文本或属性后重新保存。",
            },
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except docker.errors.APIError:
                pass
