# Domino Surface Listener (Surface Pro) — Project Plan + Domino-Hub Integration Contract

## Purpose

Build a **Windows Surface Pro always-on listener** that:

1. continuously listens for **4 wake words**
2. records the user’s utterance after wake
3. sends audio to **Domino-Hub** for STT
4. sends transcribed text to **Domino-Hub** for reasoning + actions + TTS
5. displays assistant-colored captions and a KITT-style audio visualizer in an **HTML overlay**

This is the “daily usable” client: no browser tab, no push-to-talk.

---

## Wake words (required from day one)

The client must support **four distinct wake words** simultaneously:

* **Domino**
* **Penny**
* **Jimmy**
* **Collective** (the “address them all” keyword, e.g. `Collective`)

### Wake word → persona behavior

* If wake word is Domino/Penny/Jimmy: persona is **locked** to that assistant.
* If wake word is Collective: persona is **Collective routing**:

  * send `persona="Collective"` to hub (hub chooses), OR
  * allow “first token steering” if the user says “Penny, …” after waking the Collective.

The client must always include which wake word fired in its internal state and overlay output.

---

## System components

### 1) Surface Listener Core (Windows tray app)

Responsibilities:

* microphone capture (always on)
* wake word detection (4 keywords loaded at once)
* voice activity detection / end-of-utterance detection
* record audio to WAV/PCM buffer
* call Domino-Hub endpoints:

  * STT: `POST /api/stt`
  * ask: `POST /api/ask`
* publish UI events to overlay (local websocket)
* (optional) local audio playback fallback

### 2) HTML Overlay (streamer-grade UI)

Responsibilities:

* display status: Listening / Recording / Transcribing / Thinking / Speaking
* display assistant reply text along bottom:

  * each assistant has a consistent color
  * label prefix includes assistant name
* display KITT-style audio visualizer:

  * color coded to the assistant speaking
* play audio from `audio_b64` (preferred) using WebAudio so we get a clean analyzer node

### 3) Overlay host window (HTMLWindowsOverlay)

The overlay is loaded in a transparent always-on-top window. It’s separate from the Core app. The overlay connects to Core’s local websocket.

---

## “4 Rules” for changes (for the VS Code agent)

1. **No guessing.** If code needs changes, locate the exact file and exact lines first.
2. **Exact instructions.** Use “add this file”, “replace this block”, “insert under this section”.
3. **Verify assumptions** against current repo content before changing.
4. **No phantom dependencies.** Don’t introduce components that aren’t installed or referenced.

---

## Domino-Hub Integration Contract (HTTP)

The Surface client talks to Domino-Hub via **two calls per interaction**:

1. audio → STT
2. text → ask (LLM + actions + TTS)

### Base URL

Config value:

* `HUB_BASE_URL` (example: `http://127.0.0.1:8000`)

### 1) STT Endpoint

#### Request (STT)

* `POST {HUB_BASE_URL}/api/stt`
* Content-Type: `multipart/form-data`
* Form field name: `file`
* File: WAV preferred (`audio/wav`)
  (If you use raw PCM, define it explicitly; WAV is simplest.)

#### Response (STT, expected minimal)

```json
{ "text": "transcribed speech here" }
```

#### Error behavior

* non-2xx should be treated as failure → overlay shows error → return to Listening state.

### 2) Ask Endpoint

#### Request (Ask)

* `POST {HUB_BASE_URL}/api/ask`
* Content-Type: `application/json`

#### Minimum request fields (recommended)

```json
{
  "text": "user request",
  "persona": "Domino | Penny | Jimmy | Collective",
  "client": {
    "device": "surface-pro",
    "wake_word": "Domino | Penny | Jimmy | Collective",
    "session_id": "optional-stable-session-id"
  }
}
```

Notes:

* Keep `persona` exactly one of the supported values.
* Include the `wake_word` used to trigger, even if `persona` is Collective.
* If your hub already has a session concept, pass it; otherwise omit.

#### Response (Ask, expected minimal)

```json
{
  "reply": "text to show and speak",
  "audio_b64": "base64wav_or_mp3_optional",
  "tts_provider": "fish|elevenlabs|...",
  "persona": "Domino|Penny|Jimmy", 
  "actions": [
    {
      "kind": "ha_call_service",
      "domain": "light",
      "service": "turn_on",
      "entity_id": "light.desk"
    }
  ]
}
```

Notes:

* `audio_b64` may be absent. Client must handle “no audio returned”.
* `persona` might differ when `persona=Collective` was requested (hub chooses). Client should use returned persona for color/label.
* `actions` should be treated as debug-visible info in overlay (optional display), even if hub already executed them.

### Optional Health / Version Endpoints (recommended)

If hub provides these, the client can show “Hub Offline” status cleanly:

* `GET {HUB_BASE_URL}/health` → 200 OK
* `GET {HUB_BASE_URL}/version` → include build hash / config flags

If they do not exist, the client can just treat failed `/api/stt` as “offline”.

---

## Overlay Integration Contract (Local WebSocket)

Core hosts a local websocket server (localhost only):

* `ws://127.0.0.1:{PORT}/ws`

Overlay is a dumb renderer: it does not call the hub directly.

### Core → Overlay event messages

All messages are JSON with a top-level `"type"`.

#### status

```json
{ "type":"status", "state":"listening|recording|transcribing|thinking|speaking|error", "hint":"text", "color":"#RRGGBB" }
```

#### wake

```json
{ "type":"wake", "wake_word":"Domino|Penny|Jimmy|Collective", "persona_mode":"Domino|Penny|Jimmy|Collective", "color":"#RRGGBB" }

```

#### user_utterance

```json
{ "type":"user_utterance", "text":"..." }
```

#### assistant_reply

```json
{ "type":"assistant_reply", "persona":"Domino|Penny|Jimmy", "color":"#RRGGBB", "text":"..." }
```

#### tts_audio

```json
{ "type":"tts_audio", "persona":"Domino|Penny|Jimmy", "color":"#RRGGBB", "format":"wav|mp3", "audio_b64":"..." }
```

#### actions (optional but useful)

```json
{ "type":"actions", "items":[ ... ] }
```

#### error

```json
{ "type":"error", "stage":"stt|ask|audio|wake", "message":"human readable message" }
```

### Overlay behavior expectations

* Always show the latest `status`.
* Bottom ticker appends `assistant_reply` lines (persona color + label).
* On `tts_audio`, overlay plays audio and animates visualizer in that persona color.

---

## Configuration

Single JSON config file for the Surface app:

* `settings.json`

Contains:

* hub base url
* websocket port
* wake word definitions (4)
* colors per persona/wake word
* audio capture settings (sample rate/channels)
* recording settings (max seconds / VAD threshold)
* optional sound cues per persona

Keep all integration-sensitive values in config (not hardcoded).

---

## State machine (must be explicit in code)

### Listening

* mic frames feed wake engine
* overlay: `status=listening`

### WakeDetected(wake_word, persona_mode)

* chirp (optional)
* overlay: `wake` + `status=recording`

### Recording

* capture utterance until:

  * silence threshold sustained OR
  * max duration
* then stop

### Transcribing

* send WAV to hub `/api/stt`
* overlay `status=transcribing`

### Thinking

* send text to hub `/api/ask` with persona_mode + wake_word metadata
* overlay `status=thinking`

### Speaking

* if `audio_b64` returned:

  * send `tts_audio` to overlay to play and visualize
  * overlay `status=speaking`
* else just display text and return to listening

### Error

* emit overlay error
* return to listening

---

## Development milestones (what the VS Code agent should implement in order)

### Milestone 0 — Repo bootstrap

* create a new folder with:

  * `PROJECT.md` (this file)
  * `settings.json` template
  * `src/` folder for code
  * `overlay/Content/` for HTML overlay

### Milestone 1 — Overlay websocket + dummy UI

* Core hosts websocket
* Overlay connects and shows “ONLINE”
* Core can broadcast `status` and `assistant_reply` test messages

Acceptance:

* launching Core + overlay shows state changes.

### Milestone 2 — Wake word detection with 4 keywords

* wake engine loads 4 wake words simultaneously
* returns which keyword fired

Acceptance:

* speaking any wake word changes overlay color + state to Recording.

### Milestone 3 — Record + VAD stop

* after wake, record utterance
* stop on silence or max seconds

Acceptance:

* audio captured reliably; no hangs; returns to Listening after stop.

### Milestone 4 — Domino-Hub calls

* implement `/api/stt` + `/api/ask`
* wire responses into overlay events

Acceptance:

* full pipeline works end-to-end:
  wake → STT → ask → caption shows → audio plays (if returned).

### Milestone 5 — Visualizer

* overlay uses WebAudio analyser on the returned `tts_audio`
* KITT bars drawn on canvas and color-coded

Acceptance:

* bars animate during speech.

---

## Testing checklist

* Hub offline: overlay shows error, app returns to listening without crash.
* Wake word false positives: sensitivity configurable per word.
* Collective mode: hub decides persona; returned persona changes overlay color.
* No `audio_b64`: captions still show; state returns to listening.
* Long utterance: max duration enforced.
* Rapid consecutive triggers: ignore wake word while in Recording/Thinking/Speaking.

---

## Implementation notes for the VS Code agent

* Keep **all Domino-Hub request/response parsing in a single “HubClient” module** so schema changes are localized.
* Keep **overlay event schema centralized** in a single file or types so both sender and UI are consistent.
* Don’t implement HA calls here; Domino-Hub already does actions. Client just displays them if returned.

---

## If the agent needs the *exact* hub schema

Before coding the final HubClient, validate by running:

* `curl -X POST /api/stt ...`
* `curl -X POST /api/ask ...`

Then lock the request/response JSON structure in this doc and implement accordingly.

(If you already have sample responses from your hub logs, paste them into this project folder as `samples/stt_response.json` and `samples/ask_response.json` and use them as ground truth.)

---

If you want, paste **one real `/api/ask` response JSON** (from your hub logs) and I’ll revise the “Integration Contract” section above so it matches your hub *exactly*—still with no guessing.
