"""Environment checker – validates that all runtime dependencies are usable."""

from __future__ import annotations

import sys

# ── ANSI helpers ──────────────────────────────────────────────────────────────

_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_PASS = f"  {_GREEN}✔ PASS{_RESET}"
_FAIL = f"  {_RED}✘ FAIL{_RESET}"
_WARN = f"  {_YELLOW}⚠ WARN{_RESET}"


def _header(title: str) -> None:
    print(f"\n{_BOLD}-- {title} --{_RESET}")


def _check_python_version() -> bool:
    """Ensure Python ≥ 3.11."""
    _header("Python version")
    ver = sys.version_info
    ok = ver >= (3, 11)
    tag = _PASS if ok else _FAIL
    print(f"{tag}  Python {ver.major}.{ver.minor}.{ver.micro}")
    return ok


def _check_sounddevice() -> bool:
    """Import sounddevice and query the default input device."""
    _header("sounddevice + microphone")
    try:
        import sounddevice as sd  # noqa: WPS433

        dev = sd.query_devices(kind="input")
        print(f"{_PASS}  Default input device: {dev['name']}")  # type: ignore[index]
        return True
    except Exception as exc:
        print(f"{_FAIL}  {exc}")
        return False


def _check_pynput() -> bool:
    """Import pynput.keyboard."""
    _header("pynput (keyboard listener)")
    try:
        from pynput import keyboard as _  # noqa: F401, WPS433

        print(f"{_PASS}  pynput.keyboard imported")
        return True
    except Exception as exc:
        print(f"{_FAIL}  {exc}")
        return False


def _check_pyperclip() -> bool:
    """Test clipboard read/write via pyperclip."""
    _header("pyperclip (clipboard)")
    try:
        import pyperclip  # noqa: WPS433

        # Attempt a harmless paste to verify clipboard access.
        pyperclip.paste()
        print(f"{_PASS}  Clipboard accessible")
        return True
    except Exception as exc:
        print(f"{_FAIL}  {exc}")
        return False


def _check_moonshine() -> bool:
    """Import moonshine_voice."""
    _header("moonshine_voice")
    try:
        import moonshine_voice as _  # noqa: F401, WPS433

        print(f"{_PASS}  moonshine_voice imported")
        return True
    except Exception as exc:
        print(f"{_FAIL}  {exc}")
        return False


def _check_model_files(cfg) -> bool:
    """Check whether the configured Moonshine model files are present."""
    _header("Model files")
    try:
        from voice_automation.downloader import is_moonshine_model_downloaded

        installed, message = is_moonshine_model_downloaded(
            cfg.model_arch,
            cfg.get_moonshine_cache_dir(),
        )
        if installed:
            print(f"{_PASS}  {message}")
            return True

        print(f"{_FAIL}  {message}")
        print("       Download the selected Moonshine model from Settings.")
        return False
    except Exception as exc:
        print(f"{_WARN}  Could not verify model files: {exc}")
        return True


def _check_deepgram(cfg) -> bool:
    """Ensure Deepgram API key is configured."""
    _header("Deepgram Cloud API")
    import os
    api_key = cfg.deepgram_api_key or os.environ.get("DEEPGRAM_API_KEY", "")
    if api_key:
        masked = api_key[:4] + "..." + api_key[-4:] if len(api_key) > 8 else "..."
        print(f"{_PASS}  Deepgram API key configured ({masked})")
        return True
    else:
        print(f"{_FAIL}  Deepgram API key is missing. Set it in .env or voice_automation_config.json")
        return False


# ── Public API ────────────────────────────────────────────────────────────────


def run_checks(cfg=None) -> bool:
    """Execute all environment checks and print a coloured report.

    Returns
    -------
    bool
        ``True`` if every *required* check passed, ``False`` otherwise.
    """
    print(f"\n{_BOLD}Voice Automation – Environment Check{_RESET}")
    print("=" * 42)

    if cfg is None:
        from voice_automation.config import load_config

        cfg = load_config()

    results: list[bool] = [
        _check_python_version(),
        _check_sounddevice(),
        _check_pynput(),
        _check_pyperclip(),
    ]

    provider = cfg.model_provider
    if provider == "deepgram":
        results.append(_check_deepgram(cfg))
    else:  # moonshine
        results.append(_check_moonshine())
        results.append(_check_model_files(cfg))

    all_passed = all(results)
    passed_count = sum(results)
    total = len(results)

    print("\n" + "=" * 42)
    if all_passed:
        print(f"{_GREEN}{_BOLD}All {total} checks passed ✔{_RESET}")
    else:
        print(
            f"{_RED}{_BOLD}{total - passed_count}/{total} check(s) failed ✘{_RESET}"
        )
    print()

    return all_passed


if __name__ == "__main__":
    passed = run_checks()
    sys.exit(0 if passed else 1)
