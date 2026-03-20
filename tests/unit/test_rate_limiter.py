"""Unit tests for Story 6.2: Rate Limiter."""

from __future__ import annotations

from rate_limiter import RateConfig, RateLimiter


class _MockClock:
    """Mock clock for deterministic time in tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class TestTokenBucket:
    def test_burst_within_limit_succeeds(self):
        clock = _MockClock()
        rl = RateLimiter(
            read_config=RateConfig(capacity=5, refill_rate=1.0),
            clock=clock,
        )
        for _ in range(5):
            assert rl.check(":1.1", "GetGPUInfo") is True

    def test_exceeding_limit_rejected(self):
        clock = _MockClock()
        rl = RateLimiter(
            read_config=RateConfig(capacity=3, refill_rate=1.0),
            clock=clock,
        )
        for _ in range(3):
            assert rl.check(":1.1", "GetGPUInfo") is True
        assert rl.check(":1.1", "GetGPUInfo") is False

    def test_rate_limit_resets_after_window(self):
        clock = _MockClock()
        rl = RateLimiter(
            read_config=RateConfig(capacity=3, refill_rate=1.0),
            clock=clock,
        )
        for _ in range(3):
            rl.check(":1.1", "GetGPUInfo")
        assert rl.check(":1.1", "GetGPUInfo") is False

        clock.advance(2.0)  # 2 tokens refilled
        assert rl.check(":1.1", "GetGPUInfo") is True

    def test_separate_callers_independent(self):
        clock = _MockClock()
        rl = RateLimiter(
            read_config=RateConfig(capacity=2, refill_rate=0.1),
            clock=clock,
        )
        assert rl.check(":1.1", "GetGPUInfo") is True
        assert rl.check(":1.1", "GetGPUInfo") is True
        assert rl.check(":1.1", "GetGPUInfo") is False

        # Different caller still has full bucket
        assert rl.check(":1.2", "GetGPUInfo") is True
        assert rl.check(":1.2", "GetGPUInfo") is True

    def test_write_methods_use_write_limit(self):
        clock = _MockClock()
        rl = RateLimiter(
            read_config=RateConfig(capacity=100, refill_rate=10.0),
            write_config=RateConfig(capacity=2, refill_rate=0.1),
            clock=clock,
        )
        assert rl.check(":1.1", "InstallDriver") is True
        assert rl.check(":1.1", "InstallDriver") is True
        assert rl.check(":1.1", "InstallDriver") is False

        # Read methods still available
        assert rl.check(":1.1", "GetGPUInfo") is True

    def test_stale_entry_cleanup(self):
        clock = _MockClock()
        rl = RateLimiter(clock=clock)
        rl.check(":1.1", "GetGPUInfo")
        rl.check(":1.2", "GetGPUInfo")
        assert len(rl._callers) == 2

        clock.advance(301.0)  # past stale timeout
        purged = rl.cleanup_stale()
        assert purged == 2
        assert len(rl._callers) == 0

    def test_active_entry_not_cleaned(self):
        clock = _MockClock()
        rl = RateLimiter(clock=clock)
        rl.check(":1.1", "GetGPUInfo")
        clock.advance(200.0)
        rl.check(":1.1", "GetGPUInfo")  # refreshes last_activity
        clock.advance(200.0)  # 200s since last activity, total 400s
        purged = rl.cleanup_stale()
        assert purged == 0

    def test_default_read_config(self):
        rl = RateLimiter()
        assert rl.read_config.capacity == 30
        assert rl.read_config.refill_rate == 3.0

    def test_default_write_config(self):
        rl = RateLimiter()
        assert rl.write_config.capacity == 5
