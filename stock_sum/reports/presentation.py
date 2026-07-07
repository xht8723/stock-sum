"""Compatibility exports for presentation rendering."""

from __future__ import annotations

from stock_sum.reports.formatting import PresentationMode, PresentationRenderError, SocialReportDetail
from stock_sum.reports.renderer import PresentationRenderer

__all__ = [
    "PresentationMode",
    "PresentationRenderError",
    "PresentationRenderer",
    "SocialReportDetail",
]
