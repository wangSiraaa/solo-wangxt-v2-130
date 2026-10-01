from celery import Celery

from ..config import get_settings

settings = get_settings()
celery_app = Celery(
    "leveling",
    broker=settings.broker_url,
    backend=settings.result_backend,
    include=["app.workers.tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_default_queue="leveling",
    beat_schedule={
        "recover-and-advance-jobs": {
            "task": "app.workers.tasks.advance_jobs",
            "schedule": 5.0,
        },
    },
)
