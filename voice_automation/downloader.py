"""Model downloader for Voice Automation.

Downloads the speech-recognition model used by the configured provider.
Currently supports **moonshine_voice** models.
"""

from __future__ import annotations

import logging
import sys
import time

from voice_automation.config import load_config

logger = logging.getLogger(__name__)

# ── ANSI helpers ──────────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_RED = "\033[31m"
_BOLD = "\033[1m"
_RESET = "\033[0m"


def _download_moonshine(model_arch: int) -> bool:
    """Download a Moonshine model of the given architecture size.

    Returns ``True`` on success, ``False`` otherwise.
    """
    print(f"{_BOLD}Downloading Moonshine model (arch={model_arch}) …{_RESET}")
    start = time.perf_counter()

    try:
        from moonshine_voice import ModelArch, get_model_for_language  # noqa: WPS433

        arch_enum = ModelArch(model_arch)
        path_str, arch = get_model_for_language(wanted_model_arch=arch_enum)
        elapsed = time.perf_counter() - start
        print(f"{_GREEN}{_BOLD}✔ Download complete in {elapsed:.1f}s{_RESET}")
        print(f"Model architecture: {arch.name}")
        print(f"Model path: {path_str}")
        return True

    except Exception as exc:
        logger.exception("Model download failed")
        print(f"{_RED}{_BOLD}✘ Download failed: {exc}{_RESET}")
        return False


def _verify_model(model_arch: int) -> bool:
    """Quick sanity-check that model files exist after download."""
    try:
        from moonshine_voice import ModelArch, get_model_for_language  # noqa: WPS433
        from pathlib import Path

        arch_enum = ModelArch(model_arch)
        path_str, _ = get_model_for_language(wanted_model_arch=arch_enum)
        path = Path(path_str)
        if path.exists() and any(path.iterdir()):
            print(f"{_GREEN}✔ Model files verified in {path}{_RESET}")
            return True

        print(f"{_RED}✘ Model directory is empty: {path}{_RESET}")
        return False
    except Exception as exc:
        logger.warning("Verification step failed: %s", exc)
        return False


def download() -> None:
    """Download and verify the model specified in the current config.

    Exits with code **1** on failure.
    """
    cfg = load_config()

    if cfg.model_provider != "moonshine":
        print(f"{_RED}Unsupported model provider: {cfg.model_provider}{_RESET}")
        sys.exit(1)

    success = _download_moonshine(cfg.model_arch)
    if not success:
        sys.exit(1)

    _verify_model(cfg.model_arch)


if __name__ == "__main__":
    download()
