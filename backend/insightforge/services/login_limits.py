import math
import threading
import time
from collections import deque
from collections.abc import Callable

from insightforge.config import get_settings

MAX_KEYS = 10_000


class LoginLimiter:
    """Counts failed sign-ins per email (never per IP: behind the proxy every request looks alike).

    In memory and per process, like the run queue. Anyone can lock a known email for the window by
    failing on purpose; that is the price of not trusting client addresses.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.failures: dict[str, deque[float]] = {}
        self.lock = threading.Lock()

    def _window(self) -> float:
        return get_settings().login_window_minutes * 60.0

    def _fresh(self, email: str, now: float) -> deque[float]:
        recent = self.failures.get(email)
        if recent is None:
            return deque()
        while recent and now - recent[0] >= self._window():
            recent.popleft()
        if not recent:
            self.failures.pop(email, None)
        return recent

    def retry_after(self, email: str) -> int:
        """Seconds until another attempt is allowed; 0 when the email is not locked."""
        with self.lock:
            now = self.clock()
            recent = self._fresh(email, now)
            if len(recent) < get_settings().login_max_failures:
                return 0
            return max(1, math.ceil(recent[0] + self._window() - now))

    def record_failure(self, email: str) -> None:
        with self.lock:
            now = self.clock()
            recent = self._fresh(email, now)
            recent.append(now)
            self.failures[email] = recent
            if len(self.failures) > MAX_KEYS:
                self._trim(now)

    def clear(self, email: str) -> None:
        with self.lock:
            self.failures.pop(email, None)

    def reset(self) -> None:
        with self.lock:
            self.failures.clear()

    def _trim(self, now: float) -> None:
        for key in list(self.failures):
            self._fresh(key, now)
        overflow = len(self.failures) - MAX_KEYS
        if overflow > 0:
            oldest = sorted(self.failures, key=lambda key: self.failures[key][-1])
            for key in oldest[:overflow]:
                del self.failures[key]


login_limiter = LoginLimiter()


def lockout_message(seconds: int) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    unit = "minute" if minutes == 1 else "minutes"
    return f"Too many failed sign-in attempts. Try again in {minutes} {unit}."
