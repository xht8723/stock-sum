"""Scheduled job definitions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReportJob:
    """A scheduled report profile."""

    profile: str
    cron: str
    timezone: str
