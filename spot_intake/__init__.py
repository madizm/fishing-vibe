"""spot_intake — 钓点收录: deep intake module for Douyin fishing-spot collection.

Slice 1: pure domain logic (vocabulary + extraction). Browser/LLM/geocode/SQLite
adapters and the Intake orchestrator land in slice 2.
"""

from spot_intake import extract, vocabulary

__all__ = ["extract", "vocabulary"]
