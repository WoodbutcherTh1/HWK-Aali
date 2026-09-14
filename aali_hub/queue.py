"""Aali Hub request queue — admission control, fair ordering, circuit breaker.

Pure in-process state (single-process FastAPI service on the VPS). All limits
live in :class:`aali_hub.config.HubConfig` and are env-tunable:

- daily request quotas per tier: free 20 / pro 500 / enterprise 2000
  (``AALI_DAILY_FREE`` etc.) — enforced against usage read from users_db
- per-minute limits: 100 requests/min per user, 10 tool_calls/min per user,
  max 5 concurrent tool_calls per user
- max 3 concurrent requests per user
- fair ordering: round-robin across users, FIFO within a user — one user
  flooding the queue cannot starve others
- brain circuit breaker: after repeated failures the Hub answers 429 +
  Retry-After instead of stacking doomed requests

Zero I/O — the clock is injectable so tests are deterministic. Daily usage
counters are supplied by the caller (from ``users_db.usage_today``); this
module tracks only in-memory windows.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from aali_hub.config import HubConfig

__all__ = ["Rejected", "QueuedRequest", "RequestQueue"]


class Rejected(Exception):
    """Admission refusal with a machine code + optional Retry-After seconds."""

    def __init__(self, code: str, detail: str = "",
                 retry_after: int | None = None) -> None:
        self.code = code
        self.detail = detail
        self.retry_after = retry_after
        super().__init__(f"{code}: {detail}" if detail else code)


@dataclass
class QueuedRequest:
    """One admission-controlled request slot."""

    user_id: str
    tier: str
    enqueued_at: float
    kind: str = "ask"  # "ask" | "tool_call"


def _tier_daily_key(tier: str) -> str:
    """Map a role onto its daily-quota config attribute name."""
    return {
        "free_user": "daily_limit_free",
        "pro_user": "daily_limit_pro",
        "owner": "daily_limit_enterprise",
        "admin": "daily_limit_enterprise",
        "external_provider": "daily_limit_enterprise",
    }.get(tier, "daily_limit_free")


class RequestQueue:
    """Fair per-user queue with rate limits and a brain circuit breaker."""

    def __init__(self, config: HubConfig, *,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.config = config
        self._clock = clock
        # sliding per-minute windows / concurrency, per user
        self._minute_win: dict[str, deque[float]] = {}
        self._minute_tool_win: dict[str, deque[float]] = {}
        self._concurrent: dict[str, int] = {}
        self._concurrent_tool: dict[str, int] = {}
        # fair queueing: FIFO overall, round-robin service across users
        self._queue: deque[QueuedRequest] = deque()
        self._rr_cursor: str | None = None
        # circuit breaker
        self._failures: deque[float] = deque()
        self._brain_down_until: float = 0.0

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _now(self) -> float:
        return float(self._clock())

    @staticmethod
    def _prune(window: deque[float], now: float, span: float) -> None:
        while window and now - window[0] > span:
            window.popleft()

    def _check_minute(self, window: deque[float], limit: int, now: float,
                      code: str, what: str) -> None:
        self._prune(window, now, 60.0)
        if len(window) >= limit:
            retry = int(60 - (now - window[0])) + 1
            raise Rejected(code, f"{what} limit ({limit}/min) reached",
                           retry_after=max(1, retry))

    # ------------------------------------------------------------------
    # admission
    # ------------------------------------------------------------------
    def admit_request(self, user_id: str, tier: str, *,
                      used_today: int | None = None) -> None:
        """Admission-check a chat request (does NOT enqueue a slot).

        ``used_today`` is the caller's persisted count for this UTC day
        (from users_db). Raises :class:`Rejected` on any limit.
        """
        now = self._now()
        if now < self._brain_down_until:
            raise Rejected(
                "circuit_open", "brain temporarily unavailable",
                retry_after=max(1, int(self._brain_down_until - now)))

        win = self._minute_win.setdefault(user_id, deque())
        self._check_minute(win, self.config.user_requests_per_min, now,
                           "rate_limited", "requests-per-minute")
        if used_today is not None:
            daily = getattr(self.config, _tier_daily_key(tier))
            if used_today >= daily:
                raise Rejected("daily_quota_exceeded",
                               f"daily limit ({daily}) reached for {tier}",
                               retry_after=3600)
        concurrent = self._concurrent.get(user_id, 0)
        if concurrent >= self.config.max_concurrent_per_user:
            raise Rejected("too_many_concurrent",
                           f"max {self.config.max_concurrent_per_user} "
                           "concurrent requests")
        win.append(now)
        self._concurrent[user_id] = concurrent + 1

    def admit_tool_call(self, user_id: str, tier: str) -> None:
        """Admission-check a tool_call (10/min, 5 concurrent per user)."""
        now = self._now()
        if now < self._brain_down_until:
            raise Rejected(
                "circuit_open", "brain temporarily unavailable",
                retry_after=max(1, int(self._brain_down_until - now)))
        win = self._minute_tool_win.setdefault(user_id, deque())
        self._check_minute(win, self.config.tool_calls_per_min, now,
                           "rate_limited", "tool-calls-per-minute")
        concurrent = self._concurrent_tool.get(user_id, 0)
        if concurrent >= self.config.max_concurrent_tool_calls:
            raise Rejected("too_many_concurrent",
                           f"max {self.config.max_concurrent_tool_calls} "
                           "concurrent tool_calls")
        win.append(now)
        self._concurrent_tool[user_id] = concurrent + 1

    def release_request(self, user_id: str) -> None:
        """Mark a chat request finished (frees a concurrency slot)."""
        self._concurrent[user_id] = max(0, self._concurrent.get(user_id, 0) - 1)

    def release_tool_call(self, user_id: str) -> None:
        """Mark a tool_call finished (frees a concurrency slot)."""
        self._concurrent_tool[user_id] = max(
            0, self._concurrent_tool.get(user_id, 0) - 1)

    # ------------------------------------------------------------------
    # fair queueing
    # ------------------------------------------------------------------
    def enqueue(self, user_id: str, tier: str, *, kind: str = "ask"
                ) -> QueuedRequest:
        """Append a request to the queue (after admit_* passed)."""
        item = QueuedRequest(user_id=user_id, tier=tier,
                             enqueued_at=self._now(), kind=kind)
        self._queue.append(item)
        return item

    def pop_next(self) -> QueuedRequest | None:
        """Pop the next request: round-robin across users, FIFO within."""
        if not self._queue:
            self._rr_cursor = None
            return None
        if self._rr_cursor is not None:
            # advance past the last served user to the next distinct user
            idx = 0
            while idx < len(self._queue) and \
                    self._queue[idx].user_id == self._rr_cursor:
                idx += 1
            if idx < len(self._queue):
                item = self._queue[idx]
                self._rr_cursor = item.user_id
            else:
                item = self._queue[0]
                self._rr_cursor = item.user_id
            self._queue.remove(item)
            return item
        item = self._queue.popleft()
        self._rr_cursor = item.user_id
        return item

    def queue_depth(self) -> int:
        """Number of requests waiting in the queue."""
        return len(self._queue)

    # ------------------------------------------------------------------
    # circuit breaker
    # ------------------------------------------------------------------
    def record_brain_failure(self) -> None:
        """Record a brain failure; opens the breaker after 3 in a minute."""
        now = self._now()
        self._failures.append(now)
        while self._failures and now - self._failures[0] > 60.0:
            self._failures.popleft()
        if len(self._failures) >= 3:
            self._brain_down_until = now + self.config.circuit_breaker_cooldown_sec
            self._failures.clear()

    def record_brain_success(self) -> None:
        """A healthy brain response resets the failure streak."""
        self._failures.clear()
        self._brain_down_until = 0.0

    def brain_available(self) -> bool:
        """True when the circuit allows attempts."""
        return self._now() >= self._brain_down_until
