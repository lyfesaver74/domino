## 08_FRONTEND — Hub console UI (hub-served)

Files:
- console.html: persona selector + auto-promote checkbox + chat pane + mic/send/play controls
- console.js: behavior
- console.css: styling

Key flows:
- Session ID stored in localStorage key: dominoHubSessionId (random UUID)
  - sent as session_id in requests
- Auto-promote toggle stored in localStorage key: dominoHubAutoPromote
  - sent as context.extra.auto_promote in POST /api/ask_stream

Request flow:
- Streaming by default:
  - fetch('/api/ask_stream', { method:'POST', body: { persona, text, session_id, context }})

SSE parsing:
- reads from resp.body.getReader()
- splits frames on blank line (\n\n)
- handles events: meta, memory, message, audio, error, done

Audio handling:
- If SSE audio includes audio_id:
  - UI fetches GET /api/audio/{audio_id}
  - plays queued audio sequentially (prevents overlaps)
- “Play” button replays last audio

Mic/STT flow:
- Prefers MediaRecorder:
  - Record audio → POST /api/stt (multipart) → {text} → send chat
- Otherwise uses webkitSpeechRecognition.
