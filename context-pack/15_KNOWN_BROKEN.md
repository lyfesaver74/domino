## 15_KNOWN_BROKEN — Known mismatches / breakpoints (as stated)

### Fish workspace
- Root docker-compose.yml is empty.
- Upstream compose volume paths may not point at the real wrapper checkpoints/ and references/ folders.
- Nginx expects hostname fish-speech-server but upstream compose service is named server (DNS mismatch unless container_name matches).
- Custom Fish UI delete flow mismatch:
  - undefined API_BASE in one place
  - delete uses query param but server expects body reference_id

### Wake Word PC app
- ask payload in hub_client.py is {persona,text,room,session_id,context}; richer contract described in PROJECT.md not currently sent.
- TTS is received (audio_b64) but only logged; no playback and no tts_audio broadcast.
- Persona labeling/coloring uses wake persona, not hub-returned persona (matters for collective routing).

### Domino Hub
- Local LM Studio base URL caveat when hub runs in Docker:
  - default 127.0.0.1 points to container, not host; host.docker.internal not guaranteed on Linux.
