"""Configuration loading, persistence, and secret storage."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "voice_automation_config.json"
_APP_NAME = "VoiceAutomation"
_KEYRING_SERVICE = "voice-automation"
_DEEPGRAM_KEY_NAME = "deepgram_api_key"

VALID_MODEL_PROVIDERS = {"moonshine", "deepgram"}
VALID_MOONSHINE_ARCHES = {0, 1, 2, 3, 4, 5}
VALID_PASTE_MODES = {"clipboard", "type"}
VALID_HOTKEYS = {
    "right_ctrl",
    "left_ctrl",
    "right_alt",
    "left_alt",
    "right_shift",
    *{f"f{i}" for i in range(1, 13)},
}


@dataclasses.dataclass
class Config:
    """Runtime configuration for Voice Automation."""

    hotkey: str = "right_ctrl"
    language: str = "en"

    model_provider: str = "moonshine"
    model_arch: int = 4
    moonshine_cache_dir: str = ""
    moonshine_model_dirs: dict[str, str] = dataclasses.field(default_factory=dict)
    deepgram_api_key: str = ""

    paste_mode: str = "type"
    trailing_space: bool = True
    clipboard_restore_delay: float = 0.15

    max_record_seconds: int = 300
    min_record_seconds: float = 0.3
    sample_rate: int = 16_000
    chunk_ms: int = 100

    sound_cues: bool = False
    replacements: dict[str, str] = dataclasses.field(
        default_factory=lambda: {
            "jason": "JSON",
            "python": "Python",
            "cursor": "Cursor",
        }
    )

    def get_moonshine_cache_dir(self) -> str:
        """Return the configured cache directory for the selected Moonshine model."""
        return self.moonshine_model_dirs.get(str(self.model_arch), self.moonshine_cache_dir)


def get_project_config_path() -> Path:
    """Return the project-local configuration path used by the CLI."""
    return Path.cwd() / _CONFIG_FILENAME


def get_app_config_path() -> Path:
    """Return the per-user desktop configuration path."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / _APP_NAME / _CONFIG_FILENAME


def _config_path(use_app_data: bool = False) -> Path:
    return get_app_config_path() if use_app_data else get_project_config_path()


def _write_default_config(path: Path) -> None:
    defaults = dataclasses.asdict(Config())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(defaults, indent=4) + "\n", encoding="utf-8")
    logger.info("Created default config -> %s", path)


def _merge(defaults: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = dict(defaults)
    for key, value in overrides.items():
        if key in merged:
            merged[key] = value
        else:
            logger.warning("Ignoring unknown config key: %r", key)
    return merged


def load_dotenv_file() -> None:
    """Load variables from .env in the current working directory."""
    dotenv_path = Path.cwd() / ".env"
    if not dotenv_path.exists():
        return

    try:
        with dotenv_path.open("r", encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip()
                if val.startswith(('"', "'")) and val.endswith(val[0]):
                    val = val[1:-1]
                os.environ[key] = val
        logger.info("Loaded environment variables from %s", dotenv_path)
    except Exception as exc:
        logger.warning("Failed to load %s: %s", dotenv_path, exc)


def _get_env_deepgram_api_key() -> str:
    return os.environ.get("DEEPGRAM_API_KEY", "")


def get_deepgram_api_key() -> str:
    """Return the Deepgram API key from the OS credential store."""
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception as exc:
        logger.debug("keyring is unavailable: %s", exc)
        return ""

    try:
        return keyring.get_password(_KEYRING_SERVICE, _DEEPGRAM_KEY_NAME) or ""
    except Exception as exc:
        logger.warning("Could not read Deepgram API key from keyring: %s", exc)
        return ""


def set_deepgram_api_key(value: str) -> None:
    """Store or clear the Deepgram API key in the OS credential store."""
    try:
        import keyring  # type: ignore[import-not-found]
    except Exception as exc:
        raise RuntimeError("keyring is required to store API keys") from exc

    try:
        if value:
            keyring.set_password(_KEYRING_SERVICE, _DEEPGRAM_KEY_NAME, value)
            return

        try:
            keyring.delete_password(_KEYRING_SERVICE, _DEEPGRAM_KEY_NAME)
        except keyring.errors.PasswordDeleteError:
            pass
    except Exception as exc:
        raise RuntimeError(f"Could not update Deepgram API key: {exc}") from exc


def load_config(use_app_data: bool = False) -> Config:
    """Load configuration from disk, creating a default file if necessary."""
    load_dotenv_file()
    path = _config_path(use_app_data=use_app_data)

    if not path.exists():
        _write_default_config(path)
        cfg = Config()
        cfg.deepgram_api_key = _get_env_deepgram_api_key() or get_deepgram_api_key()
        return cfg

    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read %s; falling back to defaults: %s", path, exc)
        cfg = Config()
        cfg.deepgram_api_key = _get_env_deepgram_api_key() or get_deepgram_api_key()
        return cfg

    merged = _merge(dataclasses.asdict(Config()), raw)
    if not merged.get("deepgram_api_key"):
        merged["deepgram_api_key"] = (
            _get_env_deepgram_api_key() or get_deepgram_api_key()
        )

    return Config(**merged)


def save_config(
    config: Config,
    use_app_data: bool = False,
    include_secrets: bool = False,
) -> Path:
    """Persist configuration to JSON and return the written path."""
    path = _config_path(use_app_data=use_app_data)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dataclasses.asdict(config)
    if not include_secrets:
        data["deepgram_api_key"] = ""
    path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
    logger.info("Saved config -> %s", path)
    return path


def validate_config(config: Config, require_deepgram_key: bool = False) -> list[str]:
    """Return validation error messages for desktop-friendly settings."""
    errors: list[str] = []

    if config.model_provider not in VALID_MODEL_PROVIDERS:
        providers = ", ".join(sorted(VALID_MODEL_PROVIDERS))
        errors.append(f"model_provider must be one of: {providers}")
    if config.hotkey not in VALID_HOTKEYS:
        errors.append("hotkey is not supported")
    if config.sample_rate <= 0:
        errors.append("sample_rate must be a positive integer")
    if config.model_arch not in VALID_MOONSHINE_ARCHES:
        errors.append("model_arch must be between 0 and 5")
    if config.paste_mode not in VALID_PASTE_MODES:
        modes = ", ".join(sorted(VALID_PASTE_MODES))
        errors.append(f"paste_mode must be one of {modes}")
    if (
        require_deepgram_key
        and config.model_provider == "deepgram"
        and not (
            config.deepgram_api_key
            or _get_env_deepgram_api_key()
            or get_deepgram_api_key()
        )
    ):
        errors.append("Deepgram API key is required for the Deepgram backend")

    return errors
