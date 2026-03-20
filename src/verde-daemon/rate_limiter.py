"""Per-caller token-bucket rate limiter for D-Bus methods.

Read methods: 30 tokens, refill 3/sec (burst of 30, sustained 3/sec).
Write methods: 5 tokens, refill ~0.083/sec (burst of 5, sustained 1/12sec).

Callers are tracked by D-Bus unique bus name (e.g. ``:1.42``).
Stale entries are cleaned up after 300s of inactivity.

References: FR80; Story 6.2, Task 1.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger("verde-daemon.rate-limiter")

_STALE_TIMEOUT = 300.0  # seconds of inactivity before caller entry is purged

# Methods classified as write/privileged
WRITE_METHODS: frozenset[str] = frozenset({
    "InstallDriver",
    "RollbackDriver",
    "FixSuspend",
    "FixHibernate",
    "RepairDpkg",
    "DeleteSnapshot",
})


@dataclass
class RateConfig:
    """Token bucket configuration."""

    capacity: int
    refill_rate: float  # tokens per second


@dataclass
class CallerState:
    """Per-caller rate limiter state."""

    read_tokens: float
    write_tokens: float
    last_read_refill: float = field(default_factory=time.monotonic)
    last_write_refill: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Per-caller token bucket rate limiter for D-Bus methods."""

    def __init__(
        self,
        read_config: RateConfig | None = None,
        write_config: RateConfig | None = None,
        clock: object | None = None,
    ) -> None:
        self.read_config = read_config or RateConfig(capacity=30, refill_rate=3.0)
        self.write_config = write_config or RateConfig(capacity=5, refill_rate=1 / 12)
        self._callers: dict[str, CallerState] = {}
        self._clock = clock or time

    def _now(self) -> float:
        return self._clock.monotonic()  # type: ignore[union-attr]

    def _get_or_create(self, sender: str) -> CallerState:
        """Get or create caller state."""
        if sender not in self._callers:
            now = self._now()
            self._callers[sender] = CallerState(
                read_tokens=float(self.read_config.capacity),
                write_tokens=float(self.write_config.capacity),
                last_read_refill=now,
                last_write_refill=now,
                last_activity=now,
            )
        return self._callers[sender]

    def check(self, sender: str, method: str) -> bool:
        """Check if the call is allowed.  Returns True if allowed, False if rate limited."""
        state = self._get_or_create(sender)
        now = self._now()
        state.last_activity = now
        is_write = method in WRITE_METHODS

        if is_write:
            config = self.write_config
            elapsed = now - state.last_write_refill
            state.write_tokens = min(
                config.capacity,
                state.write_tokens + elapsed * config.refill_rate,
            )
            state.last_write_refill = now
            if state.write_tokens >= 1.0:
                state.write_tokens -= 1.0
                return True
            return False
        else:
            config = self.read_config
            elapsed = now - state.last_read_refill
            state.read_tokens = min(
                config.capacity,
                state.read_tokens + elapsed * config.refill_rate,
            )
            state.last_read_refill = now
            if state.read_tokens >= 1.0:
                state.read_tokens -= 1.0
                return True
            return False

    def cleanup_stale(self) -> int:
        """Remove stale caller entries.  Returns number of entries purged."""
        now = self._now()
        stale = [
            sender
            for sender, state in self._callers.items()
            if (now - state.last_activity) > _STALE_TIMEOUT
        ]
        for sender in stale:
            del self._callers[sender]
        return len(stale)
