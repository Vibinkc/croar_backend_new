from celery import Celery
from celery.schedules import schedule


def register_celery_schedules(celery_app: Celery) -> None:
    celery_app.conf.beat_schedule = {
        "monitor_targets": {
            "task": "ping",
            "schedule": schedule(5),  # every second
        }
    }
