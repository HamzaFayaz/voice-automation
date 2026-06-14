"""Audio capture module using sounddevice.

Provides mono audio recording with a thread-safe queue for real-time
chunk access, automatic timeout, and clean resource management.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Generator, Optional

import numpy as np

logger = logging.getLogger(__name__)

try:
    import sounddevice as sd

    _SD_AVAILABLE = True
except ImportError:
    _SD_AVAILABLE = False
    sd = None  # type: ignore[assignment]


class AudioCapture:
    """Mono audio recorder backed by a ``sounddevice`` InputStream.

    Audio frames are pushed into a :class:`queue.Queue` by the stream
    callback thread and can be consumed in real time via
    :meth:`get_chunks` or collected all at once via :meth:`stop_recording`.

    Parameters
    ----------
    sample_rate:
        Samples per second (Hz).  Defaults to 16 000 (common for STT).
    chunk_ms:
        Duration of each audio chunk in milliseconds.  Determines the
        ``blocksize`` passed to sounddevice.
    max_record_seconds:
        Safety cap – recording will auto-stop after this many seconds.

    Raises
    ------
    RuntimeError
        If *sounddevice* is not installed.
    """

    def __init__(
        self,
        sample_rate: int = 16_000,
        chunk_ms: int = 100,
        max_record_seconds: int = 30,
    ) -> None:
        if not _SD_AVAILABLE:
            raise RuntimeError(
                "sounddevice is required but not installed. "
                "Install it with:  pip install sounddevice"
            )

        self._sample_rate = sample_rate
        self._chunk_ms = chunk_ms
        self._max_record_seconds = max_record_seconds
        self._block_size: int = int(sample_rate * chunk_ms / 1000)

        # Recording state – guarded by _lock
        self._lock = threading.Lock()
        self._recording: bool = False
        self._start_time: float = 0.0
        self._latest_volume: float = 0.0

        # Audio data
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream: Optional[sd.InputStream] = None

        # Timeout watchdog
        self._timeout_timer: Optional[threading.Timer] = None

    # ------------------------------------------------------------------
    # sounddevice callback (runs on the PortAudio callback thread)
    # ------------------------------------------------------------------

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: object,
        status: sd.CallbackFlags,
    ) -> None:
        """Receive audio data from sounddevice and enqueue it."""
        if status:
            logger.warning("sounddevice status: %s", status)

        # Copy the data – indata buffer is reused by sounddevice
        chunk = indata[:, 0].copy()
        self._queue.put(chunk)

        # Calculate root-mean-square (RMS) for volume level indicator
        if len(chunk) > 0:
            rms = np.sqrt(np.mean(chunk**2))
            with self._lock:
                self._latest_volume = float(rms)

    # ------------------------------------------------------------------
    # Timeout handling
    # ------------------------------------------------------------------

    def _on_timeout(self) -> None:
        """Auto-stop recording when max_record_seconds is exceeded."""
        logger.warning(
            "Maximum recording time (%d s) reached – auto-stopping",
            self._max_record_seconds,
        )
        # stop_recording is safe to call from a Timer thread
        self.stop_recording()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        """Return ``True`` if audio is currently being captured."""
        with self._lock:
            return self._recording

    def start_recording(self) -> None:
        """Begin capturing audio from the default input device.

        Raises
        ------
        RuntimeError
            If already recording.
        OSError
            If no microphone / input device is available.
        """
        with self._lock:
            if self._recording:
                raise RuntimeError("Already recording")

        # Verify that an input device is available
        try:
            device_info = sd.query_devices(kind="input")
        except sd.PortAudioError as exc:
            raise OSError(
                "No microphone found. Please connect an audio input device "
                "and try again."
            ) from exc

        if device_info is None:
            raise OSError(
                "No microphone found. Please connect an audio input device "
                "and try again."
            )

        logger.info(
            "Starting audio capture: rate=%d Hz, chunk=%d ms, device=%s",
            self._sample_rate,
            self._chunk_ms,
            device_info.get("name", "unknown"),  # type: ignore[union-attr]
        )

        # Drain any stale data from a previous session
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

        try:
            self._stream = sd.InputStream(
                samplerate=self._sample_rate,
                blocksize=self._block_size,
                channels=1,
                dtype="float32",
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception:
            self._stream = None
            logger.exception("Failed to open audio stream")
            raise

        with self._lock:
            self._recording = True
            self._start_time = time.monotonic()

        # Start the timeout watchdog
        self._timeout_timer = threading.Timer(
            self._max_record_seconds, self._on_timeout
        )
        self._timeout_timer.daemon = True
        self._timeout_timer.start()

        logger.debug("Audio capture started")

    def stop_recording(self) -> np.ndarray:
        """Stop capturing and return the full recording.

        Returns
        -------
        numpy.ndarray
            1-D float32 array of audio samples normalised to [-1, 1].
            Returns an empty array if no audio was captured.
        """
        with self._lock:
            if not self._recording:
                return np.array([], dtype=np.float32)
            self._recording = False

        # Cancel the timeout timer
        if self._timeout_timer is not None:
            self._timeout_timer.cancel()
            self._timeout_timer = None

        # Close the stream
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                logger.exception("Error closing audio stream")
            finally:
                self._stream = None

        # Drain the queue into a single array
        chunks: list[np.ndarray] = []
        while True:
            try:
                chunks.append(self._queue.get_nowait())
            except queue.Empty:
                break

        if not chunks:
            logger.debug("No audio chunks captured")
            return np.array([], dtype=np.float32)

        audio = np.concatenate(chunks).astype(np.float32)

        # Normalise to [-1, 1] if needed (sounddevice float32 streams are
        # already in that range, but we guard against edge cases).
        peak = np.max(np.abs(audio))
        if peak > 1.0:
            audio = audio / peak

        duration = len(audio) / self._sample_rate
        logger.info(
            "Audio capture stopped – %.2f s, %d samples", duration, len(audio)
        )
        return audio

    def get_chunks(self) -> Generator[np.ndarray, None, None]:
        """Yield audio chunks as they arrive from the input stream.

        This is a blocking generator intended for streaming STT pipelines.
        It blocks on the internal queue with a short timeout and yields
        each chunk as a 1-D float32 numpy array.  The generator exits
        when recording stops and the queue is drained.

        Yields
        ------
        numpy.ndarray
            1-D float32 audio chunk (mono, ``chunk_ms`` duration).
        """
        while True:
            # Check if we should stop
            with self._lock:
                still_recording = self._recording

            try:
                chunk = self._queue.get(timeout=0.05)
                yield chunk
            except queue.Empty:
                if not still_recording:
                    # Recording stopped and queue is empty – we're done
                    break

    @property
    def sample_rate(self) -> int:
        """The configured sample rate in Hz."""
        return self._sample_rate

    @property
    def latest_volume(self) -> float:
        """The RMS volume of the most recently captured audio chunk."""
        with self._lock:
            if not self._recording:
                return 0.0
            return self._latest_volume
