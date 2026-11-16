from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)
