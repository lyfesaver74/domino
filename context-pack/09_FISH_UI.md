## 09_FISH_UI — Fish Voice Studio UI (nginx-served)

Files:
- index.html: admin/testing UI for Fish references + TTS playback
- nginx.conf: proxies /api/ to Fish

Behavior:
- UI calls fetch('/api/v1/...'), nginx proxies to http://fish-speech-server:8080/v1/...
Supports:
- GET /v1/health
- list/add/delete references
- POST /v1/tts

Note:
- UI JS contains a deleteRef() that references API_BASE in one spot; if delete fails, this may be why (it otherwise uses /api/... elsewhere).
