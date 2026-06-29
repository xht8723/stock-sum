"""Import smoke tests for the architecture scaffold."""

import importlib


MODULES = [
    "stock_sum",
    "stock_sum.cli",
    "stock_sum.config.loader",
    "stock_sum.core.pipeline",
    "stock_sum.collectors.base",
    "stock_sum.llm.catalog",
    "stock_sum.reports.renderer",
    "stock_sum.reports.presentation",
    "stock_sum.delivery.email",
    "stock_sum.scheduler.service",
    "stock_sum.storage.database",
    "stock_sum.api.app",
    "stock_sum.api.jobs",
]


def test_modules_import() -> None:
    for module in MODULES:
        importlib.import_module(module)
