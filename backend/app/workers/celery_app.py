from celery import Celery
from celery.schedules import crontab
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import quiet_url_loggers

_settings = get_settings()

# Workers post to the Slack webhook; httpx would log its full URL (a secret) at INFO.
quiet_url_loggers()

celery_app = Celery("frogeye", broker=_settings.redis_url, backend=_settings.redis_url)
celery_app.conf.update(
    timezone=_settings.timezone,  # crontabs below are local (Asia/Jerusalem) times
    enable_utc=True,
    task_queues=(Queue("daily"), Queue("agents")),
    task_default_queue="daily",
    task_routes={
        "app.workers.tasks.ingest": {"queue": "daily"},
        "app.workers.tasks.daily_intel": {"queue": "daily"},
        "app.workers.tasks.build_kb": {"queue": "agents"},
        "app.workers.tasks.intel_briefs": {"queue": "agents"},
    },
    beat_schedule={
        # The daily graph ingests first, so it replaces the plain "ingest-daily" entry.
        "daily-intel": {
            "task": "app.workers.tasks.daily_intel",
            "schedule": crontab(hour=6, minute=0),
        },
        "kb-weekly": {
            "task": "app.workers.tasks.build_kb",
            "schedule": crontab(hour=5, minute=0, day_of_week="sun"),
        },
        # The weekly competitor and product briefs, after the KB refresh and before the
        # 06:00 daily run (it does not take the run lock, so that run is never skipped).
        "intel-weekly": {
            "task": "app.workers.tasks.intel_briefs",
            "schedule": crontab(hour=5, minute=30, day_of_week="sun"),
        },
    },
    imports=("app.workers.tasks",),
)
