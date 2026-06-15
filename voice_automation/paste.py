"""Text insertion (paste / type) module.

Provides :class:`TextInserter` which can inject text into the currently
focused application via either the system clipboard (Ctrl+V) or by
simulating individual key presses.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


def get_active_window_info() -> tuple[str, str]:
    """Return (process_name, window_title) of foreground window on Windows."""
    import sys
    if sys.platform != "win32":
        return "", ""

    import ctypes
    from ctypes import wintypes
    from pathlib import Path

    try:
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if not hwnd:
            return "", ""

        # Title
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        title_buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, title_buf, length + 1)
        title = title_buf.value

        # Process ID
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))

        # Open process
        PROCESS_QUERY_INFORMATION = 0x0400
        PROCESS_VM_READ = 0x0010
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)

        process_name = ""
        if handle:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                process_name = Path(buf.value).name.lower()
            ctypes.windll.kernel32.CloseHandle(handle)

        return process_name, title
    except Exception:
        return "", ""


class TextInserter:
    """Insert text into the active window.

    Two strategies are supported:

    * **clipboard** – copy to clipboard, simulate Ctrl+V, then restore the
      previous clipboard contents.
    * **type** – simulate individual key presses via *pynput*.

    If the primary strategy fails the other is attempted as a fallback.

    Parameters
    ----------
    paste_mode:
        ``"clipboard"`` (default) or ``"type"``.
    clipboard_restore_delay:
        Seconds to wait after pasting before restoring the original
        clipboard content.  Increase if applications don't receive the
        paste in time.
    """

    VALID_MODES = ("clipboard", "type")

    def __init__(
        self,
        paste_mode: str = "clipboard",
        clipboard_restore_delay: float = 0.15,
    ) -> None:
        if paste_mode not in self.VALID_MODES:
            raise ValueError(
                f"paste_mode must be one of {self.VALID_MODES!r}, "
                f"got {paste_mode!r}"
            )
        self._paste_mode: str = paste_mode
        self._clipboard_restore_delay: float = clipboard_restore_delay

    # -- public API ----------------------------------------------------------

    def insert_text(self, text: str) -> bool:
        """Insert *text* into the currently focused application.

        Tries the configured ``paste_mode`` first; on failure, falls back
        to the alternative method.

        Returns
        -------
        bool
            *True* if the text was inserted successfully by either method.
        """
        if not text:
            logger.debug("insert_text called with empty text – nothing to do")
            return True

        # Context-aware adjustments
        process_name, title = get_active_window_info()
        logger.debug("Active window process: %s, title: %s", process_name, title)

        is_terminal = process_name in ("cmd.exe", "powershell.exe", "wt.exe", "bash.exe", "conhost.exe")
        should_execute = False

        if is_terminal:
            trimmed = text.strip()
            import re
            # Match "execute" or "run" as the last word case-insensitively
            match = re.search(r"\b(execute|run)$", trimmed, re.IGNORECASE)
            if match:
                text = text[:match.start()].rstrip()
                should_execute = True
                if not text:
                    return True

        primary = self._paste_via_clipboard if self._paste_mode == "clipboard" else self._type_directly
        fallback = self._type_directly if self._paste_mode == "clipboard" else self._paste_via_clipboard
        primary_name = "clipboard" if self._paste_mode == "clipboard" else "type"
        fallback_name = "type" if self._paste_mode == "clipboard" else "clipboard"

        success = False
        if primary(text):
            success = True
        elif fallback(text):
            success = True

        if success and should_execute:
            try:
                from pynput.keyboard import Controller, Key
                keyboard = Controller()
                # Brief sleep to make sure paste operation is registered by the OS
                time.sleep(0.05)
                keyboard.press(Key.enter)
                keyboard.release(Key.enter)
                logger.info("Auto-executed command in terminal")
            except Exception as e:
                logger.warning("Failed to auto-execute command: %s", e)

        return success

    # -- private strategies --------------------------------------------------

    def _paste_via_clipboard(self, text: str) -> bool:
        """Insert *text* by copying to the clipboard and sending Ctrl+V."""
        try:
            import pyperclip
            from pynput.keyboard import Controller, Key
        except ImportError:
            logger.error(
                "pyperclip and/or pynput are not installed. "
                "Install them with: pip install pyperclip pynput"
            )
            return False

        keyboard = Controller()

        try:
            # 1 – save current clipboard
            original_clipboard: Optional[str] = None
            try:
                original_clipboard = pyperclip.paste()
            except Exception:
                logger.debug("Could not read current clipboard – will not restore")

            # 2 – set transcript text
            pyperclip.copy(text)

            # 3 – small delay to let the clipboard settle
            time.sleep(0.05)

            # 4 – Ctrl+V
            keyboard.press(Key.ctrl)
            keyboard.press("v")
            keyboard.release("v")
            keyboard.release(Key.ctrl)

            # 5 – wait before restoring
            time.sleep(self._clipboard_restore_delay)

            # 6 – restore original clipboard
            if original_clipboard is not None:
                try:
                    pyperclip.copy(original_clipboard)
                except Exception:
                    logger.debug("Could not restore original clipboard content")

            logger.debug("Text inserted via clipboard paste (%d chars)", len(text))
            return True

        except Exception:
            logger.exception("Clipboard paste failed")
            return False

    def _type_directly(self, text: str) -> bool:
        """Insert *text* by sending direct keyboard input."""
        try:
            from pynput.keyboard import Controller
        except ImportError:
            logger.error(
                "pynput is not installed. Install it with: pip install pynput"
            )
            return False

        keyboard = Controller()

        try:
            keyboard.type(text)
            logger.debug("Text inserted via direct typing (%d chars)", len(text))
            return True

        except Exception:
            logger.exception("Direct typing failed")
            return False
