"""Voice Automation orchestrator – the main state-machine loop.

Wires together configuration, audio capture, speech-to-text, transcript
cleanup, text insertion, and hotkey listening into a single cohesive
pipeline.  Designed for resilience: no exception is allowed to crash the
main loop.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np

from voice_automation.audio import AudioCapture
from voice_automation.cleanup import clean_transcript
from voice_automation.config import Config, load_config
from voice_automation.hotkey import HotkeyController
from voice_automation.logger import setup_logging
from voice_automation.paste import TextInserter
from voice_automation.state import AppState, StateManager
from voice_automation.stt import SttAdapter, create_stt_adapter

logger = logging.getLogger(__name__)


def _safe_print(*args: Any, **kwargs: Any) -> None:
    """Print without letting console encoding errors break the engine."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(arg) for arg in args)
        safe_text = text.encode("ascii", errors="replace").decode("ascii")
        print(safe_text, **kwargs)


# ---------------------------------------------------------------------------
# Model loading with automatic fallback
# ---------------------------------------------------------------------------

def _get_adapter_kwargs(provider: str, cfg: Config) -> dict:
    """Return constructor arguments for the specified provider."""
    if provider == "moonshine":
        return {
            "model_arch": cfg.model_arch,
            "sample_rate": cfg.sample_rate,
            "cache_dir": cfg.get_moonshine_cache_dir(),
        }
    elif provider == "deepgram":
        return {"api_key": cfg.deepgram_api_key, "sample_rate": cfg.sample_rate}
    else:
        raise ValueError(f"Unknown provider: {provider!r}")


def _load_model(cfg: Config) -> SttAdapter:
    """Create and load an STT model, falling back if the primary fails.

    Attempts the provider specified in *cfg.model_provider* first.  If that
    fails, falls back to the alternative backend so the user can still
    dictate even if the preferred engine is unavailable.

    Raises
    ------
    RuntimeError
        If **no** backend could be loaded.
    """
    primary = cfg.model_provider
    fallback = "deepgram" if primary == "moonshine" else "moonshine"

    # ── primary attempt ───────────────────────────────────────────────
    logger.info("Loading STT model: provider=%s …", primary)
    try:
        kwargs = _get_adapter_kwargs(primary, cfg)
        adapter = create_stt_adapter(primary, **kwargs)
        if adapter.load_model():
            logger.info("STT model ready (%s)", primary)
            return adapter
        logger.warning("Primary STT provider (%s) failed to load", primary)
    except Exception:
        logger.exception("Error creating primary STT adapter (%s)", primary)

    # ── fallback attempt ──────────────────────────────────────────────
    logger.info("Trying fallback STT provider: %s …", fallback)
    try:
        kwargs = _get_adapter_kwargs(fallback, cfg)
        adapter = create_stt_adapter(fallback, **kwargs)
        if adapter.load_model():
            logger.info("Fallback STT model ready (%s)", fallback)
            return adapter
    except Exception:
        logger.exception("Error creating fallback STT adapter (%s)", fallback)

    raise RuntimeError(
        "Could not load any STT model. Run 'voice-automation check' to "
        "diagnose the problem."
    )


# ---------------------------------------------------------------------------
# Sound feedback cues
# ---------------------------------------------------------------------------

def play_sound_cue(cue_type: str, cfg: Config) -> None:
    """Play a pleasant synthesized sound cue on Windows asynchronously."""
    if not cfg.sound_cues:
        return

    import sys
    if sys.platform != "win32":
        return

    def _play():
        try:
            import winsound
            if cue_type == "start":
                # Rising pleasant double chime
                winsound.Beep(880, 70)
                winsound.Beep(1175, 70)
            elif cue_type == "success":
                # Double success chime
                winsound.Beep(1318, 60)
                winsound.Beep(1568, 80)
            elif cue_type == "error":
                # Low flat buzz
                winsound.Beep(440, 150)
        except Exception:
            pass

    import threading
    threading.Thread(target=_play, daemon=True).start()


# ---------------------------------------------------------------------------
# Orchestrator callbacks
# ---------------------------------------------------------------------------

class _Orchestrator:
    """Internal coordinator that holds references to all components."""

    def __init__(
        self,
        cfg: Config,
        state: StateManager,
        audio: AudioCapture,
        stt: SttAdapter,
        inserter: TextInserter,
    ) -> None:
        self.cfg = cfg
        self.state = state
        self.audio = audio
        self.stt = stt
        self.inserter = inserter
        # Lock to serialise press/release so a very fast tap cannot
        # interleave with an in-progress pipeline thread.
        self._pipeline_lock = threading.Lock()
        self._last_release_time = 0.0

        # Background streaming thread components
        self._streaming_thread: threading.Thread | None = None
        self._recording_start_time = 0.0

    # ── hotkey callbacks ──────────────────────────────────────────────

    def on_press(self) -> None:
        """Called when the hotkey is pressed down."""
        if self.state.get_state() is not AppState.IDLE:
            logger.debug(
                "Hotkey pressed but state is %s – ignoring",
                self.state.get_state().value,
            )
            return

        # Prevent key-repeat and key-bounce duplicate triggers
        if time.monotonic() - self._last_release_time < 0.4:
            logger.debug("Hotkey press ignored due to repeat cooldown")
            return

        try:
            self.state.set_state(AppState.RECORDING)
            self._recording_start_time = time.monotonic()
            self.audio.start_recording()

            # Play start sound cue
            play_sound_cue("start", self.cfg)

            self._streaming_thread = threading.Thread(
                target=self._feed_streaming_audio,
                daemon=True,
                name="voice-streaming-feed"
            )
            self._streaming_thread.start()

            _safe_print("🎤 Recording…")
            logger.info("Recording started")
        except Exception:
            logger.exception("Failed to start recording")
            _safe_print("❌ Could not start recording – see log")
            self.state.set_state(AppState.IDLE)

    def _feed_streaming_audio(self) -> None:
        """Read chunks from audio capture in real-time and feed them to STT adapter."""
        try:
            self.stt.start_stream()
            for chunk in self.audio.get_chunks():
                self.stt.feed_chunk(chunk)
        except Exception:
            logger.exception("Error feeding streaming audio to STT adapter")

    def on_release(self) -> None:
        """Called when the hotkey is released."""
        self._last_release_time = time.monotonic()
        if self.state.get_state() is not AppState.RECORDING:
            logger.debug(
                "Hotkey released but state is %s – ignoring",
                self.state.get_state().value,
            )
            return

        # Stop recording synchronously (fast) to free the mic immediately.
        try:
            captured_audio = self.audio.stop_recording()
        except Exception:
            logger.exception("Failed to stop recording")
            _safe_print("❌ Could not stop recording – see log")
            self.state.set_state(AppState.IDLE)
            return

        duration_s = time.monotonic() - self._recording_start_time

        # Offload the finalization to a background worker thread.
        worker = threading.Thread(
            target=self._run_finalize_pipeline,
            args=(duration_s, captured_audio),
            daemon=True,
            name="voice-pipeline-finalize",
        )
        worker.start()

    def _run_finalize_pipeline(
        self,
        duration_s: float,
        captured_audio: np.ndarray | None = None,
    ) -> None:
        try:
            # Wait for streaming thread to finish feeding remaining chunks
            if self._streaming_thread is not None:
                self._streaming_thread.join(timeout=2.0)
                self._streaming_thread = None

            # ── Guard: too-short audio ────────────────────────────
            if duration_s < self.cfg.min_record_seconds:
                _safe_print("⚠️  Recording too short (%.2fs) – skipped" % duration_s)
                logger.info(
                    "Recording too short (%.2f s < %.2f s) – skipping",
                    duration_s,
                    self.cfg.min_record_seconds,
                )
                self.stt.end_stream()
                return

            # ── Guard: model not loaded ───────────────────────────────────
            if not self.stt.is_loaded():
                logger.error("STT model is not loaded – cannot transcribe")
                _safe_print("❌ STT model not loaded")
                self.stt.end_stream()
                return

            # ── Finalize Transcription ────────────────────────────────────
            self.state.set_state(AppState.TRANSCRIBING)
            _safe_print("⏳ Transcribing…")
            logger.debug(
                "Finalizing stream transcription for %.2f s of audio",
                duration_s,
            )

            t_start_trans = time.monotonic()
            raw_text = self.stt.end_stream()
            if (
                self.cfg.model_provider == "deepgram"
                and not raw_text
                and captured_audio is not None
                and len(captured_audio) > 0
            ):
                logger.info("Deepgram stream returned empty; retrying with batch audio")
                raw_text = self.stt.transcribe(captured_audio, self.cfg.sample_rate)
            t_end_trans = time.monotonic()

            transcribe_ms = int((t_end_trans - t_start_trans) * 1000)
            self.state.transcribe_time_ms = transcribe_ms

            if not raw_text or not raw_text.strip():
                _safe_print("⚠️  No speech detected")
                logger.info("Transcription returned empty text")
                return

            # ── Clean up ──────────────────────────────────────────────────
            text = clean_transcript(
                raw_text,
                trailing_space=self.cfg.trailing_space,
                replacements=self.cfg.replacements
            )
            if not text:
                _safe_print("⚠️  No speech detected")
                logger.info("Cleaned transcript is empty")
                return

            # ── Paste ─────────────────────────────────────────────────────
            self.state.set_state(AppState.PASTING)

            # Play success sound cue
            play_sound_cue("success", self.cfg)

            t_start_paste = time.monotonic()
            ok = self.inserter.insert_text(text)
            t_end_paste = time.monotonic()

            paste_ms = int((t_end_paste - t_start_paste) * 1000)
            self.state.paste_time_ms = paste_ms

            if ok:
                # Strip trailing whitespace only for the display message
                _safe_print(f"✅ Pasted: {text.rstrip()}")
                logger.info("Inserted text: %r", text.rstrip())
            else:
                _safe_print("⚠️  Paste failed – see log for details")
                logger.warning("TextInserter.insert_text returned False")
                # Play error sound
                play_sound_cue("error", self.cfg)

        except Exception:
            logger.exception("Unhandled error in transcription pipeline")
            _safe_print("❌ Error during transcription – see log")
            # Play error sound
            play_sound_cue("error", self.cfg)
        finally:
            self.state.set_state(AppState.IDLE)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the voice-automation service and block until interrupted."""
    cfg = load_config()
    setup_logging()

    from voice_automation.service import VoiceAutomationService

    service = VoiceAutomationService(cfg)
    try:
        service.start()
    except RuntimeError as exc:
        logger.critical("%s", exc)
        _safe_print(f"\nError: {exc}")
        _safe_print("Install/configure a model backend and try again.\n")
        return

    _safe_print()
    _safe_print("═" * 52)
    _safe_print("  ✅  Voice Automation is running!")
    _safe_print(f"  🎯  Hold  [{cfg.hotkey}]  to dictate")
    _safe_print("  🛑  Press  Ctrl+C  to quit")
    _safe_print("═" * 52)
    _safe_print()

    try:
        service.wait_forever()
    except KeyboardInterrupt:
        _safe_print("\n🛑 Shutting down…")
        service.stop()
    finally:
        service.stop()
        _safe_print("👋 Goodbye!")
