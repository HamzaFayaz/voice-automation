"""Model downloader for Voice Automation.

Downloads the speech-recognition model used by the configured provider.
Currently supports **moonshine_voice** models.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from voice_automation.config import load_config

logger = logging.getLogger(__name__)

# ANSI helpers
_GREEN = "\033[32m"
_RED = "\033[31m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

MOONSHINE_MODEL_NAMES = {
    0: "Tiny",
    1: "Base",
    2: "Tiny Streaming",
    3: "Base Streaming",
    4: "Small Streaming",
    5: "Medium Streaming",
}


@dataclass(frozen=True)
class ModelDownloadResult:
    """Structured result for CLI and desktop model download flows."""

    success: bool
    message: str
    model_arch: int
    model_name: str | None = None
    model_path: str | None = None
    error: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class ModelDownloadProgress:
    """Progress update for a Moonshine model download."""

    model_arch: int
    model_name: str
    total_files: int
    completed_files: int
    current_file: str = ""
    current_bytes: int = 0
    total_bytes: int = 0
    status: str = ""


class ModelDownloadCancelled(Exception):
    """Raised when the user cancels a model download."""


def _verify_model_path(path_str: str) -> tuple[bool, str]:
    """Return whether a downloaded model path exists and contains files."""
    path = Path(path_str)
    if not path.exists():
        return False, f"Model path does not exist: {path}"

    if path.is_file():
        return True, f"Model file verified at {path}"

    if any(path.iterdir()):
        return True, f"Model files verified in {path}"

    return False, f"Model directory is empty: {path}"


def get_default_moonshine_cache_dir() -> Path:
    """Return Moonshine's default cache directory."""
    try:
        from moonshine_voice.download_file import get_cache_dir  # noqa: WPS433

        return Path(get_cache_dir())
    except Exception:
        import os  # noqa: WPS433

        override = os.environ.get("MOONSHINE_VOICE_CACHE")
        if override:
            return Path(override)
        return Path.home() / "AppData" / "Local" / "moonshine_voice"


def _resolve_cache_dir(cache_dir: str | Path | None = None) -> Path:
    if cache_dir:
        return Path(cache_dir).expanduser()
    return get_default_moonshine_cache_dir()


def get_moonshine_model_path(model_arch: int, cache_dir: str | Path | None = None) -> Path:
    """Return the expected local path for a Moonshine model."""
    from moonshine_voice import ModelArch  # noqa: WPS433
    from moonshine_voice.download import find_model_info  # noqa: WPS433

    model_info = find_model_info("en", ModelArch(model_arch))
    model_folder_name = model_info["download_url"].replace("https://", "")
    return _resolve_cache_dir(cache_dir) / model_folder_name


def is_moonshine_model_downloaded(
    model_arch: int,
    cache_dir: str | Path | None = None,
) -> tuple[bool, str]:
    """Return whether the selected Moonshine model is installed."""
    try:
        from moonshine_voice import ModelArch  # noqa: WPS433
        from moonshine_voice.download import (  # noqa: WPS433
            find_model_info,
            get_components_for_model_info,
        )

        model_info = find_model_info("en", ModelArch(model_arch))
        model_path = get_moonshine_model_path(model_arch, cache_dir)
        components = get_components_for_model_info(model_info)
        missing = [component for component in components if not (model_path / component).exists()]
        if missing:
            return False, f"Missing {len(missing)} file(s) in {model_path}"
        return True, f"Model installed at {model_path}"
    except Exception as exc:
        return False, f"Could not check model files: {exc}"


def _raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ModelDownloadCancelled()


def _download_file_cancellable(
    url: str,
    destination: Path,
    *,
    cancel_event: Event | None,
    progress_callback: Callable[[ModelDownloadProgress], None] | None,
    base_progress: ModelDownloadProgress,
    timeout: int = 30,
) -> Path:
    """Download one file to a partial path, renaming only after completion."""
    import requests  # noqa: WPS433
    from filelock import FileLock  # noqa: WPS433

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_file = destination.with_suffix(destination.suffix + ".partial")
    lock_file = destination.with_suffix(destination.suffix + ".lock")

    with FileLock(lock_file):
        _raise_if_cancelled(cancel_event)
        if destination.exists():
            if progress_callback is not None:
                progress_callback(
                    ModelDownloadProgress(
                        model_arch=base_progress.model_arch,
                        model_name=base_progress.model_name,
                        total_files=base_progress.total_files,
                        completed_files=base_progress.completed_files + 1,
                        current_file=destination.name,
                        status=f"Already downloaded {destination.name}",
                    )
                )
            return destination

        initial_size = temp_file.stat().st_size if temp_file.exists() else 0
        headers = {"Range": f"bytes={initial_size}-"} if initial_size else {}

        response = requests.get(url, headers=headers, stream=True, timeout=timeout)
        if response.status_code == 416:
            temp_file.unlink(missing_ok=True)
            initial_size = 0
            response = requests.get(url, stream=True, timeout=timeout)

        response.raise_for_status()

        if response.status_code == 206:
            content_range = response.headers.get("Content-Range", "")
            total_size = (
                int(content_range.split("/")[-1])
                if "/" in content_range
                else initial_size + int(response.headers.get("Content-Length", 0))
            )
        else:
            total_size = int(response.headers.get("Content-Length", 0))
            initial_size = 0
            temp_file.unlink(missing_ok=True)

        bytes_downloaded = initial_size
        mode = "ab" if initial_size else "wb"

        try:
            with temp_file.open(mode) as file:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    _raise_if_cancelled(cancel_event)
                    if not chunk:
                        continue
                    file.write(chunk)
                    bytes_downloaded += len(chunk)
                    if progress_callback is not None:
                        progress_callback(
                            ModelDownloadProgress(
                                model_arch=base_progress.model_arch,
                                model_name=base_progress.model_name,
                                total_files=base_progress.total_files,
                                completed_files=base_progress.completed_files,
                                current_file=destination.name,
                                current_bytes=bytes_downloaded,
                                total_bytes=total_size,
                                status=f"Downloading {destination.name}",
                            )
                        )
        except ModelDownloadCancelled:
            temp_file.unlink(missing_ok=True)
            raise

        _raise_if_cancelled(cancel_event)
        temp_file.replace(destination)
        return destination


def download_moonshine_model(
    model_arch: int,
    *,
    cache_dir: str | Path | None = None,
    cancel_event: Event | None = None,
    progress_callback: Callable[[ModelDownloadProgress], None] | None = None,
) -> ModelDownloadResult:
    """Download and verify the selected Moonshine model.

    This function does not print so it can be called from UI worker threads.
    Use ``download()`` for the stdout-friendly CLI wrapper.
    """
    start = time.perf_counter()
    model_name = MOONSHINE_MODEL_NAMES.get(model_arch)

    try:
        from moonshine_voice import ModelArch  # noqa: WPS433
        from moonshine_voice.download import (  # noqa: WPS433
            download_spelling_model_for_language,
            find_model_info,
            get_components_for_model_info,
        )

        _raise_if_cancelled(cancel_event)
        arch_enum = ModelArch(model_arch)
        model_info = find_model_info("en", arch_enum)
        resolved_name = model_name or model_info.get("model_name")
        components = get_components_for_model_info(model_info)
        resolved_cache_dir = _resolve_cache_dir(cache_dir)
        model_download_url = model_info["download_url"]
        model_folder_name = model_download_url.replace("https://", "")
        root_model_path = resolved_cache_dir / model_folder_name

        total_files = len(components)
        if progress_callback is not None:
            progress_callback(
                ModelDownloadProgress(
                    model_arch=model_arch,
                    model_name=resolved_name or f"Model {model_arch}",
                    total_files=total_files,
                    completed_files=0,
                    status="Starting download",
                )
            )

        completed_files = 0
        for component in components:
            _raise_if_cancelled(cancel_event)
            component_url = f"{model_download_url}/{component}"
            component_path = root_model_path / component
            progress = ModelDownloadProgress(
                model_arch=model_arch,
                model_name=resolved_name or f"Model {model_arch}",
                total_files=total_files,
                completed_files=completed_files,
                current_file=component,
                status=f"Downloading {component}",
            )
            _download_file_cancellable(
                component_url,
                component_path,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
                base_progress=progress,
            )
            completed_files += 1
            if progress_callback is not None:
                progress_callback(
                    ModelDownloadProgress(
                        model_arch=model_arch,
                        model_name=resolved_name or f"Model {model_arch}",
                        total_files=total_files,
                        completed_files=completed_files,
                        current_file=component,
                        status=f"Downloaded {component}",
                    )
                )

        # Match moonshine_voice's best-effort spelling model prefetch. It is
        # optional, so failures should not fail the selected model download.
        try:
            _raise_if_cancelled(cancel_event)
            download_spelling_model_for_language(
                model_info["language"],
                cache_root=resolved_cache_dir,
            )
        except ModelDownloadCancelled:
            raise
        except Exception as exc:
            logger.warning("Optional spelling model download failed: %s", exc)

        elapsed = time.perf_counter() - start
        path_str = str(root_model_path)
        verified, verification_message = is_moonshine_model_downloaded(
            model_arch,
            resolved_cache_dir,
        )

        if not verified:
            return ModelDownloadResult(
                success=False,
                message=verification_message,
                model_arch=model_arch,
                model_name=resolved_name,
                model_path=path_str,
                error=verification_message,
            )

        return ModelDownloadResult(
            success=True,
            message=f"Download complete in {elapsed:.1f}s. {verification_message}",
            model_arch=model_arch,
            model_name=resolved_name,
            model_path=path_str,
        )
    except ModelDownloadCancelled:
        message = f"Download cancelled for {model_name or f'Model {model_arch}'}."
        return ModelDownloadResult(
            success=False,
            message=message,
            model_arch=model_arch,
            model_name=model_name,
            error=message,
            cancelled=True,
        )
    except ValueError as exc:
        message = f"Unsupported Moonshine model architecture: {model_arch}"
        logger.warning("%s: %s", message, exc)
        return ModelDownloadResult(
            success=False,
            message=message,
            model_arch=model_arch,
            model_name=model_name,
            error=str(exc),
        )
    except Exception as exc:
        logger.exception("Model download failed")
        return ModelDownloadResult(
            success=False,
            message=f"Download failed: {exc}",
            model_arch=model_arch,
            model_name=model_name,
            error=str(exc),
        )


def download() -> None:
    """Download and verify the model specified in the current config.

    Exits with code **1** on failure.
    """
    cfg = load_config()

    if cfg.model_provider != "moonshine":
        print(f"{_RED}Unsupported model provider: {cfg.model_provider}{_RESET}")
        sys.exit(1)

    print(f"{_BOLD}Downloading Moonshine model (arch={cfg.model_arch}) ...{_RESET}")
    result = download_moonshine_model(cfg.model_arch)

    if not result.success:
        print(f"{_RED}{_BOLD}{result.message}{_RESET}")
        sys.exit(1)

    print(f"{_GREEN}{_BOLD}{result.message}{_RESET}")
    if result.model_name:
        print(f"Model architecture: {result.model_name}")
    if result.model_path:
        print(f"Model path: {result.model_path}")


if __name__ == "__main__":
    download()
