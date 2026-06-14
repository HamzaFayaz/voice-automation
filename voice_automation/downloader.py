"""Model downloader for Voice Automation.

Downloads the speech-recognition model used by the configured provider.
Currently supports **moonshine_voice** models.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path

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


def download_moonshine_model(model_arch: int) -> ModelDownloadResult:
    """Download and verify the selected Moonshine model.

    This function does not print so it can be called from UI worker threads.
    Use ``download()`` for the stdout-friendly CLI wrapper.
    """
    start = time.perf_counter()
    model_name = MOONSHINE_MODEL_NAMES.get(model_arch)

    try:
        from moonshine_voice import ModelArch, get_model_for_language  # noqa: WPS433

        arch_enum = ModelArch(model_arch)
        path_str, arch = get_model_for_language(wanted_model_arch=arch_enum)
        elapsed = time.perf_counter() - start
        resolved_name = model_name or getattr(arch, "name", None)
        verified, verification_message = _verify_model_path(path_str)

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
