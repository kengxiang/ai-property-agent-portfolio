"""Shared admission control for Gemini Developer API HTTP requests."""

from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
from fcntl import flock, LOCK_EX
import logging
import math
import os
from threading import Lock
import time

from google.genai.errors import APIError

from llm.gemini_metrics import error_metadata


logger = logging.getLogger(__name__)


_HTTP_COUNT = ContextVar("gemini_http_count", default=None)


@contextmanager
def track_gemini_http():
    # LangGraph may copy the context into executor threads. Share the counter object.
    measurement = {"requests": 0}
    token = _HTTP_COUNT.set(measurement)
    try:
        yield measurement
    finally:
        _HTTP_COUNT.reset(token)


class GeminiRateWait(RuntimeError):
    def __init__(self, seconds):
        super().__init__("Gemini rate limiter deferred the request")
        self.retry_after_seconds = math.ceil(seconds)


class GeminiTaskStop(RuntimeError):
    """The evaluation admission budget or provider quota has closed."""


def admit_budget(path, *, stop=False):
    # Fail closed on malformed state. File-lock prevents a process restart from resetting the cap.
    with open(path, "r+", encoding="ascii") as state:
        flock(state, LOCK_EX)
        value = state.read().strip()
        count, separator, blocked = value.partition(" ")
        if not separator or not count.isdecimal() or not 0 <= int(count) <= 100 or blocked not in ("open", "closed"):
            raise GeminiTaskStop("Invalid Gemini task budget state")
        if not stop and (blocked == "closed" or int(count) == 100):
            raise GeminiTaskStop("Gemini task budget or quota exhausted")
        state.seek(0)
        state.write(f"{int(count) + int(not stop)} {'closed' if stop else 'open'}\n")
        state.truncate()
        state.flush()
        os.fsync(state.fileno())


class RollingGeminiLimit:
    window = 60
    max_wait = 20

    def __init__(self, limit=10, *, clock=time.monotonic, sleep=time.sleep, budget_path=None):
        if not 1 <= limit <= 10:
            raise ValueError("GEMINI_REQUESTS_PER_MINUTE must be between 1 and 10")
        self.limit = limit
        self.budget_path = budget_path
        self.clock = clock
        self.sleep = sleep
        self.starts = deque()
        self.cooldown_until = 0.0
        self.lock = Lock()

    def acquire(self):
        deadline = self.clock() + self.max_wait
        while True:
            with self.lock:
                now = self.clock()
                while self.starts and self.starts[0] <= now - self.window:
                    self.starts.popleft()
                earliest = self.cooldown_until
                if self.starts:
                    # Smooth bursts as well as enforcing the rolling cap.
                    earliest = max(earliest, self.starts[-1] + self.window / self.limit)
                if len(self.starts) >= self.limit:
                    earliest = max(earliest, self.starts[0] + self.window)
                if earliest <= now:
                    if self.budget_path:
                        admit_budget(self.budget_path)
                    self.starts.append(now)
                    logger.debug("Gemini HTTP request admitted; rolling_count=%d limit=%d",
                                 len(self.starts), self.limit)
                    return
                if earliest > deadline:
                    logger.info("Gemini HTTP request deferred locally; wait_seconds=%d",
                                math.ceil(earliest - now))
                    raise GeminiRateWait(earliest - now)
            self.sleep(earliest - now)

    def before_send(self, request):
        self.acquire()
        if measurement := _HTTP_COUNT.get():
            with self.lock:
                measurement["requests"] += 1

    def after_response(self, response):
        if response.status_code not in (400, 401, 403, 404, 429, 503):
            return
        response.read()
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        metadata = error_metadata(APIError(response.status_code, payload, response))
        if self.budget_path and (metadata.get("quota_scope") in ("daily", "billing") or response.status_code in (400, 401, 403, 404)):
            admit_budget(self.budget_path, stop=True)
        seconds = metadata.get("retry_after_seconds")
        if seconds is not None:
            with self.lock:
                self.cooldown_until = max(self.cooldown_until, self.clock() + seconds)
        logger.info("Gemini upstream HTTP %d; retry_after_seconds=%s quota_scope=%s",
                    response.status_code, seconds, metadata.get("quota_scope"))


@lru_cache(maxsize=1)
def shared_limiter():
    return RollingGeminiLimit(int(os.getenv("GEMINI_REQUESTS_PER_MINUTE", "10")),
                              budget_path=os.getenv("GEMINI_TASK_BUDGET_FILE"))


def client_options():
    limiter = shared_limiter()
    return {"follow_redirects": False,
            "event_hooks": {"request": [limiter.before_send], "response": [limiter.after_response]}}
