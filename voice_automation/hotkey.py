"""Global hotkey controller using pynput for key press/release detection.

Provides a non-blocking global hotkey listener that maps named hotkeys
to callbacks, with thread-safe duplicate key-down suppression.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

try:
    from pynput import keyboard

    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False
    keyboard = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# Hotkey name → pynput Key mapping
# ---------------------------------------------------------------------------

def _build_hotkey_map() -> Dict[str, "keyboard.Key"]:
    """Build the mapping from human-readable hotkey names to pynput Key objects.

    Only called when pynput is available.
    """
    if not _PYNPUT_AVAILABLE:
        return {}

    mapping: Dict[str, keyboard.Key] = {
        "right_ctrl": keyboard.Key.ctrl_r,
        "left_ctrl": keyboard.Key.ctrl_l,
        "right_alt": keyboard.Key.alt_r,
        "left_alt": keyboard.Key.alt_l,
        "right_shift": keyboard.Key.shift_r,
    }

    # Function keys f1 – f12
    for i in range(1, 13):
        mapping[f"f{i}"] = getattr(keyboard.Key, f"f{i}")

    return mapping


class HotkeyController:
    """Non-blocking global hotkey listener.

    Detects key-down and key-up events for a single configured hotkey and
    forwards them to user-supplied callbacks.  Duplicate key-down events
    (auto-repeat) are silently ignored.

    Parameters
    ----------
    hotkey_name:
        Human-readable name of the hotkey to listen for.
        Supported values: ``right_ctrl``, ``left_ctrl``, ``right_alt``,
        ``left_alt``, ``right_shift``, ``f1`` – ``f12``.
    on_press:
        Callback invoked on the *first* key-down event.  Receives no arguments.
    on_release:
        Callback invoked on the key-up event.  Receives no arguments.

    Raises
    ------
    RuntimeError
        If *pynput* is not installed.
    ValueError
        If *hotkey_name* is not a recognised hotkey.

    Example
    -------
    >>> ctrl = HotkeyController("right_ctrl", on_press=start, on_release=stop)
    >>> ctrl.start()
    >>> # … later …
    >>> ctrl.stop()
    """

    def __init__(
        self,
        hotkey_name: str,
        on_press: Callable[[], None],
        on_release: Callable[[], None],
    ) -> None:
        if not _PYNPUT_AVAILABLE:
            raise RuntimeError(
                "pynput is required but not installed. "
                "Install it with:  pip install pynput"
            )

        hotkey_map = _build_hotkey_map()
        normalised = hotkey_name.strip().lower()

        if normalised not in hotkey_map:
            supported = ", ".join(sorted(hotkey_map.keys()))
            raise ValueError(
                f"Unknown hotkey {hotkey_name!r}. Supported hotkeys: {supported}"
            )

        self._target_key: keyboard.Key = hotkey_map[normalised]
        self._on_press = on_press
        self._on_release = on_release

        # Thread-safety: _pressed is guarded by _lock
        self._lock = threading.Lock()
        self._pressed: bool = False

        self._listener: Optional[keyboard.Listener] = None

    # ------------------------------------------------------------------
    # Internal callbacks
    # ------------------------------------------------------------------

    def _handle_press(self, key: keyboard.Key) -> None:
        """Handle a raw key-down event from pynput."""
        if key != self._target_key:
            return

        with self._lock:
            if self._pressed:
                # Duplicate / auto-repeat – ignore
                return
            self._pressed = True

        try:
            self._on_press()
        except Exception:
            logger.exception("Error in on_press callback")

    def _handle_release(self, key: keyboard.Key) -> None:
        """Handle a raw key-up event from pynput."""
        if key != self._target_key:
            return

        with self._lock:
            if not self._pressed:
                return
            self._pressed = False

        try:
            self._on_release()
        except Exception:
            logger.exception("Error in on_release callback")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start listening for the configured hotkey (non-blocking).

        The listener runs in a background daemon thread managed by *pynput*.
        Calling ``start()`` while already running is a no-op.
        """
        if self._listener is not None:
            logger.debug("HotkeyController already running – ignoring start()")
            return

        self._listener = keyboard.Listener(
            on_press=self._handle_press,
            on_release=self._handle_release,
        )
        self._listener.daemon = True
        self._listener.start()
        logger.info(
            "HotkeyController started – listening for %s", self._target_key
        )

    def stop(self) -> None:
        """Stop the hotkey listener and release resources.

        Blocks until the listener thread has terminated.
        Calling ``stop()`` while not running is a no-op.
        """
        if self._listener is None:
            return

        self._listener.stop()
        self._listener.join(timeout=2.0)
        self._listener = None

        with self._lock:
            self._pressed = False

        logger.info("HotkeyController stopped")

    @property
    def is_running(self) -> bool:
        """Return ``True`` if the listener is currently active."""
        return self._listener is not None and self._listener.is_alive()
