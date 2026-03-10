"""
Pipeline orchestrator for PowerplAI data ingestion.

Uses APScheduler for scheduling and provides:
- Dependency-aware execution order
- Retry with exponential backoff
- Progress tracking and logging
- Graceful shutdown handling
"""
import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from backend.src.db.database import async_session_maker
from backend.src.pipeline.config import (
    PIPELINE_CONFIGS,
    PipelineConfig,
    UpdateFrequency,
)
from backend.src.pipeline.validation import DataValidator, ValidationResult, run_all_validations

logger = structlog.get_logger()


@dataclass
class PipelineRun:
    """Records a single pipeline execution."""
    pipeline_name: str
    status: str  # "running", "success", "failed", "skipped"
    started_at: datetime
    finished_at: datetime | None = None
    records_processed: int = 0
    errors: list[str] = field(default_factory=list)
    validation_result: ValidationResult | None = None
    duration_ms: float = 0

    def to_dict(self) -> dict:
        return {
            "pipeline_name": self.pipeline_name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "records_processed": self.records_processed,
            "errors": self.errors[:10],  # Limit to first 10 errors
            "duration_ms": self.duration_ms,
        }


class PipelineOrchestrator:
    """Orchestrates data pipeline execution."""

    def __init__(self):
        self.pipelines: dict[str, Callable[..., Coroutine[Any, Any, int]]] = {}
        self.configs = PIPELINE_CONFIGS.copy()
        self.run_history: list[PipelineRun] = []
        self.is_running: dict[str, bool] = defaultdict(bool)
        self._lock = asyncio.Lock()

    def register(
        self,
        name: str,
        func: Callable[..., Coroutine[Any, Any, int]],
        config: PipelineConfig | None = None,
    ):
        """Register a pipeline function."""
        self.pipelines[name] = func
        if config:
            self.configs[name] = config
        logger.info("pipeline_registered", name=name)

    def _get_execution_order(self, pipeline_names: list[str] | None = None) -> list[str]:
        """
        Determine execution order based on dependencies and priorities.
        Uses topological sort with priority as tiebreaker.
        """
        if pipeline_names is None:
            pipeline_names = list(self.pipelines.keys())

        # Build dependency graph
        graph: dict[str, set[str]] = {name: set() for name in pipeline_names}
        for name in pipeline_names:
            config = self.configs.get(name)
            if config and config.depends_on:
                for dep in config.depends_on:
                    if dep in pipeline_names:
                        graph[name].add(dep)

        # Topological sort with priority
        result = []
        remaining = set(pipeline_names)

        while remaining:
            # Find pipelines with no remaining dependencies
            ready = [
                name for name in remaining
                if not (graph[name] & remaining)
            ]
            if not ready:
                # Circular dependency - just take one
                ready = [min(remaining)]
                logger.warning("circular_dependency_detected", pipelines=remaining)

            # Sort by priority
            ready.sort(key=lambda n: self.configs.get(n, PipelineConfig(n, None, None)).priority)

            result.append(ready[0])
            remaining.remove(ready[0])

        return result

    async def run_pipeline(
        self,
        name: str,
        db: AsyncSession | None = None,
        **kwargs,
    ) -> PipelineRun:
        """Run a single pipeline with retry logic."""
        if name not in self.pipelines:
            raise ValueError(f"Pipeline '{name}' not registered")

        config = self.configs.get(name, PipelineConfig(name, None, None))

        # Check if already running
        async with self._lock:
            if self.is_running[name]:
                logger.warning("pipeline_already_running", name=name)
                return PipelineRun(
                    pipeline_name=name,
                    status="skipped",
                    started_at=datetime.utcnow(),
                    errors=["Pipeline already running"],
                )
            self.is_running[name] = True

        run = PipelineRun(
            pipeline_name=name,
            status="running",
            started_at=datetime.utcnow(),
        )

        close_session = False
        if db is None:
            db = async_session_maker()
            close_session = True

        try:
            # Retry logic with exponential backoff
            retry_config = config.retry
            last_error = None

            for attempt in range(retry_config.max_attempts):
                try:
                    records = await self.pipelines[name](db, **kwargs)
                    run.records_processed = records
                    run.status = "success"

                    logger.info(
                        "pipeline_completed",
                        name=name,
                        records=records,
                        attempt=attempt + 1,
                    )
                    break

                except Exception as e:
                    last_error = str(e)
                    run.errors.append(f"Attempt {attempt + 1}: {last_error}")

                    if attempt < retry_config.max_attempts - 1:
                        delay = min(
                            retry_config.initial_delay * (retry_config.exponential_base ** attempt),
                            retry_config.max_delay,
                        )
                        logger.warning(
                            "pipeline_retry",
                            name=name,
                            attempt=attempt + 1,
                            delay=delay,
                            error=last_error,
                        )
                        await asyncio.sleep(delay)
                    else:
                        run.status = "failed"
                        logger.error(
                            "pipeline_failed",
                            name=name,
                            attempts=retry_config.max_attempts,
                            error=last_error,
                        )

        finally:
            run.finished_at = datetime.utcnow()
            run.duration_ms = (run.finished_at - run.started_at).total_seconds() * 1000

            async with self._lock:
                self.is_running[name] = False
                self.run_history.append(run)
                # Keep only last 100 runs
                if len(self.run_history) > 100:
                    self.run_history = self.run_history[-100:]

            if close_session:
                await db.close()

        return run

    async def run_all(
        self,
        frequency: UpdateFrequency | None = None,
        validate: bool = True,
    ) -> dict:
        """
        Run all registered pipelines in dependency order.

        Args:
            frequency: Only run pipelines matching this frequency
            validate: Run validation after all pipelines complete
        """
        start_time = datetime.utcnow()

        # Filter pipelines by frequency if specified
        pipeline_names = list(self.pipelines.keys())
        if frequency:
            pipeline_names = [
                name for name in pipeline_names
                if self.configs.get(name) and self.configs[name].frequency == frequency
            ]

        # Get execution order
        execution_order = self._get_execution_order(pipeline_names)

        logger.info(
            "pipeline_batch_starting",
            pipelines=execution_order,
            frequency=frequency.value if frequency else "all",
        )

        # Execute pipelines
        runs = []
        async with async_session_maker() as db:
            for name in execution_order:
                config = self.configs.get(name)
                if config and not config.enabled:
                    logger.info("pipeline_disabled", name=name)
                    continue

                run = await self.run_pipeline(name, db)
                runs.append(run)

                # Rate limiting between pipelines
                if config and config.rate_limit:
                    await asyncio.sleep(1.0 / config.rate_limit.requests_per_second)

            # Run validation if requested
            validation_result = None
            if validate:
                validation_result = await run_all_validations(db)

        # Summary
        success_count = sum(1 for r in runs if r.status == "success")
        failed_count = sum(1 for r in runs if r.status == "failed")
        total_records = sum(r.records_processed for r in runs)
        total_duration = (datetime.utcnow() - start_time).total_seconds() * 1000

        logger.info(
            "pipeline_batch_completed",
            success=success_count,
            failed=failed_count,
            total_records=total_records,
            duration_ms=total_duration,
        )

        return {
            "success_count": success_count,
            "failed_count": failed_count,
            "total_records": total_records,
            "duration_ms": total_duration,
            "runs": [r.to_dict() for r in runs],
            "validation": validation_result,
        }

    def get_status(self) -> dict:
        """Get current orchestrator status."""
        return {
            "registered_pipelines": list(self.pipelines.keys()),
            "running": {k: v for k, v in self.is_running.items() if v},
            "recent_runs": [r.to_dict() for r in self.run_history[-10:]],
            "configs": {
                name: {
                    "frequency": cfg.frequency.value,
                    "enabled": cfg.enabled,
                    "priority": cfg.priority,
                    "depends_on": cfg.depends_on,
                }
                for name, cfg in self.configs.items()
            },
        }


# Singleton orchestrator instance
orchestrator = PipelineOrchestrator()


# -------------------------------------------------------------------------
# Pipeline registration
# -------------------------------------------------------------------------

async def _schedule_sync_pipeline(db: AsyncSession, **kwargs) -> int:
    """Sync game schedule from NHL API."""
    from backend.src.ingestion.games import ingest_schedule_for_date
    from datetime import date, timedelta

    config = kwargs.get("config", PIPELINE_CONFIGS.get("schedule_sync"))
    days_ahead = config.extra.get("days_ahead", 7) if config else 7
    days_behind = config.extra.get("days_behind", 1) if config else 1

    total = 0
    today = date.today()

    # Sync past games (for score updates)
    for i in range(days_behind):
        d = today - timedelta(days=i)
        total += await ingest_schedule_for_date(db, d)

    # Sync upcoming games
    for i in range(days_ahead):
        d = today + timedelta(days=i)
        total += await ingest_schedule_for_date(db, d)

    return total


async def _game_logs_pipeline(db: AsyncSession, **kwargs) -> int:
    """Ingest player game logs."""
    from backend.src.ingestion.games import ingest_all_player_game_logs
    from backend.src.ingestion.scheduler import get_current_season

    config = kwargs.get("config", PIPELINE_CONFIGS.get("game_logs"))
    season = get_current_season()

    result = await ingest_all_player_game_logs(db, season)
    return result.get("logs_ingested", 0)


async def _injuries_pipeline(db: AsyncSession, **kwargs) -> int:
    """Refresh injury data from ESPN."""
    from backend.src.ingestion.espn_injuries import refresh_espn_injuries

    return await refresh_espn_injuries(db)


async def _team_goalie_stats_pipeline(db: AsyncSession, **kwargs) -> int:
    """Refresh team and goalie stats."""
    from backend.src.ingestion.team_goalie_stats import refresh_team_goalie_stats

    return await refresh_team_goalie_stats(db)


async def _roster_sync_pipeline(db: AsyncSession, **kwargs) -> int:
    """Sync player rosters."""
    from backend.src.ingestion.roster_sync import sync_all_rosters

    result = await sync_all_rosters(db)
    return result.get("players_updated", 0)


async def _salary_cap_pipeline(db: AsyncSession, **kwargs) -> int:
    """Scrape salary cap data."""
    from backend.src.ingestion.salary_cap import refresh_salary_data

    return await refresh_salary_data(db)


# Register all pipelines
def register_all_pipelines():
    """Register all standard pipelines with the orchestrator."""
    orchestrator.register("schedule_sync", _schedule_sync_pipeline)
    orchestrator.register("game_logs", _game_logs_pipeline)
    orchestrator.register("injuries", _injuries_pipeline)
    orchestrator.register("team_goalie_stats", _team_goalie_stats_pipeline)
    orchestrator.register("roster_sync", _roster_sync_pipeline)
    orchestrator.register("salary_cap", _salary_cap_pipeline)


# -------------------------------------------------------------------------
# Scheduler setup (using APScheduler)
# -------------------------------------------------------------------------

_scheduler = None


async def start_scheduler():
    """Start the background scheduler for automated pipeline runs."""
    global _scheduler

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger
    except ImportError:
        logger.warning("apscheduler_not_installed", message="Install apscheduler for automated scheduling")
        return

    register_all_pipelines()

    _scheduler = AsyncIOScheduler()

    # Schedule based on frequencies
    for name, config in PIPELINE_CONFIGS.items():
        if not config.enabled:
            continue

        if config.frequency == UpdateFrequency.HOURLY:
            _scheduler.add_job(
                orchestrator.run_pipeline,
                IntervalTrigger(hours=1),
                args=[name],
                id=f"pipeline_{name}",
                replace_existing=True,
            )
        elif config.frequency == UpdateFrequency.DAILY:
            # Run daily at 6 AM UTC
            _scheduler.add_job(
                orchestrator.run_pipeline,
                CronTrigger(hour=6, minute=0),
                args=[name],
                id=f"pipeline_{name}",
                replace_existing=True,
            )
        elif config.frequency == UpdateFrequency.WEEKLY:
            # Run weekly on Monday at 6 AM UTC
            _scheduler.add_job(
                orchestrator.run_pipeline,
                CronTrigger(day_of_week="mon", hour=6, minute=0),
                args=[name],
                id=f"pipeline_{name}",
                replace_existing=True,
            )

    _scheduler.start()
    logger.info("scheduler_started", jobs=len(_scheduler.get_jobs()))


async def stop_scheduler():
    """Stop the background scheduler."""
    global _scheduler
    if _scheduler:
        _scheduler.shutdown()
        _scheduler = None
        logger.info("scheduler_stopped")


def get_scheduler_status() -> dict:
    """Get scheduler status."""
    if not _scheduler:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
        })

    return {
        "running": _scheduler.running,
        "jobs": jobs,
    }
