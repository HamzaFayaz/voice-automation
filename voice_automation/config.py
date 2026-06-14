"""Configuration loader for Voice Automation.

Reads settings from ``voice_automation_config.json`` in the current working
directory. If the file does not exist a default copy is written automatically.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "voice_automation_config.json"


@dataclasses.dataclass
class Config:
    """Runtime configuration for Voice Automation."""

    # ── Hotkey & language ─────────────────────────────────────────────────
    hotkey: str = "right_ctrl"
    language: str = "en"

    # ── Model ─────────────────────────────────────────────────────────────
    model_provider: str = "moonshine"
    model_arch: int = 4
    model_size: str = "base"
    deepgram_api_key: str = ""

    # ── Paste behaviour ───────────────────────────────────────────────────
    paste_mode: str = "clipboard"
    trailing_space: bool = True
    clipboard_restore_delay: float = 0.15

    # ── Recording ─────────────────────────────────────────────────────────
    max_record_seconds: int = 30
    min_record_seconds: float = 0.3
    sample_rate: int = 16_000
    chunk_ms: int = 100

    # ── Advanced Dictation Features ───────────────────────────────────────
    sound_cues: bool = False
    replacements: dict[str, str] = dataclasses.field(
        default_factory=lambda: {
            "jason": "JSON",
            "python": "Python",
            "cursor": "Cursor",
        }
    )


def _config_path() -> Path:
    """Return the resolved path to the configuration file."""
    return Path.cwd() / _CONFIG_FILENAME


def _write_default_config(path: Path) -> None:
    """Write a fresh default config file to *path*."""
    defaults = dataclasses.asdict(Config())
    path.write_text(json.dumps(defaults, indent=4) + "\n", encoding="utf-8")
    logger.info("Created default config → %s", path)


def _merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Return *defaults* updated with known keys from *overrides*."""
    merged = dict(defaults)
    for key, value in overrides.items():
        if key in merged:
            merged[key] = value
        else:
            logger.warning("Ignoring unknown config key: %r", key)
    return merged


def load_dotenv_file() -> None:
    """Load variables from .env file in current working directory into os.environ."""
    import os
    dotenv_path = Path.cwd() / ".env"
    if dotenv_path.exists():
        try:
            with open(dotenv_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip()
                        # Remove quotes if present
                        if val.startswith(('"', "'")) and val.endswith(val[0]):
                            val = val[1:-1]
                        os.environ[key] = val
            logger.info("Loaded environment variables from %s", dotenv_path)
        except Exception as exc:
            logger.warning("Failed to load %s: %s", dotenv_path, exc)


def load_config() -> Config:
    """Load configuration from disk, creating a default file if necessary.

    Returns
    -------
    Config
        A populated :class:`Config` dataclass instance.
    """
    load_dotenv_file()
    path = _config_path()

    if not path.exists():
        _write_default_config(path)
        return Config()

    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read %s – falling back to defaults: %s", path, exc)
        return Config()

    defaults = dataclasses.asdict(Config())
    merged = _merge(defaults, raw)

    return Config(**merged)
