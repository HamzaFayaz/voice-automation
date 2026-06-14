"""Thread-safe application state management."""

from __future__ import annotations

import enum
import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Type alias for state-change callbacks.
StateCallback = Callable[["AppState", "AppState"], None]


class AppState(enum.Enum):
    """Possible states the voice-automation loop can be in."""

    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"
    PASTING = "pasting"


class StateManager:
    """Manage the current :class:`AppState` with thread-safe transitions.

    Parameters
    ----------
    initial_state:
        The state to start in (default :attr:`AppState.IDLE`).
    """

    def __init__(self, initial_state: AppState = AppState.IDLE) -> None:
        self._state = initial_state
        self._lock = threading.Lock()
        self._callbacks: list[StateCallback] = []

        # Advanced dictation metrics and preview
        self.live_transcript = ""
        self.transcribe_time_ms = 0
        self.paste_time_ms = 0

    # ── Public API ────────────────────────────────────────────────────────

    def get_state(self) -> AppState:
        """Return the current application state."""
        with self._lock:
            return self._state

    def set_state(self, new_state: AppState) -> None:
        """Transition to *new_state* and notify all registered callbacks.

        Parameters
        ----------
        new_state:
            The :class:`AppState` to transition to.
        """
        with self._lock:
            old_state = self._state
            if old_state is new_state:
                return
            self._state = new_state
            logger.debug("State: %s → %s", old_state.value, new_state.value)
            callbacks = list(self._callbacks)

        # Fire callbacks outside the lock to avoid deadlocks.
        for cb in callbacks:
            try:
                cb(old_state, new_state)
            except Exception:
                logger.exception("State-change callback %r failed", cb)

    def is_recording(self) -> bool:
        """Return ``True`` if the current state is :attr:`AppState.RECORDING`."""
        return self.get_state() is AppState.RECORDING

    def reset(self) -> None:
        """Reset the state to :attr:`AppState.IDLE`."""
        self.set_state(AppState.IDLE)
        self.live_transcript = ""
        self.transcribe_time_ms = 0
        self.paste_time_ms = 0

    # ── Callback management ───────────────────────────────────────────────

    def on_state_change(self, callback: StateCallback) -> None:
        """Register a *callback* to be invoked on every state change.

        The callback receives ``(old_state, new_state)`` as arguments.
        """
        with self._lock:
            self._callbacks.append(callback)

    def remove_callback(self, callback: StateCallback) -> None:
        """Remove a previously registered *callback*."""
        with self._lock:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass
