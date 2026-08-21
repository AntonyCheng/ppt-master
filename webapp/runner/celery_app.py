"""Celery configuration for the PPT Master job runner."""

from celery import Celery

from api.config import get_settings

settings = get_settings()
celery_app = Celery("pptmaster_runner", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_default_queue="pptmaster-jobs",
    task_time_limit=settings.celery_task_time_limit,
    task_track_started=True,
)
celery_app.autodiscover_tasks(["runner"])
