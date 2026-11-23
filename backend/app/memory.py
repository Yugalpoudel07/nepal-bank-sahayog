"""
memory.py
=========
Short per-session conversation memory (last N turns), in-process.

Sessions expire after SESSION_TTL_MINUTES of inactivity and the store is
capped at MAX_SESSIONS (least-recently-used sessions are dropped).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass


@dataclass
class Turn:
    question: str
    answer: str
    fiscal_year: str


class ConversationMemory:
    """Thread-safe LRU of session_id -> recent turns."""

    def __init__(self, max_turns: int, max_sessions: int, ttl_minutes: int) -> None:
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self.ttl = ttl_minutes * 60
        self._data: OrderedDict[str, tuple[float, deque[Turn]]] = OrderedDict()
        self._lock = threading.Lock()

    def _expire(self, now: float) -> None:
        for sid in [s for s, (ts, _) in self._data.items() if now - ts > self.ttl]:
            del self._data[sid]

    def get(self, session_id: str) -> list[Turn]:
        with self._lock:
            now = time.time()
            self._expire(now)
            item = self._data.get(session_id)
            return list(item[1]) if item else []

    def add(self, session_id: str, turn: Turn) -> None:
        with self._lock:
            now = time.time()
            turns = self._data.pop(session_id, (now, deque(maxlen=self.max_turns)))[1]
            turns.append(turn)
            self._data[session_id] = (now, turns)
            while len(self._data) > self.max_sessions:
                self._data.popitem(last=False)

    def clear(self, session_id: str) -> None:
        with self._lock:
            self._data.pop(session_id, None)
