# Background Tasks and Celery Integration

## Overview

This document covers patterns for handling asynchronous background tasks using Celery, including task definition, scheduling, monitoring, and best practices for long-running operations.

## Celery Setup

### Celery Application Configuration

```python
# app/tasks/celery_app.py
from celery import Celery
from app.config.settings import settings

def create_celery_app() -> Celery:
    """
    Create and configure Celery application.
    """
    celery_app = Celery(
        "api_tasks",
        broker=settings.CELERY_BROKER_URL,
        backend=settings.CELERY_RESULT_BACKEND,
    )

    # Configure Celery
    celery_app.conf.update(
        # Task settings
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,

        # Task execution
        task_track_started=True,
        task_time_limit=3600,  # 1 hour hard limit
        task_soft_time_limit=3300,  # 55 minutes soft limit
        task_acks_late=True,  # Acknowledge after task completion
        task_reject_on_worker_lost=True,

        # Result backend
        result_expires=86400,  # 24 hours
        result_persistent=True,

        # Worker settings
        worker_prefetch_multiplier=1,  # One task at a time
        worker_max_tasks_per_child=1000,  # Restart worker after N tasks

        # Broker settings
        broker_connection_retry_on_startup=True,

        # Task routes
        task_routes={
            "app.tasks.execution_tasks.*": {"queue": "execution"},
            "app.tasks.report_tasks.*": {"queue": "reports"},
            "app.tasks.notification_tasks.*": {"queue": "notifications"},
        },
    )

    # Auto-discover tasks
    celery_app.autodiscover_tasks([
        "app.tasks.user_tasks",
        "app.tasks.execution_tasks",
        "app.tasks.report_tasks",
        "app.tasks.notification_tasks",
    ])

    return celery_app

celery_app = create_celery_app()
```

### Configuration Options

```python
# config/settings.py
# Broker options
CELERY_BROKER_URL = "redis://localhost:6379/0"  # Redis
# CELERY_BROKER_URL = "amqp://guest:guest@localhost:5672//"  # RabbitMQ
# CELERY_BROKER_URL = "sqla+postgresql://user:pass@localhost/db"  # SQLAlchemy

# Result backend
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"
# CELERY_RESULT_BACKEND = "db+postgresql://user:pass@localhost/db"
```

## Task Definition

### Basic Task

```python
# app/tasks/user_tasks.py
from celery import Task
from app.tasks.celery_app import celery_app
from app.services.user_service import UserService

@celery_app.task(name="users.send_welcome_email", bind=True)
def send_welcome_email(self: Task, user_id: int):
    """
    Send welcome email to new user.

    Args:
        user_id: User ID to send email to
    """
    try:
        user = UserService.get_one(user_id)

        # Send email
        from app.util.email import send_email

        send_email(
            to=user.email,
            subject="Welcome!",
            template="welcome.html",
            context={"user": user}
        )

        return {"status": "success", "user_id": user_id}

    except Exception as e:
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=60 * (2 ** self.request.retries))
```

### Task with Retry Logic

```python
@celery_app.task(
    name="users.sync_user_data",
    bind=True,
    max_retries=3,
    default_retry_delay=60,  # 1 minute
)
def sync_user_data(self: Task, user_id: int):
    """
    Sync user data with external system.

    Retries up to 3 times with exponential backoff.
    """
    try:
        user = UserService.get_one(user_id)

        # Sync with external API
        response = external_api.sync_user(user)

        if not response.success:
            raise Exception(f"Sync failed: {response.error}")

        return {"status": "success", "user_id": user_id}

    except Exception as e:
        # Exponential backoff: 60s, 120s, 240s
        countdown = 60 * (2 ** self.request.retries)

        raise self.retry(exc=e, countdown=countdown, max_retries=3)
```

### Task with Progress Tracking

```python
@celery_app.task(name="reports.generate_report", bind=True)
def generate_report(self: Task, report_id: int):
    """
    Generate report with progress tracking.
    """
    from app.services.report_service import ReportService

    report = ReportService.get_one(report_id)

    # Update state: PENDING -> STARTED
    self.update_state(
        state="STARTED",
        meta={"current": 0, "total": 100, "status": "Initializing..."}
    )

    try:
        # Step 1: Fetch data (0-30%)
        self.update_state(
            state="PROGRESS",
            meta={"current": 10, "total": 100, "status": "Fetching data..."}
        )

        data = ReportService.fetch_data(report)

        self.update_state(
            state="PROGRESS",
            meta={"current": 30, "total": 100, "status": "Data fetched"}
        )

        # Step 2: Process data (30-70%)
        self.update_state(
            state="PROGRESS",
            meta={"current": 40, "total": 100, "status": "Processing data..."}
        )

        processed = ReportService.process_data(data)

        self.update_state(
            state="PROGRESS",
            meta={"current": 70, "total": 100, "status": "Data processed"}
        )

        # Step 3: Generate output (70-100%)
        self.update_state(
            state="PROGRESS",
            meta={"current": 80, "total": 100, "status": "Generating output..."}
        )

        output = ReportService.generate_output(processed)

        self.update_state(
            state="PROGRESS",
            meta={"current": 100, "total": 100, "status": "Complete"}
        )

        return {"status": "success", "output": output}

    except Exception as e:
        self.update_state(
            state="FAILURE",
            meta={"error": str(e)}
        )
        raise
```

### Task Chaining

```python
from celery import chain, group, chord

# Sequential execution (chain)
@celery_app.task
def task_a(x):
    return x + 1

@celery_app.task
def task_b(x):
    return x * 2

@celery_app.task
def task_c(x):
    return x - 1

# Execute: task_a -> task_b -> task_c
result = chain(task_a.s(10), task_b.s(), task_c.s())()
# Result: ((10 + 1) * 2) - 1 = 21

# Parallel execution (group)
@celery_app.task
def process_item(item):
    return item * 2

# Process multiple items in parallel
job = group(process_item.s(i) for i in range(10))
result = job.apply_async()

# Parallel + callback (chord)
@celery_app.task
def aggregate_results(results):
    return sum(results)

# Process items in parallel, then aggregate
job = chord(
    (process_item.s(i) for i in range(10)),
    aggregate_results.s()
)
result = job.apply_async()
```

## Task Execution

### Calling Tasks

```python
# Synchronous (blocking, for testing)
result = send_welcome_email(user_id=123)

# Asynchronous (non-blocking, production)
task = send_welcome_email.delay(user_id=123)

# Asynchronous with options
task = send_welcome_email.apply_async(
    args=[123],
    countdown=60,  # Delay execution by 60 seconds
    expires=3600,  # Expire after 1 hour
    retry=True,
    retry_policy={
        "max_retries": 3,
        "interval_start": 0,
        "interval_step": 60,
    }
)

# Get result (blocking)
result = task.get(timeout=10)

# Check status
if task.ready():
    result = task.result
else:
    print("Task still running")
```

### Task Status Tracking

```python
# app/web/tasks.py
from fastapi import APIRouter, HTTPException
from celery.result import AsyncResult
from app.tasks.celery_app import celery_app

router = APIRouter(prefix="/tasks", tags=["tasks"])

@router.get("/{task_id}")
async def get_task_status(task_id: str):
    """Get task status and result"""
    task_result = AsyncResult(task_id, app=celery_app)

    response = {
        "task_id": task_id,
        "status": task_result.state,
        "ready": task_result.ready(),
    }

    if task_result.ready():
        if task_result.successful():
            response["result"] = task_result.result
        else:
            response["error"] = str(task_result.result)
    elif task_result.state == "PROGRESS":
        response["progress"] = task_result.info

    return response

@router.delete("/{task_id}")
async def cancel_task(task_id: str):
    """Cancel running task"""
    task_result = AsyncResult(task_id, app=celery_app)

    if task_result.ready():
        raise HTTPException(400, "Task already completed")

    task_result.revoke(terminate=True)

    return {"status": "cancelled", "task_id": task_id}
```

## Periodic Tasks (Celery Beat)

### Scheduler Configuration

```python
# app/tasks/celery_app.py
from celery.schedules import crontab

celery_app.conf.beat_schedule = {
    # Run every hour
    "cleanup-old-data": {
        "task": "cleanup.cleanup_old_data",
        "schedule": 3600.0,  # Seconds
    },

    # Run daily at midnight
    "generate-daily-report": {
        "task": "reports.generate_daily_report",
        "schedule": crontab(hour=0, minute=0),
    },

    # Run every Monday at 9 AM
    "weekly-summary": {
        "task": "reports.generate_weekly_summary",
        "schedule": crontab(day_of_week=1, hour=9, minute=0),
    },

    # Run every 15 minutes
    "sync-users": {
        "task": "users.sync_user_data",
        "schedule": crontab(minute="*/15"),
    },
}
```

### Periodic Task Definition

```python
# app/tasks/cleanup_tasks.py
@celery_app.task(name="cleanup.cleanup_old_data")
def cleanup_old_data():
    """
    Cleanup old data from database.
    Runs daily via Celery Beat.
    """
    from datetime import datetime, timedelta

    # Delete records older than 90 days
    cutoff_date = datetime.utcnow() - timedelta(days=90)

    # Cleanup logs
    from app.services.log_service import LogService
    deleted_logs = LogService.delete_older_than(cutoff_date)

    # Cleanup old versions
    from app.services.version_service import VersionService
    deleted_versions = VersionService.delete_older_than(cutoff_date)

    return {
        "status": "success",
        "deleted_logs": deleted_logs,
        "deleted_versions": deleted_versions,
    }
```

## Best Practices

### 1. Task Design

```python
# GOOD: Idempotent task
@celery_app.task
def update_user_stats(user_id: int):
    """Task can be run multiple times safely"""
    user = UserService.get_one(user_id)

    # Calculate stats from scratch each time
    stats = UserService.calculate_stats(user)

    # Update in database
    user.stats = stats
    UserService.add_or_update(user)

# BAD: Non-idempotent task
@celery_app.task
def increment_counter(user_id: int):
    """Running multiple times gives wrong result"""
    user = UserService.get_one(user_id)
    user.counter += 1  # Problem: duplicate runs increment twice
    UserService.add_or_update(user)
```

### 2. Error Handling

```python
@celery_app.task(bind=True, max_retries=3)
def reliable_task(self: Task, data):
    """Task with comprehensive error handling"""
    try:
        # Main task logic
        result = process_data(data)
        return result

    except TemporaryError as e:
        # Retry for temporary errors
        raise self.retry(exc=e, countdown=60)

    except PermanentError as e:
        # Log and fail for permanent errors
        logger.error(f"Permanent error in task: {e}")
        raise

    except Exception as e:
        # Retry unknown errors with exponential backoff
        countdown = 60 * (2 ** self.request.retries)
        raise self.retry(exc=e, countdown=countdown)
```

### 3. Task Monitoring

```python
# app/tasks/base_task.py
from celery import Task
import logging

logger = logging.getLogger(__name__)

class MonitoredTask(Task):
    """Base task class with monitoring"""

    def on_success(self, retval, task_id, args, kwargs):
        """Called when task succeeds"""
        logger.info(f"Task {task_id} succeeded: {retval}")

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Called when task fails"""
        logger.error(
            f"Task {task_id} failed: {exc}",
            extra={"args": args, "kwargs": kwargs}
        )

        # Send alert
        from app.util.alerts import send_alert
        send_alert(f"Task {task_id} failed: {exc}")

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Called when task is retried"""
        logger.warning(f"Task {task_id} retrying: {exc}")

# Usage
@celery_app.task(base=MonitoredTask, bind=True)
def monitored_task(self):
    pass
```

### 4. Database Connections

```python
@celery_app.task
def task_with_db_connection():
    """Properly manage database connections in tasks"""
    from app.db.async_session import SQLAlchemyAsync

    # Use context manager for session
    with SQLAlchemyAsync.scoped_context():
        try:
            # Perform database operations
            result = UserService.get_all()

            return {"status": "success", "count": len(result)}

        finally:
            # Session is automatically closed
            pass
```

### 5. Long-Running Tasks

```python
@celery_app.task(
    bind=True,
    time_limit=7200,  # 2 hour hard limit
    soft_time_limit=7000,  # 1h 56m soft limit
)
def long_running_task(self: Task):
    """Handle long-running operations"""
    from celery.exceptions import SoftTimeLimitExceeded

    try:
        # Process in chunks with progress updates
        total_items = 10000
        chunk_size = 100

        for i in range(0, total_items, chunk_size):
            # Process chunk
            process_chunk(i, i + chunk_size)

            # Update progress
            self.update_state(
                state="PROGRESS",
                meta={
                    "current": i + chunk_size,
                    "total": total_items,
                }
            )

    except SoftTimeLimitExceeded:
        # Cleanup before hard limit
        logger.warning("Task approaching time limit, cleaning up...")
        cleanup()
        raise
```

## Running Workers

### Development

```bash
# Start worker
celery -A app.tasks.celery_app worker --loglevel=info

# Start worker with specific queue
celery -A app.tasks.celery_app worker -Q execution --loglevel=info

# Start beat scheduler
celery -A app.tasks.celery_app beat --loglevel=info

# Start worker + beat together
celery -A app.tasks.celery_app worker --beat --loglevel=info
```

### Production

```bash
# Multiple workers with different queues
celery multi start w1 w2 w3 \
  -A app.tasks.celery_app \
  -Q:1 execution \
  -Q:2 reports \
  -Q:3 notifications \
  --pidfile=/var/run/celery/%n.pid \
  --logfile=/var/log/celery/%n%I.log

# Stop workers
celery multi stop w1 w2 w3 \
  --pidfile=/var/run/celery/%n.pid

# Restart workers
celery multi restart w1 w2 w3 \
  -A app.tasks.celery_app \
  --pidfile=/var/run/celery/%n.pid
```

### Systemd Service

```ini
# /etc/systemd/system/celery-worker.service
[Unit]
Description=Celery Worker
After=network.target

[Service]
Type=forking
User=celery
Group=celery
WorkingDirectory=/app
ExecStart=/usr/local/bin/celery multi start worker \
    -A app.tasks.celery_app \
    --pidfile=/var/run/celery/%n.pid \
    --logfile=/var/log/celery/%n%I.log \
    --loglevel=INFO \
    --concurrency=4
ExecStop=/usr/local/bin/celery multi stop worker \
    --pidfile=/var/run/celery/%n.pid
ExecReload=/usr/local/bin/celery multi restart worker \
    -A app.tasks.celery_app \
    --pidfile=/var/run/celery/%n.pid \
    --logfile=/var/log/celery/%n%I.log \
    --loglevel=INFO

[Install]
WantedBy=multi-user.target
```

## Monitoring and Debugging

### Flower (Web UI)

```bash
# Install Flower
pip install flower

# Start Flower
celery -A app.tasks.celery_app flower --port=5555

# Access at http://localhost:5555
```

### Task Inspection

```python
# Get active tasks
from app.tasks.celery_app import celery_app

i = celery_app.control.inspect()

# Active tasks
active = i.active()

# Scheduled tasks
scheduled = i.scheduled()

# Reserved tasks
reserved = i.reserved()

# Worker stats
stats = i.stats()
```

## Conclusion

Celery provides robust background task processing for:
- Long-running operations
- Scheduled jobs
- Asynchronous workflows
- Distributed computing

The patterns shown here enable scalable, reliable background processing in FastAPI applications.
