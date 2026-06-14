"""Floating dictation overlay GUI using tkinter.

Displays a compact, elegant dark-mode HUD in the bottom-right corner of the
screen when recording, transcribing, or pasting. Integrates a pulsing audio
waveform indicator driven by the real-time RMS input volume.
"""

from __future__ import annotations

import logging
import math
import random
import sys
import time
import threading
import tkinter as tk
from typing import Optional

from voice_automation.audio import AudioCapture
from voice_automation.state import AppState, StateManager

logger = logging.getLogger(__name__)


class DictationOverlay:
    """Borderless, top-most Tkinter HUD overlay for dictation feedback."""

    def __init__(self, state_manager: StateManager, audio_capture: AudioCapture) -> None:
        self.state_manager = state_manager
        self.audio_capture = audio_capture

        self.root: Optional[tk.Tk] = None
        self.status_label: Optional[tk.Label] = None
        self.canvas: Optional[tk.Canvas] = None
        self.thread: Optional[threading.Thread] = None

        # Thread-safe interface between orchestrator and Tkinter thread
        self._pending_state: Optional[AppState] = None
        self._current_gui_state: AppState = AppState.IDLE

        # Delay hide control
        self._was_pasting: bool = False
        self._hide_timer_id: Optional[str] = None

        # Register callback
        self.state_manager.on_state_change(self._on_state_change)

    def start(self) -> None:
        """Start the Tkinter GUI event loop in a background daemon thread."""
        self.thread = threading.Thread(target=self._run_gui, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Destroy the Tkinter window and stop the loop."""
        if self.root:
            try:
                # Schedule destruction on the Tkinter thread to remain thread-safe
                self.root.after(0, self.root.destroy)
            except Exception:
                pass

    def _on_state_change(self, old_state: AppState, new_state: AppState) -> None:
        """Receive state updates from the state manager (called from worker threads)."""
        self._pending_state = new_state

    def _run_gui(self) -> None:
        """Initialise the Tkinter window and start the main loop."""
        try:
            self.root = tk.Tk()
        except Exception as exc:
            logger.warning("Could not initialise Tkinter (no display?): %s", exc)
            return

        self.root.title("Voice Automation HUD")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)

        # Style colors (Catppuccin Mocha theme)
        bg_color = "#1e1e2e"

        self.root.configure(bg=bg_color)

        # Position HUD in the bottom-right corner, offset from taskbar
        width = 250
        height = 90
        try:
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
        except Exception:
            screen_width, screen_height = 1920, 1080

        x = screen_width - width - 30
        y = screen_height - height - 80
        self.root.geometry(f"{width}x{height}+{x}+{y}")

        # Rounded/framed container frame
        frame = tk.Frame(
            self.root,
            bg=bg_color,
            highlightbackground="#313244",
            highlightthickness=1,
        )
        frame.pack(fill="both", expand=True)

        # Status Label
        self.status_label = tk.Label(
            frame,
            text="Listening...",
            font=("Segoe UI", 11, "bold"),
            fg="#cdd6f4",
            bg=bg_color,
        )
        self.status_label.pack(pady=(10, 2))

        # Audio Waveform Canvas
        self.canvas = tk.Canvas(
            frame,
            width=200,
            height=40,
            bg=bg_color,
            highlightthickness=0,
        )
        self.canvas.pack()

        # Start hidden
        self.root.withdraw()

        # Begin GUI tick update loop
        self._update_loop()

        self.root.mainloop()

    def _update_loop(self) -> None:
        """GUI tick loop. Resolves state transitions and triggers animations."""
        if not self.root:
            return

        # 1. State changes
        if self._pending_state is not None:
            state = self._pending_state
            self._pending_state = None
            self._apply_state(state)

        # 2. Frame animations & dynamic labels
        if self._current_gui_state is AppState.RECORDING:
            self._draw_waveform()
            self.status_label.configure(text="🎤 Listening...", fg="#a6e3a1")
        elif self._current_gui_state is AppState.TRANSCRIBING:
            self._draw_transcribing()

        # Tick at 20 FPS (every 50 ms)
        self.root.after(50, self._update_loop)

    def _apply_state(self, state: AppState) -> None:
        """Apply state-specific visual adjustments."""
        self._current_gui_state = state

        # If a new recording or transcribing starts, cancel the auto-hide timer
        if state in (AppState.RECORDING, AppState.TRANSCRIBING) and self._hide_timer_id is not None:
            try:
                self.root.after_cancel(self._hide_timer_id)
            except Exception:
                pass
            self._hide_timer_id = None
            self._was_pasting = False

        if state is AppState.IDLE:
            # If we were not pasting (or if the paste message timer has completed), hide immediately
            if not self._was_pasting:
                self.root.withdraw()
        else:
            self.root.deiconify()
            self.root.attributes("-topmost", True)

            if state is AppState.RECORDING:
                self.status_label.configure(text="🎤 Listening...", fg="#a6e3a1")  # Mocha Green
                self.canvas.delete("all")

            elif state is AppState.TRANSCRIBING:
                self.status_label.configure(text="⏳ Transcribing...", fg="#fab387")  # Mocha Peach
                self.canvas.delete("all")

            elif state is AppState.PASTING:
                self._was_pasting = True
                trans_ms = self.state_manager.transcribe_time_ms
                paste_ms = self.state_manager.paste_time_ms
                metric_str = f" ({trans_ms + paste_ms}ms)" if (trans_ms or paste_ms) else ""
                self.status_label.configure(text=f"✅ Pasted!{metric_str}", fg="#89b4fa")  # Mocha Blue
                self.canvas.delete("all")
                # Auto-hide HUD shortly after pasting
                self._hide_timer_id = self.root.after(1200, self._hide_overlay)

    def _hide_overlay(self) -> None:
        """Hide the overlay window if the application is currently IDLE."""
        self._hide_timer_id = None
        self._was_pasting = False
        if self._current_gui_state is AppState.IDLE and self.root:
            self.root.withdraw()

    def _draw_waveform(self) -> None:
        """Draw a beautiful, smooth, pulsing/morphing voice orb like ChatGPT Live."""
        if not self.canvas:
            return

        self.canvas.delete("all")

        # Map current RMS volume level to scale (0.0 to 1.0)
        volume = self.audio_capture.latest_volume
        scaled_vol = min(1.0, volume / 0.25)

        width = 200
        height = 40
        cx = width / 2
        cy = height / 2

        # Base radius
        base_radius = 6.0
        # Target radius based on volume
        target_radius = base_radius + (scaled_vol * 8.0)

        # Draw a beautiful, smooth morphing orb using a smooth closed polygon
        # We vary the radius at different angles using a sine wave over time
        t = time.monotonic() * 10
        num_points = 36

        # 1. Outer soft halo
        halo_radius = target_radius + 3.0
        halo_points = []
        for i in range(num_points):
            angle = (2 * math.pi * i) / num_points
            undulation = 1.0 + 0.15 * math.sin(angle * 3 + t) + 0.1 * math.cos(angle * 5 - t * 1.5)
            r = halo_radius * undulation
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            halo_points.extend((x, y))

        self.canvas.create_polygon(halo_points, fill="#89b4fa", outline="", smooth=True)

        # 2. Inner core
        inner_points = []
        for i in range(num_points):
            angle = (2 * math.pi * i) / num_points
            undulation = 1.0 + 0.08 * math.sin(angle * 4 - t * 2.0)
            r = target_radius * undulation
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            inner_points.extend((x, y))

        self.canvas.create_polygon(inner_points, fill="#cdd6f4", outline="", smooth=True)

    def _draw_transcribing(self) -> None:
        """Draw a morphing, breathing orb that slowly breathes to represent processing."""
        if not self.canvas:
            return

        self.canvas.delete("all")

        width = 200
        height = 40
        cx = width / 2
        cy = height / 2

        t = time.monotonic() * 5
        r_base = 8.0 + 2.0 * math.sin(t)

        num_points = 36
        points = []
        for i in range(num_points):
            angle = (2 * math.pi * i) / num_points
            undulation = 1.0 + 0.1 * math.sin(angle * 3 + t * 1.5)
            r = r_base * undulation
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            points.extend((x, y))

        self.canvas.create_polygon(points, fill="#fab387", outline="", smooth=True)
