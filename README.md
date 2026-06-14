# Voice Automation

A Windows push-to-talk voice automation tool for writing, searching, coding, and operating text fields faster across desktop applications.

This project was built to reduce repetitive typing during daily AI engineering work. The app runs in the background, listens only while a hotkey is held, transcribes speech, cleans the transcript, and inserts the result into the currently focused application.

It supports two speech-to-text modes:

- **Deepgram API mode** for high-accuracy cloud transcription.
- **Moonshine local mode** for offline, CPU-friendly transcription.

## License

This project is open source under the [MIT License](LICENSE).

## Why This Exists

Modern technical work involves constant context switching: coding in Cursor, writing prompts, using terminals, searching documentation, messaging, and testing ideas in browsers. Voice input can remove a lot of small typing overhead, but most dictation tools are either app-specific, always listening, or awkward to use inside developer workflows.

Voice Automation is designed as a lightweight system-wide dictation shortcut:

1. Hold a hotkey.
2. Speak.
3. Release the hotkey.
4. The transcript is pasted into the active app.

The goal is not to replace the keyboard. The goal is to make common text-heavy actions faster.

## Features

- Global push-to-talk hotkey.
- Records only while the hotkey is held.
- Works across normal Windows text fields.
- Supports Deepgram cloud transcription.
- Supports Moonshine local transcription.
- Clipboard paste insertion with direct typing fallback.
- Optional command execution in terminals by ending speech with "run" or "execute".
- Lightweight transcript cleanup and word replacements.
- Small overlay HUD for recording/transcription feedback.
- Config-driven backend switching.

## System Design

```text
[ Global Hotkey ]
  right_ctrl / F8
         |
         | press
         v
[ Audio Capture ]
  sounddevice
  mono chunks
         |
         | audio stream
         v
[ Speech-to-Text Adapter ]
  - Deepgram API
  - Moonshine Local
  - faster-whisper fallback
         |
         | transcript
         v
[ Text Cleanup ]
  spacing, casing,
  replacements
         |
         | clean text
         v
[ Text Insertion ]
  clipboard paste
  direct typing
         |
         v
[ Active App ]
  Cursor, terminal,
  browser, docs
```

## Backend Modes

### Deepgram API Mode

Deepgram is the current default mode in this project because it provides stronger transcription accuracy for real usage. This is useful when dictating technical language, prompts, commands, or longer natural speech.

Deepgram also provides generous free credits, which makes it practical for personal productivity automation.

To use Deepgram mode:

1. Create a Deepgram account.
2. Generate an API key from the Deepgram dashboard.
3. Copy `.env.example` to `.env`.
4. Add your API key to `.env`.
5. Keep `model_provider` set to `deepgram` in `voice_automation_config.json`.

Example config:

```json
{
    "model_provider": "deepgram",
    "sample_rate": 8000,
    "deepgram_api_key": ""
}
```

The API key can be set either in `voice_automation_config.json` or in a `.env` file:

```env
DEEPGRAM_API_KEY=your_api_key_here
```

### Moonshine Local Mode

Moonshine is included for local/offline transcription. It is useful when you want the system to run without sending audio to a cloud API.

Example config:

```json
{
    "model_provider": "moonshine",
    "model_arch": 5,
    "sample_rate": 16000
}
```

Available Moonshine model architecture values:

| Model | `model_arch` |
|---|---:|
| Tiny | `0` |
| Base | `1` |
| Tiny Streaming | `2` |
| Base Streaming | `3` |
| Small Streaming | `4` |
| Medium Streaming | `5` |

For better local accuracy, use `model_arch: 5` for Moonshine Medium Streaming.

Download the selected Moonshine model:

```bat
python -m voice_automation download-model
```

The setup script also runs the model download command:

```bat
setup.bat
```

Important: model download is for Moonshine mode. If `model_provider` is set to `deepgram`, the downloader will not download a Moonshine model until the config is switched to `moonshine`.

## Installation

Requirements:

- Windows
- Python 3.11 or newer
- Microphone access

Create and activate a virtual environment:

```bat
python -m venv .venv
call .venv\Scripts\activate.bat
```

Install the project:

```bat
pip install -e .
```

Or run the setup script:

```bat
setup.bat
```

If you are using Deepgram, create your `.env` file after installation:

```bat
copy .env.example .env
```

Then edit `.env` and set:

```env
DEEPGRAM_API_KEY=your_deepgram_api_key_here
```

## Configuration

The app uses:

```text
voice_automation_config.json
```

Current important settings:

| Setting | Purpose |
|---|---|
| `hotkey` | Key used for push-to-talk |
| `model_provider` | `deepgram`, `moonshine`, or `faster-whisper` |
| `deepgram_api_key` | API key for Deepgram mode |
| `model_arch` | Moonshine model architecture |
| `sample_rate` | Audio sample rate |
| `paste_mode` | `clipboard` or `type` |
| `max_record_seconds` | Safety limit for one recording |
| `replacements` | Custom word replacements |

Default hotkey:

```json
"hotkey": "right_ctrl"
```

### Deepgram Configuration

For cloud transcription, use:

```json
{
    "model_provider": "deepgram",
    "sample_rate": 8000,
    "deepgram_api_key": ""
}
```

The recommended approach is to keep `deepgram_api_key` empty in `voice_automation_config.json` and store the real key in `.env`.

### Moonshine Configuration

For local transcription, use:

```json
{
    "model_provider": "moonshine",
    "model_arch": 5,
    "sample_rate": 16000
}
```

Then download the local model:

```bat
python -m voice_automation download-model
```

## Usage

Run the application:

```bat
python -m voice_automation run
```

Or:

```bat
run.bat
```

Then:

1. Focus any text field.
2. Hold the configured hotkey.
3. Speak.
4. Release the hotkey.
5. The transcript is pasted into the focused app.

Run environment checks:

```bat
python -m voice_automation check
```

Download a Moonshine model:

```bat
python -m voice_automation download-model
```

Note: model download currently applies to Moonshine mode. Deepgram does not need a local model download.

## Project Structure

```text
voice_automation/
+-- __main__.py        CLI entrypoint
+-- orchestrator.py    Main runtime pipeline
+-- audio.py           Microphone recording
+-- hotkey.py          Global push-to-talk listener
+-- stt.py             Deepgram, Moonshine, and faster-whisper adapters
+-- paste.py           Clipboard/direct text insertion
+-- cleanup.py         Transcript cleanup
+-- state.py           Thread-safe app state
+-- overlay.py         Tkinter status HUD
+-- check.py           Environment checks
+-- downloader.py      Moonshine model downloader
+-- config.py          Config and .env loading
```

## Engineering Notes

- The app is intentionally push-to-talk, not always listening.
- STT providers are hidden behind a common adapter interface.
- Audio capture and transcription run through background threads to keep the hotkey loop responsive.
- Clipboard insertion is preferred because it is faster and more reliable than typing character by character.
- Direct typing remains available as a fallback for apps where clipboard paste is not suitable.

## Current Status

This is a working personal automation project focused on Windows productivity workflows. The active configuration uses Deepgram for transcription accuracy, while Moonshine remains available for local/offline experiments.

Future improvements may include a tray icon, richer configuration UI, better test coverage, provider-specific setup commands, and packaging as a standalone Windows executable.
