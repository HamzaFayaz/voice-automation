# Release Notes

## Unreleased - Desktop App Branch

This branch adds the V1 desktop app path for Voice Automation. The desktop app is Windows-first and tray-first, with a system tray menu and settings window instead of a large dashboard.

### Desktop App

- Added a PySide6 desktop entrypoint for running from source with `python -m voice_automation.desktop`.
- Added the `voice-automation-desktop` console script path for installed environments.
- Added a tray workflow with Start Dictation, Stop Dictation, Settings, Check Environment, and Quit.
- Added a settings flow for switching between Deepgram API and Moonshine Local backends.
- Added keyring-backed Deepgram API key storage for desktop settings, keeping the key out of JSON config files.
- Added Moonshine model selection in the desktop UI, including Tiny, Base, Tiny Streaming, Base Streaming, Small Streaming, and Medium Streaming.
- Added a UI-driven Moonshine model download flow so local models can be installed without using the CLI.
- Added PyInstaller packaging support for a Windows one-folder desktop build.

### Usage Notes

- Run from source with `python -m voice_automation.desktop`.
- Run the installed console script with `voice-automation-desktop`.
- Build the executable with `pyinstaller voice_automation_desktop.spec` or the provided desktop build script.
- Deepgram remains the recommended accuracy-focused backend.
- Moonshine Medium Streaming `model_arch: 5` remains the recommended local backend option.

## v0.1.0 - Initial Public Release

Voice Automation v0.1.0 is the first public release of the Windows push-to-talk dictation and productivity automation tool.

This release focuses on the core workflow: hold a hotkey, speak, release, transcribe, clean the text, and paste it into the currently active application.

### Features

- Push-to-talk voice input with a global Windows hotkey.
- Microphone recording only while the configured hotkey is held.
- Deepgram cloud transcription mode for higher-accuracy speech-to-text.
- Moonshine local transcription mode for offline, CPU-friendly usage.
- Shared speech-to-text adapter interface for switching providers through configuration.
- Clipboard-based text insertion into the active application.
- Direct typing fallback for applications where clipboard paste is not suitable.
- Lightweight transcript cleanup:
  - whitespace normalization
  - duplicate word cleanup
  - first-letter capitalization
  - configurable trailing space
  - custom word replacements
- Terminal command helper that can press Enter when dictated text ends with "run" or "execute".
- Tkinter overlay HUD for recording, transcribing, and paste feedback.
- Environment check command for validating Python, microphone, clipboard, hotkey, and selected STT backend.
- Moonshine model download command for local model setup.
- JSON configuration file for hotkey, backend provider, audio settings, paste behavior, and replacements.
- `.env` support for keeping Deepgram API keys outside committed config.
- Windows launch scripts for setup, foreground run, and minimized background run.

### Supported Backends

- Deepgram API
- Moonshine local models
- faster-whisper adapter path is included as an experimental fallback

### Current Default Mode

The default checked-in configuration uses Deepgram:

```json
{
    "model_provider": "deepgram",
    "sample_rate": 8000
}
```

Moonshine can be enabled by switching:

```json
{
    "model_provider": "moonshine",
    "model_arch": 5,
    "sample_rate": 16000
}
```

### Known Limitations

- Windows is the primary supported platform.
- Deepgram mode requires an API key and internet access.
- Moonshine accuracy depends on the selected local model.
- `download-model` currently applies to Moonshine only.
- There is no packaged installer yet.
- Automated test coverage is not included in this release.

### Recommended Next Improvements

- Add a tray icon and pause/resume controls.
- Add provider-specific setup commands.
- Add automated tests around transcript cleanup, config loading, and STT adapter behavior.
- Package the app as a standalone Windows executable.
- Improve handling for provider-specific sample rates and model setup.
