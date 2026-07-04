"""Shared domain exceptions."""


class StockSumError(Exception):
    """Base error for stock-sum failures."""


class ConfigurationError(StockSumError):
    """Raised when configuration is invalid or incomplete."""


class UnsupportedSourceTypeError(StockSumError):
    """Raised when storage has no source-specific schema for a raw item."""
