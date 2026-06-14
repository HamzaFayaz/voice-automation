# Next Steps

## 1. Final Desktop Testing

Run the desktop app from source:

```bat
python -m voice_automation.desktop
```

Test this checklist:

- App opens to the Home screen.
- Tray icon appears.
- Settings screen opens.
- Deepgram API key can be saved.
- Saved Deepgram key is not shown in the input field.
- Deepgram backend can be selected.
- Moonshine backend can be selected.
- Only the selected backend settings are visible.
- Check Environment output is readable.
- Test Backend records and returns a transcript.
- Start enables dictation.
- Stop disables dictation.
- Hold hotkey to record.
- Release hotkey to transcribe and type once.
- Dictation works in Notepad.
- Dictation works in Cursor.
- Dictation works in a browser text field.
- App exits cleanly from the tray menu.

## 2. Build The Windows App

Build the PyInstaller one-folder app:

```bat
build_desktop.bat
```

Expected output:

```text
dist\VoiceAutomation\
```

The folder should contain:

```text
VoiceAutomation.exe
_internal\
```

## 3. Test The Built App

Run:

```bat
dist\VoiceAutomation\VoiceAutomation.exe
```

Repeat the desktop testing checklist against the packaged app.

Pay special attention to:

- Launch from `dist`.
- Keyring access for the Deepgram API key.
- Microphone access.
- Global hotkey access.
- Deepgram transcription.
- Direct typing into other applications.
- Tray Quit behavior.

## 4. Create Release Zip

Zip the full one-folder build, not only the `.exe`:

```bat
powershell Compress-Archive -Path dist\VoiceAutomation -DestinationPath VoiceAutomation-Windows.zip -Force
```

The release asset should be:

```text
VoiceAutomation-Windows.zip
```

## 5. Add To GitHub Release

Create a GitHub Release, then upload:

```text
VoiceAutomation-Windows.zip
```

Do not commit the built `.exe`, `build\`, or `dist\` folders to the repository.

Recommended release notes:

```text
Windows desktop tray app for Voice Automation.

- Deepgram online dictation
- Moonshine offline model selection
- Push-to-talk global hotkey
- Direct typing into active applications
- Settings stored under user app data
- Deepgram API key stored with OS keyring
```
