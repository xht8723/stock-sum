"""Collector interfaces and registry."""

from stock_sum.collectors.base import Collector
from stock_sum.collectors.factory import build_collector

__all__ = ["Collector", "build_collector"]
