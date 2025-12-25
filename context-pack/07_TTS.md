## 07_TTS — Fish + ElevenLabs + browser fallback

### Server-side TTS decision
main.py: generate_tts(persona, text, tts_pref="auto")
- tts_pref comes from promoted state tts_overrides[persona] via _pick_tts_pref()

Rules:
- "off" → no audio
- "browser" → no server audio (client uses Web Speech)
- "auto" / "fish":
  - try Fish if enabled (FISH_TTS_ENABLED)
  - if Fish fails, try ElevenLabs if configured
- "elevenlabs":
  - skip Fish, go straight to ElevenLabs

### Fish client (tts_fish.py)
- Gate: FISH_TTS_ENABLED (env boolean, default False)
- Endpoint: FISH_TTS_BASE_URL default http://fish-speech-server:8080
- Calls POST {base}/v1/tts and base64-encodes binary response
- Optional per-persona reference_id via env:
  - FISH_REF_DOMINO, FISH_REF_PENNY, FISH_REF_JIMMY

### ElevenLabs (main.py)
Requires:
- ELEVENLABS_API_KEY
- ELEVENLABS_VOICE_DOMINO, ELEVENLABS_VOICE_PENNY, ELEVENLABS_VOICE_JIMMY
Uses:
- ELEVENLABS_MODEL_ID default eleven_multilingual_v2

### Client/browser TTS (console.js)
- Uses window.speechSynthesis with persona-tuned rate/pitch if no server audio provided.
