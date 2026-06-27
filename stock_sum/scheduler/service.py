"""APScheduler service wrapper scaffold."""

from __future__ import annotations

from stock_sum.config.models import AppConfig
from stock_sum.scheduler.jobs import ReportJob


class SchedulerService:
    """Registers report jobs without executing business logic yet."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.jobs: list[ReportJob] = []

    def configure_jobs(self) -> list[ReportJob]:
        """Create in-memory job definitions from configured report profiles."""

        self.jobs = [
            ReportJob(profile=name, cron=profile.schedule, timezone=profile.timezone)
            for name, profile in self.config.reports.items()
        ]
        return self.jobs

    def start(self) -> None:
        """Start the scheduler."""

        raise NotImplementedError("Scheduler execution is scaffolded only.")
