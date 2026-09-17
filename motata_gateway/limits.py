"""Bounded per-instance admission; waiting on one account cannot hold global slots."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from .errors import GatewayError

@dataclass(frozen=True)
class Limits:
    admitted: int = 64
    upstream: int = 20
    per_account: int = 2
    queue_wait: float = 30.0
    request_bytes: int = 1024 * 1024
    response_bytes: int = 8 * 1024 * 1024
    body_seconds: float = 15.0

    def __post_init__(self):
        if not 1 <= self.per_account <= self.upstream <= self.admitted <= 1024:
            raise ValueError('Invalid concurrency limits')
        if not 0 < self.queue_wait <= 300 or not 0 < self.body_seconds <= 300:
            raise ValueError('Invalid time limits')
        if not 1024 <= self.request_bytes <= 8 * 1024 * 1024 or not 1024 <= self.response_bytes <= 32 * 1024 * 1024:
            raise ValueError('Invalid byte limits')


class Scheduler:
    def __init__(self, limits: Limits):
        self.limits = limits
        self.admitted = 0
        self.global_slots = asyncio.Semaphore(limits.upstream)
        self.accounts: dict[tuple, list] = {}
        self.active = 0
        self.peak = 0

    @asynccontextmanager
    async def admission(self):
        # All calls are on the same event loop: check/increment contains no await.
        if self.admitted >= self.limits.admitted:
            raise GatewayError('GATEWAY_BUSY', 503, 'Gateway capacity reached; retry later.')
        self.admitted += 1
        try:
            yield
        finally:
            self.admitted -= 1

    @asynccontextmanager
    async def execution(self, key):
        row = self.accounts.setdefault(key, [asyncio.Semaphore(self.limits.per_account), 0])
        row[1] += 1
        account_acquired = global_acquired = False
        try:
            try:
                async with asyncio.timeout(self.limits.queue_wait):
                    await row[0].acquire()
                    account_acquired = True
                    await self.global_slots.acquire()
                    global_acquired = True
            except TimeoutError:
                raise GatewayError('QUEUE_TIMEOUT', 503, 'Gateway queue wait expired.') from None
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                yield
            finally:
                self.active -= 1
        finally:
            if global_acquired:
                self.global_slots.release()
            if account_acquired:
                row[0].release()
            row[1] -= 1
            if row[1] == 0:
                self.accounts.pop(key, None)
