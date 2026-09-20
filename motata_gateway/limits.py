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


class RateBudget:
    """Per-gateway and per-account token buckets, separately from concurrency.

    Budgets are administrator values, not claims about platform quotas. Idle
    buckets are pruned; pending waiters have a deadline and bounded state.
    """
    def __init__(self, global_rate=None, account_rate=None, *, burst=20, account_burst=5,
                 wait_timeout=30, capacity=4096, clock=None, sleep=asyncio.sleep):
        import time
        import math
        for rate in (global_rate, account_rate):
            if rate is not None and (type(rate) not in (float, int) or not math.isfinite(rate) or not 0 < rate <= 10000):
                raise ValueError('Invalid request-rate budget')
        if (type(burst) is not int or type(account_burst) is not int or
            not 1 <= burst <= 10000 or not 1 <= account_burst <= burst or
            type(wait_timeout) not in (int, float) or not math.isfinite(wait_timeout) or
            not 0 < wait_timeout <= 300 or type(capacity) is not int or not 2 <= capacity <= 65536):
            raise ValueError('Invalid rate-bucket policy')
        self.global_rate, self.account_rate = global_rate, account_rate
        self.burst, self.account_burst, self.wait_timeout = burst, account_burst, wait_timeout
        self.capacity = capacity
        self.clock, self.sleep = clock or time.monotonic, sleep
        self.buckets = {}
        self.users = {}

    async def wait(self, account):
        rules = []
        if self.global_rate:
            rules.append((('global',), self.global_rate, self.burst))
        if self.account_rate:
            rules.append((('account', *account), self.account_rate, self.account_burst))
        if not rules:
            return
        now = self.clock()
        self.buckets = {k: v for k, v in self.buckets.items() if self.users.get(k) or now - v[1] < 300}
        added = []
        try:
            for key, rate, burst in rules:
                if key not in self.buckets:
                    if len(self.buckets) >= self.capacity:
                        raise GatewayError('GATEWAY_BUSY', 503, 'Rate-budget state capacity reached.')
                    self.buckets[key] = [float(burst), now]
                self.users[key] = self.users.get(key, 0) + 1
                added.append(key)
            deadline = now + self.wait_timeout
            while True:
                now = self.clock(); delay = 0
                for key, rate, burst in rules:
                    bucket = self.buckets[key]
                    bucket[0] = min(burst, bucket[0] + max(0, now - bucket[1]) * rate)
                    bucket[1] = now
                    delay = max(delay, (1 - bucket[0]) / rate)
                if delay <= 0:
                    for key, _, _ in rules:
                        self.buckets[key][0] -= 1
                    return
                if now + delay > deadline:
                    raise GatewayError('RATE_WAIT_TIMEOUT', 503, 'Configured request-rate budget wait exceeded.', 'not_sent')
                await self.sleep(max(delay, .001))
        finally:
            for key in added:
                self.users[key] -= 1
                if not self.users[key]: del self.users[key]
