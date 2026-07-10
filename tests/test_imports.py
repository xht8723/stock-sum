"""Import smoke tests for the stock-sum package."""

import importlib


MODULES = [
    "stock_sum",
    "stock_sum.cli",
    "stock_sum.config.loader",
    "stock_sum.config.secrets",
    "stock_sum.core.pipeline",
    "stock_sum.collectors.base",
    "stock_sum.llm.catalog",
    "stock_sum.reports.formatting",
    "stock_sum.reports.renderer",
    "stock_sum.api.app",
    "stock_sum.api.jobs",
]


def test_modules_import() -> None:
    for module in MODULES:
        importlib.import_module(module)
