# Domino Surface Listener (Milestone 1)

Milestone 1 delivers:

- Core hosts a local websocket server: `ws://127.0.0.1:{PORT}/ws`
- Overlay HTML connects and renders `status` + `assistant_reply` demo messages

## Prereqs

- Windows
- Python 3.10+

## Install

From the repo root:

- `python -m pip install -r requirements.txt`

## Run Core websocket

From the repo root:

- `python src/core_ws.py`

Expected console output includes the websocket URL.

## Milestone 2 (Wake Words)

Milestone 2 uses Vosk keyword spotting to detect the four wake words:

- Domino
- Penny
- Jimmy
- Collective

### Download model

Place the Vosk model folder at:

- `models/vosk-model-small-en-us-0.15/`

See [models/README.md](models/README.md).

### Run Core (wake listener)

- `python src/core_m2.py`

When you say a wake word, the overlay should receive a `wake` event and switch to `recording` briefly.

## Milestone 3 (Record + VAD stop)

Milestone 3 is implemented in the same entrypoint:

- `python src/core_m2.py`

Behavior:

- Say a wake word
- Core switches overlay to `recording`
- Records until you stop speaking (silence sustained) or `recording.max_seconds`
- Prints a line like: `[m3] recorded wav: ... bytes, sr=..., dur=...s`
- Returns overlay to `listening`

## Run Overlay (HTMLWindowsOverlay)

This repo includes the overlay content at:

- `overlay/Content/index.html`

In Streamer.bot HTMLWindowsOverlay (from https://github.com/Streamerbot/html-windows-overlay), point it at that `index.html` file.

When the overlay connects, the top-left indicator should show `ONLINE` and you should see a short demo state sequence plus a `Domino: Milestone 1 websocket OK ...` line in the ticker.

## Config

Websocket host/port/path are read from:

- `settings.json` → `overlay_ws`

If you change the port/path in `settings.json`, update the overlay connection URL in:

- `overlay/Content/overlay.js`

(We’ll make the overlay read settings automatically later, if needed.)

## Microphone selection

If the wrong microphone is being used, set an input device in:

- `settings.json` → `audio.input_device`

Accepted values:

- `null` (default): system default input
- a number: device index (e.g. `3`)
- a string: substring match on device name (e.g. `"Yeti"`)

To list available input devices and their indices:

- `python src/list_audio_devices.py`
