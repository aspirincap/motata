"""Bounded retries for reviewed read-only TikTok endpoints."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
import math
import random
import time


def retry_after_seconds(value, *, now=None):
    """Accept delay-seconds and HTTP dates; never turn invalid input into a sleep."""
    if value is None or isinstance(value, bool):
        return None
    try:
        delay = float(value)
    except (TypeError, ValueError, OverflowError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            delay = max(0.0, date.timestamp() - (time.time() if now is None else now))
        except (TypeError, ValueError, OverflowError, AttributeError):
            return None
    return delay if math.isfinite(delay) and delay >= 0 else None


@dataclass(frozen=True)
class ReadRetryPolicy:
    # Includes the initial attempt. Budget limits sleeps, not HTTP execution time.
    max_attempts: int = 5
    base_delay: float = 10.0
    max_delay: float = 60.0
    max_wait: float = 180.0

    def __post_init__(self):
        if isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int) or not 1 <= self.max_attempts <= 10:
            raise ValueError('Read attempts must be an integer from 1 to 10.')
        for name in ('base_delay', 'max_delay', 'max_wait'):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{name} must be a finite, nonnegative number.')

    def delay(self, failed_attempt, retry_after=None, *, now=None, uniform=None):
        ceiling = min(self.max_delay, self.base_delay * 2 ** (failed_attempt - 1))
        # Equal jitter keeps a nonzero floor. A server delay is a minimum and
        # must not be truncated to max_delay or replaced by a smaller jitter.
        floor = max(ceiling / 2, retry_after_seconds(retry_after, now=now) or 0.0)
        return floor + (uniform or random.uniform)(0.0, ceiling / 2)
