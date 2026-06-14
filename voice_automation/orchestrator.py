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

from voice_automation.audio import AudioCapture
from voice_automation.cleanup import clean_transcript
from voice_automation.config import Config, load_config
from voice_automation.hotkey import HotkeyController
from voice_automation.logger import setup_logging
from voice_automation.paste import TextInserter
from voice_automation.state import AppState, StateManager
from voice_automation.stt import SttAdapter, create_stt_adapter

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Model loading with automatic fallback
# ---------------------------------------------------------------------------

def _get_adapter_kwargs(provider: str, cfg: Config) -> dict:
    """Return constructor arguments for the specified provider."""
    if provider == "moonshine":
        return {"model_arch": cfg.model_arch}
    elif provider == "deepgram":
        return {"api_key": cfg.deepgram_api_key}
    else:
        return {"model_size": cfg.model_size, "compute_type": "int8"}


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
    fallback = "faster-whisper" if primary == "moonshine" else "moonshine"

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

            # Start background streaming feed thread
            self._streaming_thread = threading.Thread(
                target=self._feed_streaming_audio,
                daemon=True,
                name="voice-streaming-feed"
            )
            self._streaming_thread.start()

            print("🎤 Recording…")
            logger.info("Recording started")
        except Exception:
            logger.exception("Failed to start recording")
            print("❌ Could not start recording – see log")
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
            self.audio.stop_recording()
        except Exception:
            logger.exception("Failed to stop recording")
            print("❌ Could not stop recording – see log")
            self.state.set_state(AppState.IDLE)
            return

        duration_s = time.monotonic() - self._recording_start_time

        # Offload the finalization to a background worker thread.
        worker = threading.Thread(
            target=self._run_finalize_pipeline,
            args=(duration_s,),
            daemon=True,
            name="voice-pipeline-finalize",
        )
        worker.start()

    def _run_finalize_pipeline(self, duration_s: float) -> None:
        try:
            # Wait for streaming thread to finish feeding remaining chunks
            if self._streaming_thread is not None:
                self._streaming_thread.join(timeout=2.0)
                self._streaming_thread = None

            # ── Guard: too-short audio ────────────────────────────
            if duration_s < self.cfg.min_record_seconds:
                print("⚠️  Recording too short (%.2fs) – skipped" % duration_s)
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
                print("❌ STT model not loaded")
                self.stt.end_stream()
                return

            # ── Finalize Transcription ────────────────────────────────────
            self.state.set_state(AppState.TRANSCRIBING)
            print("⏳ Transcribing…")
            logger.debug(
                "Finalizing stream transcription for %.2f s of audio",
                duration_s,
            )

            t_start_trans = time.monotonic()
            raw_text = self.stt.end_stream()
            t_end_trans = time.monotonic()

            transcribe_ms = int((t_end_trans - t_start_trans) * 1000)
            self.state.transcribe_time_ms = transcribe_ms

            if not raw_text or not raw_text.strip():
                print("⚠️  No speech detected")
                logger.info("Transcription returned empty text")
                return

            # ── Clean up ──────────────────────────────────────────────────
            text = clean_transcript(
                raw_text,
                trailing_space=self.cfg.trailing_space,
                replacements=self.cfg.replacements
            )
            if not text:
                print("⚠️  No speech detected")
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
                print(f"✅ Pasted: {text.rstrip()}")
                logger.info("Inserted text: %r", text.rstrip())
            else:
                print("⚠️  Paste failed – see log for details")
                logger.warning("TextInserter.insert_text returned False")
                # Play error sound
                play_sound_cue("error", self.cfg)

        except Exception:
            logger.exception("Unhandled error in transcription pipeline")
            print("❌ Error during transcription – see log")
            # Play error sound
            play_sound_cue("error", self.cfg)
        finally:
            self.state.set_state(AppState.IDLE)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the voice-automation orchestrator.

    This is the top-level function invoked by ``__main__.py``.  It sets up
    all components, prints a status banner, then enters a blocking loop
    that keeps the process alive until interrupted by Ctrl+C.
    """
    # 1 ── Configuration & logging ─────────────────────────────────────
    cfg = load_config()
    setup_logging()

    logger.info("Voice Automation starting up")
    logger.info(
        "Config: hotkey=%s  provider=%s  paste=%s  language=%s",
        cfg.hotkey,
        cfg.model_provider,
        cfg.paste_mode,
        cfg.language,
    )

    # 2 ── Core components ─────────────────────────────────────────────
    state = StateManager()

    audio = AudioCapture(
        sample_rate=cfg.sample_rate,
        chunk_ms=cfg.chunk_ms,
        max_record_seconds=cfg.max_record_seconds,
    )

    inserter = TextInserter(
        paste_mode=cfg.paste_mode,
        clipboard_restore_delay=cfg.clipboard_restore_delay,
    )

    # 3 ── STT model (with fallback) ───────────────────────────────────
    try:
        stt = _load_model(cfg)
        stt.set_state_manager(state)
    except RuntimeError as exc:
        logger.critical("%s", exc)
        print(f"\n❌ {exc}")
        print("   Install a model backend and try again.\n")
        return

    # 4 ── Orchestrator + hotkey ────────────────────────────────────────
    orch = _Orchestrator(cfg, state, audio, stt, inserter)

    try:
        hotkey = HotkeyController(
            hotkey_name=cfg.hotkey,
            on_press=orch.on_press,
            on_release=orch.on_release,
        )
    except (RuntimeError, ValueError) as exc:
        logger.critical("Cannot create hotkey controller: %s", exc)
        print(f"\n❌ {exc}\n")
        stt.unload()
        return

    hotkey.start()

    # ── Dictation Overlay HUD ─────────────────────────────────────────
    overlay = None
    try:
        from voice_automation.overlay import DictationOverlay

        overlay = DictationOverlay(state, audio)
        overlay.start()
        logger.info("Dictation overlay HUD started")
    except Exception as exc:
        logger.warning("Could not start dictation overlay HUD: %s", exc)

    # 5 ── Ready banner ────────────────────────────────────────────────
    print()
    print("═" * 52)
    print("  ✅  Voice Automation is running!")
    print(f"  🎯  Hold  [{cfg.hotkey}]  to dictate")
    print("  🛑  Press  Ctrl+C  to quit")
    print("═" * 52)
    print()

    # 6 ── Main loop (keep alive) ──────────────────────────────────────
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n🛑 Shutting down…")
        logger.info("KeyboardInterrupt received – shutting down")
    finally:
        # ── Graceful teardown ─────────────────────────────────────────
        if overlay:
            try:
                overlay.stop()
                logger.info("Dictation overlay HUD stopped")
            except Exception:
                logger.exception("Error stopping dictation overlay HUD")

        hotkey.stop()
        logger.info("Hotkey listener stopped")

        # If a recording is in progress, stop it cleanly.
        if audio.is_recording:
            try:
                audio.stop_recording()
                logger.info("In-progress recording stopped")
            except Exception:
                logger.exception("Error stopping active recording")

        stt.unload()
        logger.info("STT model unloaded")

        state.reset()
        logger.info("Voice Automation shut down cleanly")
        print("👋 Goodbye!")
