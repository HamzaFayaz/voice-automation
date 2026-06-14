"""Speech-to-text adapter module.

Provides an abstract STT interface and concrete implementations for
Moonshine and faster-whisper backends. Heavy dependencies are imported
lazily so the module can be loaded even when a backend is not installed.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class SttAdapter(ABC):
    """Abstract base class for speech-to-text adapters."""

    def set_state_manager(self, state_manager: Any) -> None:
        """Set the shared application state manager (optional)."""
        pass

    @abstractmethod
    def load_model(self) -> bool:
        """Load the underlying model. Returns *True* on success."""
        ...

    @abstractmethod
    def is_loaded(self) -> bool:
        """Return whether the model is currently loaded and ready."""
        ...

    @abstractmethod
    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        """Transcribe a complete audio buffer (batch mode).

        Parameters
        ----------
        audio:
            1-D float32 numpy array of audio samples.
        sample_rate:
            Sample rate of *audio* in Hz.

        Returns
        -------
        str
            The transcribed text.
        """
        ...

    @abstractmethod
    def start_stream(self) -> None:
        """Begin a new streaming transcription session."""
        ...

    @abstractmethod
    def feed_chunk(self, chunk: np.ndarray) -> None:
        """Feed an audio chunk into the current streaming session.

        Parameters
        ----------
        chunk:
            1-D float32 numpy array of audio samples.
        """
        ...

    @abstractmethod
    def get_partial(self) -> str:
        """Return the current partial transcript for the active stream."""
        ...

    @abstractmethod
    def end_stream(self) -> str:
        """Finalise the streaming session and return the complete transcript."""
        ...

    @abstractmethod
    def unload(self) -> None:
        """Release all model resources."""
        ...


# ---------------------------------------------------------------------------
# Moonshine implementation
# ---------------------------------------------------------------------------

class MoonshineSttAdapter(SttAdapter):
    """STT adapter backed by the *moonshine_voice* package.

    Parameters
    ----------
    model_arch:
        Model architecture identifier passed to
        ``moonshine_voice.Transcriber``.  Defaults to ``4`` (Small Streaming).
    """

    def __init__(self, model_arch: int = 4) -> None:
        self._model_arch: int = model_arch
        self._transcriber: Any = None  # moonshine_voice.Transcriber
        self._stream_chunks: list[np.ndarray] = []
        self._streaming: bool = False
        self._partial: str = ""

    # -- lifecycle -----------------------------------------------------------

    def load_model(self) -> bool:
        """Create the Moonshine ``Transcriber`` instance."""
        try:
            from moonshine_voice import Transcriber, ModelArch, get_model_for_language  # lazy import
        except ImportError:
            logger.error(
                "moonshine_voice is not installed. "
                "Install it with: pip install moonshine_voice"
            )
            return False

        try:
            arch_enum = ModelArch(self._model_arch)
            path, arch = get_model_for_language(wanted_model_arch=arch_enum)
            self._transcriber = Transcriber(path, arch)
            logger.info(
                "Moonshine model loaded from %s (arch=%s)", path, arch.name
            )
            return True
        except FileNotFoundError:
            logger.error(
                "Moonshine model files not found – have you downloaded them?"
            )
            return False
        except Exception:
            logger.exception("Failed to load Moonshine model")
            return False

    def is_loaded(self) -> bool:
        return self._transcriber is not None

    def unload(self) -> None:
        """Release the transcriber."""
        self._transcriber = None
        self._stream_chunks.clear()
        self._streaming = False
        self._partial = ""
        logger.info("Moonshine model unloaded")

    # -- batch ---------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if not self.is_loaded():
            logger.error("Model not loaded – call load_model() first")
            return ""
        try:
            transcript = self._transcriber.transcribe_without_streaming(audio.tolist(), sample_rate)
            text = " ".join(line.text for line in transcript.lines)
            return text.strip()
        except Exception:
            logger.exception("Moonshine batch transcription failed")
            return ""

    # -- streaming -----------------------------------------------------------

    def start_stream(self) -> None:
        if not self.is_loaded():
            logger.error("Model not loaded – call load_model() first")
            return
        self._stream_chunks.clear()
        self._streaming = True
        self._partial = ""
        logger.debug("Moonshine stream started")

    def feed_chunk(self, chunk: np.ndarray) -> None:
        if not self._streaming:
            logger.warning("feed_chunk called outside a streaming session")
            return
        self._stream_chunks.append(chunk)

        # Attempt incremental transcription on accumulated audio so far.
        try:
            combined = np.concatenate(self._stream_chunks)
            # Use 16 kHz as the default streaming sample rate.
            transcript = self._transcriber.transcribe_without_streaming(combined.tolist(), 16000)
            text = " ".join(line.text for line in transcript.lines)
            self._partial = text.strip()
        except Exception:
            logger.debug(
                "Partial transcription unavailable, will finalise on end_stream"
            )

    def get_partial(self) -> str:
        return self._partial

    def end_stream(self) -> str:
        if not self._streaming:
            logger.warning("end_stream called outside a streaming session")
            return ""
        self._streaming = False

        if not self._stream_chunks:
            logger.debug("Stream ended with no audio chunks")
            return ""

        combined = np.concatenate(self._stream_chunks)
        self._stream_chunks.clear()

        try:
            transcript = self._transcriber.transcribe_without_streaming(combined.tolist(), 16000)
            result = " ".join(line.text for line in transcript.lines)
            self._partial = ""
            return result.strip()
        except Exception:
            logger.exception("Moonshine stream finalisation failed")
            self._partial = ""
            return ""


# ---------------------------------------------------------------------------
# faster-whisper implementation
# ---------------------------------------------------------------------------

class FasterWhisperSttAdapter(SttAdapter):
    """STT adapter backed by *faster-whisper*.

    Parameters
    ----------
    model_size:
        Whisper model size string (e.g. ``"base"``, ``"small"``).
    compute_type:
        CTranslate2 compute type (default ``"int8"``).
    """

    def __init__(
        self,
        model_size: str = "base",
        compute_type: str = "int8",
    ) -> None:
        self._model_size: str = model_size
        self._compute_type: str = compute_type
        self._model: Any = None  # faster_whisper.WhisperModel
        self._stream_chunks: list[np.ndarray] = []
        self._streaming: bool = False

    # -- lifecycle -----------------------------------------------------------

    def load_model(self) -> bool:
        """Create the faster-whisper ``WhisperModel``."""
        try:
            from faster_whisper import WhisperModel  # lazy import
        except ImportError:
            logger.error(
                "faster_whisper is not installed. "
                "Install it with: pip install faster-whisper"
            )
            return False

        try:
            self._model = WhisperModel(
                self._model_size,
                device="cpu",
                compute_type=self._compute_type,
            )
            logger.info(
                "faster-whisper model loaded (size=%s, compute=%s)",
                self._model_size,
                self._compute_type,
            )
            return True
        except Exception:
            logger.exception("Failed to load faster-whisper model")
            return False

    def is_loaded(self) -> bool:
        return self._model is not None

    def unload(self) -> None:
        self._model = None
        self._stream_chunks.clear()
        self._streaming = False
        logger.info("faster-whisper model unloaded")

    # -- batch ---------------------------------------------------------------

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if not self.is_loaded():
            logger.error("Model not loaded – call load_model() first")
            return ""
        try:
            segments, _info = self._model.transcribe(
                audio,
                beam_size=5,
            )
            text = " ".join(seg.text.strip() for seg in segments)
            return text.strip()
        except Exception:
            logger.exception("faster-whisper batch transcription failed")
            return ""

    # -- streaming (accumulate + batch) --------------------------------------

    def start_stream(self) -> None:
        if not self.is_loaded():
            logger.error("Model not loaded – call load_model() first")
            return
        self._stream_chunks.clear()
        self._streaming = True
        logger.debug("faster-whisper stream started (accumulate mode)")

    def feed_chunk(self, chunk: np.ndarray) -> None:
        if not self._streaming:
            logger.warning("feed_chunk called outside a streaming session")
            return
        self._stream_chunks.append(chunk)

    def get_partial(self) -> str:
        """faster-whisper does not natively support streaming.

        Returns an empty string; the final result is available after
        ``end_stream()``.
        """
        return ""

    def end_stream(self) -> str:
        if not self._streaming:
            logger.warning("end_stream called outside a streaming session")
            return ""
        self._streaming = False

        if not self._stream_chunks:
            logger.debug("Stream ended with no audio chunks")
            return ""

        combined = np.concatenate(self._stream_chunks)
        self._stream_chunks.clear()

        return self.transcribe(combined, 16000)


# ---------------------------------------------------------------------------
# Deepgram cloud implementation
# ---------------------------------------------------------------------------

class DeepgramSttAdapter(SttAdapter):
    """STT adapter backed by the Deepgram REST/WebSocket API."""

    def __init__(self, api_key: str = "", model: str = "nova-3") -> None:
        self._api_key = api_key
        self._model = model
        self._loaded = False
        self._streaming = False
        self._state_manager = None

        # Real-time WebSocket streaming components
        self._queue = None
        self._loop = None
        self._thread = None
        self._stream_result = ""
        self._stream_error = None
        self._sample_rate = 8000

    def set_state_manager(self, state_manager: Any) -> None:
        self._state_manager = state_manager

    def load_model(self) -> bool:
        """Verify that the API key is present."""
        if not self._api_key:
            import os  # noqa: WPS433
            self._api_key = os.environ.get("DEEPGRAM_API_KEY", "")

        if not self._api_key:
            logger.error("Deepgram API key is missing. Set deepgram_api_key in config or DEEPGRAM_API_KEY env var.")
            return False

        self._loaded = True
        return True

    def is_loaded(self) -> bool:
        return self._loaded

    def transcribe(self, audio: np.ndarray, sample_rate: int) -> str:
        if not self.is_loaded():
            logger.error("Deepgram STT adapter not loaded (missing API key)")
            return ""

        import io  # noqa: WPS433
        import json  # noqa: WPS433
        import urllib.request  # noqa: WPS433
        import wave  # noqa: WPS433

        try:
            # 1. Convert float32 numpy audio to 16-bit PCM WAV in memory
            wav_io = io.BytesIO()
            with wave.open(wav_io, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)  # 16-bit PCM
                wav_file.setframerate(sample_rate)
                # Normalise and convert float32 to int16
                audio_int16 = (audio * 32767).astype(np.int16)
                wav_file.writeframes(audio_int16.tobytes())

            wav_data = wav_io.getvalue()

            # 2. Call Deepgram Listen endpoint (nova-2)
            url = f"https://api.deepgram.com/v1/listen?model={self._model}&smart_format=true"
            req = urllib.request.Request(
                url,
                data=wav_data,
                headers={
                    "Authorization": f"Token {self._api_key}",
                    "Content-Type": "audio/wav",
                },
                method="POST",
            )

            # Standard library HTTP POST request with timeout
            with urllib.request.urlopen(req, timeout=30) as response:
                res_data = response.read().decode("utf-8")
                res_json = json.loads(res_data)

                channels = res_json.get("results", {}).get("channels", [])
                if channels:
                    transcript = channels[0].get("alternatives", [{}])[0].get("transcript", "")
                    return transcript.strip()

            return ""

        except Exception as exc:
            logger.exception("Deepgram Cloud transcription failed: %s", exc)
            return ""

    def start_stream(self) -> None:
        import queue
        import asyncio
        import threading
        from voice_automation.config import load_config

        cfg = load_config()
        self._sample_rate = cfg.sample_rate

        self._queue = queue.Queue()
        self._stream_result = ""
        self._stream_error = None
        self._streaming = True

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_async_loop,
            args=(self._loop,),
            daemon=True,
            name="deepgram-websocket-thread"
        )
        self._thread.start()

    def _run_async_loop(self, loop) -> None:
        import asyncio
        asyncio.set_event_loop(loop)
        loop.run_until_complete(self._websocket_transcribe_loop())
        loop.close()

    async def _websocket_transcribe_loop(self) -> None:
        import websockets
        import json
        import numpy as np
        import asyncio

        url = (
            f"wss://api.deepgram.com/v1/listen"
            f"?model={self._model}"
            f"&encoding=linear16"
            f"&sample_rate={self._sample_rate}"
            f"&smart_format=true"
            f"&interim_results=false"
        )
        headers = {
            "Authorization": f"Token {self._api_key}"
        }

        transcript_parts = []

        try:
            async with websockets.connect(url, additional_headers=headers) as ws:
                async def sender():
                    while True:
                        # Get chunk from the synchronous queue using executor
                        chunk = await self._loop.run_in_executor(None, self._queue.get)
                        if chunk is None:
                            # Send close stream message
                            await ws.send(json.dumps({"type": "CloseStream"}))
                            break

                        # Convert chunk to 16-bit PCM bytes
                        audio_int16 = (chunk * 32767).astype(np.int16)
                        await ws.send(audio_int16.tobytes())
                        await asyncio.sleep(0.001)

                async def receiver():
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            channel = data.get("channel", {})
                            alternatives = channel.get("alternatives", [])
                            if alternatives:
                                text = alternatives[0].get("transcript", "")
                                if text:
                                    transcript_parts.append(text)
                                    if self._state_manager is not None:
                                        self._state_manager.live_transcript = " ".join(transcript_parts)
                        except Exception as e:
                            logger.warning("Error parsing Deepgram streaming response: %s", e)

                await asyncio.gather(sender(), receiver())

        except Exception as exc:
            self._stream_error = exc
            logger.exception("Error in Deepgram WebSocket streaming loop: %s", exc)

        self._stream_result = " ".join(transcript_parts).strip()

    def feed_chunk(self, chunk: np.ndarray) -> None:
        if self._streaming and self._queue is not None:
            self._queue.put(chunk)

    def get_partial(self) -> str:
        # Streaming partial transcripts not supported / not used
        return ""

    def end_stream(self) -> str:
        if not self._streaming:
            return ""
        self._streaming = False

        if self._queue is not None:
            self._queue.put(None)

        if self._thread is not None:
            self._thread.join(timeout=5.0)

        self._queue = None
        self._loop = None
        self._thread = None

        return self._stream_result

    def unload(self) -> None:
        self._loaded = False
        if self._streaming:
            self.end_stream()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[SttAdapter]] = {
    "moonshine": MoonshineSttAdapter,
    "faster-whisper": FasterWhisperSttAdapter,
    "deepgram": DeepgramSttAdapter,
}


def create_stt_adapter(provider: str, **kwargs: Any) -> SttAdapter:
    """Instantiate an :class:`SttAdapter` by provider name.

    Parameters
    ----------
    provider:
        One of ``"moonshine"``, ``"faster-whisper"``, or ``"deepgram"``.
    **kwargs:
        Forwarded to the adapter constructor.

    Raises
    ------
    ValueError
        If *provider* is not recognised.
    """
    cls = _PROVIDERS.get(provider)
    if cls is None:
        available = ", ".join(sorted(_PROVIDERS))
        raise ValueError(
            f"Unknown STT provider {provider!r}. "
            f"Available providers: {available}"
        )
    return cls(**kwargs)
