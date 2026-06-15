# Desktop Production UI Plan

## Summary

Redesign the PySide6 desktop UI as a native, professional Windows utility app. Keep the Home screen simple and focused, but rebuild Settings into a structured setup and configuration surface.

Local research confirms the app is using PySide6/Qt 6.11.1. The current package supports the widgets needed for this redesign without changing frameworks: `QStackedWidget`, `QScrollArea`, `QGroupBox`, `QFrame`, and `QButtonGroup`.

## Key UI Changes

- Keep Home focused:
  - Status: Stopped, Running, Recording, Transcribing, or Error.
  - Backend summary: Deepgram Online or Moonshine Offline.
  - Setup-required banner when the selected backend is incomplete.
  - Primary Start and Stop buttons.
  - Secondary Settings, Check Environment, and Diagnostics actions.
- Redesign Settings as a scrollable production screen:
  - Use `QScrollArea` so smaller screens do not clip fields.
  - Replace the current flat `QFormLayout` with grouped sections.
  - Use consistent margins, spacing, status colors, and button hierarchy.
- Backend section:
  - Use a two-option selector backed by `QButtonGroup`.
  - Options: Deepgram Online and Moonshine Offline.
  - Show a readiness badge: Ready, Needs API Key, Needs Download, or Error.
  - Show only the selected backend setup panel with `QStackedWidget`.
- Deepgram setup panel:
  - Show saved or missing key state.
  - Actions: Save Key, Change Key, Test Backend.
  - Keep key hidden after saving.
  - Do not expose raw config fields in this section.
- Moonshine setup panel:
  - Show model selector.
  - Show model readiness and storage path.
  - Actions: Download Model, Cancel Download, Use Default Path, Browse.
  - Keep download progress modal, but improve wording and avoid making minimized downloads feel stuck.
- Recording section:
  - Hotkey selector.
  - Max recording seconds.
  - Hide sample rate from normal users.
- Advanced section:
  - Sample rate.
  - Model storage path details.
  - Config path.
  - Diagnostics shortcut.
  - Add Reset Settings only if implemented safely.

## Implementation Changes

- Refactor `voice_automation/desktop.py` UI construction into smaller helpers:
  - `build_home_page()`
  - `build_settings_page()`
  - `build_backend_section()`
  - `build_deepgram_panel()`
  - `build_moonshine_panel()`
  - `build_recording_section()`
  - `build_advanced_section()`
- Add small reusable UI helpers:
  - status badge label factory
  - section panel/group factory
  - primary/secondary button styling helper
- Keep existing runtime behavior:
  - no silent backend fallback
  - Start blocked until selected backend is ready
  - desktop config stored under app data
  - Deepgram key stored through keyring
  - Moonshine download remains background-threaded
- Do not add new dependencies.
- Do not add a first-run wizard in this pass. Home and Settings should be self-guided.

## Test Plan

- Run `python -m py_compile voice_automation\desktop.py`.
- Run `python -m compileall voice_automation`.
- Manual UI checks:
  - Home renders cleanly at the current minimum size.
  - Settings remains usable when window height is reduced.
  - Backend selector switches panels correctly.
  - Deepgram missing key shows a clear blocked state.
  - Saved Deepgram key shows ready state.
  - Moonshine missing model shows download-required state.
  - Downloaded Moonshine model shows ready state.
  - Start remains disabled until backend is ready.
  - Test Backend remains blocked while dictation is running.
  - Diagnostics still opens from Home and tray.
- Packaged app checks after build:
  - Settings layout works from `dist\VoiceAutomation\VoiceAutomation.exe`.
  - Icon, tray menu, dialogs, keyring, and model path display correctly.

## Assumptions

- Visual direction: native professional Windows utility UI.
- No separate first-run wizard for this pass.
- Settings redesign is the priority; Home gets polish only where needed.
- The current PySide6/Qt 6.11.1 package is sufficient, so no framework switch is needed.
