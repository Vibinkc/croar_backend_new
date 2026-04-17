from celery import Celery

from app.core.settings import settings

celery_app = Celery(
    "croar_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_broker_url,
    include=["app.tasks.email_tasks", "app.tasks.assessment_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    worker_pool="solo",  # Required for Windows to avoid PermissionErrors
    task_track_started=True,
    # Windows specific fixes if needed
    worker_pool_restarts=True,
)
