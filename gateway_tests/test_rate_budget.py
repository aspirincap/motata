"""Independent rate limits, bounded waiting and cancellation (no wall-clock delays)."""
import asyncio
import unittest
from motata_gateway.limits import RateBudget
from motata_gateway.errors import GatewayError


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.waits = []
    def clock(self): return self.now
    async def sleep(self, delay):
        self.waits.append(delay)
        self.now += delay


class RateTests(unittest.IsolatedAsyncioTestCase):
    def budget(self, **kw):
        self.t = FakeTime()
        return RateBudget(clock=self.t.clock, sleep=self.t.sleep, **kw)

    async def test_disabled_has_no_state(self):
        b = self.budget()
        for n in range(100): await b.wait(('meta', str(n)))
        self.assertEqual(b.buckets, {})
        self.assertEqual(self.t.waits, [])

    async def test_global_rate_not_concurrency(self):
        b = self.budget(global_rate=2, burst=2, account_burst=1)
        for i in range(4): await b.wait(('meta', str(i)))
        self.assertEqual(self.t.waits, [.5, .5])
        self.assertEqual(b.users, {})

    async def test_account_budget_does_not_consume_global_while_waiting(self):
        b = self.budget(global_rate=10, account_rate=1, burst=10, account_burst=1)
        await b.wait(('meta', 'a'))
        await b.wait(('meta', 'a'))
        self.assertEqual(self.t.waits, [1.0])
        self.assertEqual(b.buckets[('global',)][0], 9)
        await b.wait(('meta', 'b'))
        self.assertEqual(self.t.waits, [1.0])
        self.assertEqual(b.buckets[('global',)][0], 8)

    async def test_deadline_and_waiter_cleanup(self):
        b = self.budget(global_rate=.1, burst=1, account_burst=1, wait_timeout=1)
        await b.wait(('meta', 'a'))
        with self.assertRaises(GatewayError) as exc:
            await b.wait(('meta', 'a'))
        self.assertEqual(exc.exception.code, 'RATE_WAIT_TIMEOUT')
        self.assertEqual(b.users, {})

    async def test_bucket_capacity_and_idle_pruning(self):
        b = self.budget(global_rate=10, account_rate=10, capacity=2)
        await b.wait(('meta', 'a'))
        with self.assertRaises(GatewayError): await b.wait(('meta', 'b'))
        self.assertEqual(b.users, {})
        self.t.now += 301
        await b.wait(('meta', 'b'))
        self.assertNotIn(('account', 'meta', 'a'), b.buckets)

    async def test_cancelled_waiter_releases_state(self):
        started = asyncio.Event()
        async def stop(delay):
            started.set()
            await asyncio.Event().wait()
        b = RateBudget(global_rate=.1, burst=1, account_burst=1, sleep=stop)
        await b.wait(('meta', 'a'))
        task = asyncio.create_task(b.wait(('meta', 'a')))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(b.users, {})

    async def test_50_waiters_no_negative_tokens(self):
        b = self.budget(global_rate=10, account_rate=10, burst=10, account_burst=2)
        await asyncio.gather(*(b.wait(('meta', str(i % 5))) for i in range(50)))
        self.assertEqual(b.users, {})
        self.assertTrue(all(row[0] >= 0 for row in b.buckets.values()))
        self.assertGreaterEqual(self.t.now, 4)

    async def test_invalid_configuration_rejected(self):
        for kw in ({'global_rate': 0}, {'global_rate': True}, {'global_rate': float('nan')},
                   {'account_rate': -1}, {'wait_timeout': float('inf')}, {'burst': 1.5},
                   {'burst': True}, {'capacity': 1}, {'capacity': 2.5}):
            with self.subTest(kw=kw), self.assertRaises(ValueError): RateBudget(**kw)
