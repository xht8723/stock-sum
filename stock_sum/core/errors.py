"""Shared domain exceptions."""


class StockSumError(Exception):
    """Base error for stock-sum failures."""


class ConfigurationError(StockSumError):
    """Raised when configuration is invalid or incomplete."""


class PipelineNotImplementedError(StockSumError):
    """Raised by scaffolded pipeline operations."""
