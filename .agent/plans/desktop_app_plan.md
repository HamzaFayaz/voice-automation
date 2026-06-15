# Desktop App Plan: PySide6 + PyInstaller

## Summary

Build the desktop application on a new branch named `desktop-app`. Keep the current Python package as the voice automation engine, then add a PySide6 desktop layer with a system tray, settings window, backend selection, secure Deepgram API key storage, Moonshine model selection/download, and PyInstaller packaging.

Default decisions:

- UI framework: `PySide6`
- Packaging: `PyInstaller`
- Branch: `desktop-app`
- API key storage: OS credential store through `keyring`
- V1 app shape: tray app + settings window, not a large dashboard

## Parallel Work Plan

### 0. Branch Setup

Owner: Git/coordination agent

- Create and switch to branch:
  ```bash
  git checkout -b desktop-app
  ```
- Keep all desktop work off `main`.
- Commit by feature area:
  - engine refactor
  - config/keyring
  - PySide6 UI
  - model manager
  - packaging
  - docs

### 1. Core Engine Refactor

Owner: backend agent
Can run in parallel after branch creation.

- Extract the blocking orchestration logic into a reusable service class, for example `VoiceAutomationService`.
- Required service interface:
  - `start() -> None`
  - `stop() -> None`
  - `restart(config: Config) -> None`
  - `is_running -> bool`
  - expose state updates through callbacks/signals
- Keep existing CLI behavior working:
  - `python -m voice_automation run`
  - `python -m voice_automation check`
  - `python -m voice_automation download-model`
- The CLI should call the same service used by the desktop app.
- Preserve current hotkey, audio capture, STT adapter, cleanup, paste, and overlay behavior.

### 2. Config And Secret Storage

Owner: config/security agent
Can run in parallel with engine refactor.

- Add a config save/update path, not just `load_config()`.
- Add desktop-friendly config persistence under user app data:
  ```text
  %APPDATA%\VoiceAutomation\voice_automation_config.json
  ```
- Keep compatibility with the current root `voice_automation_config.json` for CLI/dev usage.
- Add Deepgram key storage with `keyring`:
  - service name: `voice-automation`
  - key name/user: `deepgram_api_key`
- Desktop app stores the Deepgram key in keyring, not JSON.
- Runtime config should still support `.env` and JSON keys for CLI compatibility.
- Add validation helpers for:
  - backend provider
  - hotkey
  - sample rate
  - Moonshine architecture
  - empty/missing Deepgram key

### 3. PySide6 Tray And Settings UI

Owner: desktop UI agent
Depends on basic service/config interfaces.

- Add a PySide6 desktop entrypoint, for example:
  ```bash
  python -m voice_automation.desktop
  ```
- Add console script:
  ```text
  voice-automation-desktop
  ```
- Build a system tray app with menu items:
  - Start Dictation
  - Stop Dictation
  - Settings
  - Check Environment
  - Quit
- Build a settings window with:
  - backend selector: `Deepgram API`, `Moonshine Local`
  - Deepgram API key field with save/test button
  - Moonshine model dropdown:
    - Tiny `0`
    - Base `1`
    - Tiny Streaming `2`
    - Base Streaming `3`
    - Small Streaming `4`
    - Medium Streaming `5`
  - Moonshine Download Model button
  - hotkey selector
  - paste mode selector
  - sample rate field or backend-aware default display
  - save/apply button
- UI should show current status:
  - stopped
  - starting
  - running
  - recording
  - transcribing
  - pasting
  - error

### 4. Model Manager

Owner: model/download agent
Can run in parallel with UI after config contract is known.

- Refactor Moonshine download logic into a reusable function callable from both CLI and UI.
- Required behavior:
  - accept selected `model_arch`
  - download selected Moonshine model
  - verify local model files exist
  - return structured success/error result
- UI download button should run in a background worker thread so the app does not freeze.
- Show download result in the settings window.
- Deepgram mode should clearly show that no local model download is required.

### 5. Packaging

Owner: packaging agent
Depends on UI entrypoint existing.

- Add dependencies:
  - `PySide6`
  - `keyring`
- Add packaging config for PyInstaller.
- Build target:
  - Windows desktop executable
  - prefer one-folder distribution for V1 reliability
- Include:
  - app icon
  - required package data
  - hidden imports needed by PySide6, sounddevice, pynput, websockets, keyring, Moonshine
- Output should land under ignored build folders:
  ```text
  build/
  dist/
  ```
- Add a build command or script, for example:
  ```bat
  pyinstaller voice_automation_desktop.spec
  ```

### 6. Docs And Release Prep

Owner: docs agent
Can run after UI decisions are stable.

- Update README with a Desktop App section:
  - how to run from source
  - how to configure Deepgram
  - how to configure Moonshine
  - how to download a Moonshine model from the UI
  - how to build the executable
- Add screenshots later if available.
- Update release notes for desktop app branch when ready.

## Public Interfaces And Files To Add

- New desktop entrypoint:
  ```text
  voice-automation-desktop
  ```
- New reusable service API:
  ```python
  service = VoiceAutomationService(config)
  service.start()
  service.stop()
  service.restart(config)
  ```
- New config save/keyring helpers:
  ```python
  load_config()
  save_config(config)
  get_deepgram_api_key()
  set_deepgram_api_key(value)
  ```
- New model manager helper:
  ```python
  download_moonshine_model(model_arch: int) -> result
  ```
- Keep existing CLI commands working unchanged.

## Test Plan

- CLI regression:
  - `python -m voice_automation check`
  - `python -m voice_automation run`
  - `python -m voice_automation download-model`
- Config tests:
  - save/load config
  - invalid provider rejected
  - Deepgram key stored in keyring, not JSON
  - `.env`/JSON compatibility still works
- UI manual tests:
  - tray icon appears
  - settings window opens
  - Deepgram key can be saved and detected
  - backend can switch between Deepgram and Moonshine
  - Moonshine architecture selection saves correctly
  - model download runs without freezing UI
  - Start/Stop controls work
- End-to-end manual tests:
  - Deepgram mode dictates into Notepad/Cursor/browser
  - Moonshine mode dictates after model download
  - app exits cleanly from tray
- Packaging tests:
  - PyInstaller build completes
  - packaged app launches on Windows
  - packaged app can read/write config
  - packaged app can access keyring
  - packaged app can use microphone and hotkey

## Assumptions

- V1 is Windows-first.
- V1 desktop app is tray-first with a settings window.
- The current CLI remains supported for developer use.
- Deepgram is the recommended default for accuracy.
- Moonshine Medium Streaming `model_arch: 5` is the recommended local option.
- API keys must not be written to committed config files.
- PyInstaller one-folder output is preferred for the first desktop release; one-file packaging can be added later.
