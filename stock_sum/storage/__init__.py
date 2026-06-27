"""SQLite storage interfaces."""

from stock_sum.storage.repository import StorageRepository
from stock_sum.storage.sqlite import SQLiteStorageRepository

__all__ = ["SQLiteStorageRepository", "StorageRepository"]
