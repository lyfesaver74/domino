const chatEl = document.getElementById('chat');
const inputEl = document.getElementById('text-input');
const sendBtn = document.getElementById('send-btn');
const statusEl = document.getElementById('status');
const micBtn = document.getElementById('mic-btn');
const playBtn = document.getElementById('play-btn');
const autoPromoteEl = document.getElementById('auto-promote');

function loadAutoPromote() {
  try {
    const key = 'dominoHubAutoPromote';
    const v = localStorage.getItem(key);
    const enabled = v === '1';
    if (autoPromoteEl) autoPromoteEl.checked = enabled;
  } catch (_) {}
}

function saveAutoPromote(v) {
  try {
    const key = 'dominoHubAutoPromote';
    localStorage.setItem(key, v ? '1' : '0');
  } catch (_) {}
}

function getAutoPromote() {
  try {
    return !!(autoPromoteEl && autoPromoteEl.checked);
  } catch (_) {
    return false;
  }
}

function getOrCreateSessionId() {
  try {
    const key = 'dominoHubSessionId';
    let v = localStorage.getItem(key);
    if (!v) {
      v = (crypto?.randomUUID ? crypto.randomUUID() : String(Date.now()) + '-' + String(Math.random()).slice(2));
      localStorage.setItem(key, v);
    }
    return v;
  } catch (_) {
    return null;
  }
}

// ---- persona + UI helpers ----
function getPersona() {
  return document.querySelector('input[name="persona"]:checked').value;
}

function appendMessage(role, text, persona, meta) {
  const div = document.createElement('div');
  div.classList.add('msg');
  if (role === 'user') {
    div.classList.add('user');
  } else {
    div.classList.add(persona);
  }

  const content = document.createElement('div');
  content.textContent = text;
  div.appendChild(content);

  if (meta && meta.has_audio && meta.tts_provider) {
    const metaEl = document.createElement('span');
    metaEl.className = 'meta';
    metaEl.textContent = `Audio: ${meta.tts_provider}`;
    div.appendChild(metaEl);
  }

  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function appendSystem(text) {
  const div = document.createElement('div');
  div.classList.add('msg', 'system');
  div.textContent = text;
  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

// ---- TTS (mouth) ----
const synthSupported = 'speechSynthesis' in window;
let voices = [];
const personaVoiceSettings = {
  domino: { rate: 1.05, pitch: 1.2 },
  penny: { rate: 1.0, pitch: 1.0 },
  jimmy: { rate: 0.95, pitch: 0.9 },
};

const voiceHints = {
  domino: ['androgynous', 'neutral', 'aria', 'zira'],
  penny: ['female', 'jenny', 'salli', 'emma'],
  jimmy: ['male', 'guy', 'brian', 'matthew'],
};

function loadVoices() {
  if (!synthSupported) return;
  voices = window.speechSynthesis.getVoices();
  if (!voices.length) {
    window.speechSynthesis.onvoiceschanged = () => {
      voices = window.speechSynthesis.getVoices();
    };
  }
  if (voices.length) {
    statusEl.textContent = 'TTS ready';
  } else if (synthSupported) {
    statusEl.textContent = 'Loading voices...';
  } else {
    statusEl.textContent = 'No browser TTS';
  }
}

function getVoiceForPersona(persona) {
  if (!voices.length) return null;
  const hints = voiceHints[persona] || [];
  for (const hint of hints) {
    const v = voices.find((voice) => voice.name.toLowerCase().includes(hint.toLowerCase()));
    if (v) return v;
  }
  if (persona === 'domino') return voices[0];
  if (persona === 'penny') return voices[1] || voices[0];
  if (persona === 'jimmy') return voices[2] || voices[0];
  return voices[0];
}

// ---- Server audio playback (Fish / ElevenLabs) ----
let lastServerAudioSrc = null; // legacy data: URL (base64)
let lastServerAudioProvider = null;
let lastServerAudioId = null;
let lastServerAudioMime = null;
let currentServerAudio = null;
let serverAudioStarting = false;
const serverAudioQueue = [];

function mimeFromProvider(provider) {
  const p = (provider || '').toLowerCase();
  if (p === 'elevenlabs') return 'audio/mpeg';
  // Fish returns WAV in this stack
  return 'audio/wav';
}

function setServerAudio(audio_b64, provider) {
  if (!audio_b64) {
    lastServerAudioSrc = null;
    lastServerAudioProvider = null;
    lastServerAudioId = null;
    lastServerAudioMime = null;
    playBtn.disabled = true;
    playBtn.textContent = 'Play';
    return;
  }

  const mime = mimeFromProvider(provider);
  lastServerAudioSrc = `data:${mime};base64,${audio_b64}`;
  lastServerAudioProvider = provider || null;
  lastServerAudioId = null;
  lastServerAudioMime = null;
  playBtn.disabled = false;
  playBtn.textContent = provider ? `Play (${provider})` : 'Play';
}

function enqueueServerAudio(audio_b64, provider) {
  if (!audio_b64) return;
  const mime = mimeFromProvider(provider);
  const src = `data:${mime};base64,${audio_b64}`;
  serverAudioQueue.push({ kind: 'src', src, provider, mime });
  // Update "Play" button to the most recent audio
  lastServerAudioSrc = src;
  lastServerAudioProvider = provider || null;
  lastServerAudioId = null;
  lastServerAudioMime = mime;
  playBtn.disabled = false;
  playBtn.textContent = provider ? `Play (${provider})` : 'Play';
}

function enqueueServerAudioId(audio_id, provider, mime) {
  if (!audio_id) return;
  const resolvedMime = mime || mimeFromProvider(provider);
  serverAudioQueue.push({ kind: 'id', audio_id, provider, mime: resolvedMime });

  // Update "Play" button to the most recent audio
  lastServerAudioSrc = null;
  lastServerAudioProvider = provider || null;
  lastServerAudioId = audio_id;
  lastServerAudioMime = resolvedMime;
  playBtn.disabled = false;
  playBtn.textContent = provider ? `Play (${provider})` : 'Play';
}

async function fetchAudioObjectUrl(audio_id) {
  const resp = await fetch(`/api/audio/${encodeURIComponent(audio_id)}`);
  if (!resp.ok) throw new Error(`audio fetch failed: ${resp.status}`);
  const blob = await resp.blob();
  return URL.createObjectURL(blob);
}

async function playNextInQueue() {
  // Important: guard against concurrent starts.
  // Multiple SSE 'audio' events can arrive back-to-back; without this lock,
  // each handler can see currentServerAudio === null and start overlapping playback.
  if (currentServerAudio || serverAudioStarting || !serverAudioQueue.length) return;
  serverAudioStarting = true;
  const next = serverAudioQueue.shift();

  let src = null;
  let revokeUrl = null;
  try {
    if (next.kind === 'id') {
      src = await fetchAudioObjectUrl(next.audio_id);
      revokeUrl = src;
    } else {
      src = next.src;
    }
  } catch (_) {
    // If fetch fails, skip to next
    serverAudioStarting = false;
    playNextInQueue();
    return;
  }

  // From here on, other calls will be blocked by currentServerAudio.
  // Clear the startup lock before awaiting playback.
  serverAudioStarting = false;

  const audio = new Audio(src);
  currentServerAudio = audio;
  audio.onended = () => {
    if (revokeUrl) {
      try {
        URL.revokeObjectURL(revokeUrl);
      } catch (_) {}
    }
    currentServerAudio = null;
    playNextInQueue();
  };
  try {
    await audio.play();
  } catch (_) {
    // Autoplay can be blocked.
    if (revokeUrl) {
      try {
        URL.revokeObjectURL(revokeUrl);
      } catch (_) {}
    }
    currentServerAudio = null;
    // Try the next queued item (may still fail if user gesture required).
    playNextInQueue();
  }
}

async function playServerAudio() {
  if (!lastServerAudioSrc && !lastServerAudioId) return;

  if (currentServerAudio) {
    try {
      currentServerAudio.pause();
    } catch (_) {}
    currentServerAudio = null;
  }

  let src = lastServerAudioSrc;
  let revokeUrl = null;
  if (!src && lastServerAudioId) {
    try {
      src = await fetchAudioObjectUrl(lastServerAudioId);
      revokeUrl = src;
    } catch (_) {
      return;
    }
  }

  const audio = new Audio(src);
  currentServerAudio = audio;
  audio.onended = () => {
    if (revokeUrl) {
      try {
        URL.revokeObjectURL(revokeUrl);
      } catch (_) {}
    }
    currentServerAudio = null;
  };
  try {
    await audio.play();
  } catch (_) {
    // Autoplay can be blocked; user can click Play.
    if (revokeUrl) {
      try {
        URL.revokeObjectURL(revokeUrl);
      } catch (_) {}
    }
  }
}

playBtn.addEventListener('click', () => {
  playServerAudio();
});

function speak(text, persona, ttsInfo) {
  // Prefer server-generated audio if present
  if (ttsInfo && ttsInfo.audio_b64) {
    // Stop any browser speech before playing server audio
    if (synthSupported) {
      window.speechSynthesis.cancel();
    }

    setServerAudio(ttsInfo.audio_b64, ttsInfo.tts_provider);
    if (ttsInfo.tts_provider) {
      statusEl.textContent = `Audio via ${ttsInfo.tts_provider}`;
    }
    playServerAudio();
    return;
  }

  // Fallback: browser TTS
  if (!synthSupported || !text) return;
  const utter = new SpeechSynthesisUtterance(text);
  const v = getVoiceForPersona(persona);
  if (v) utter.voice = v;
  const cfg = personaVoiceSettings[persona] || {};
  if (cfg.rate) utter.rate = cfg.rate;
  if (cfg.pitch) utter.pitch = cfg.pitch;

  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utter);
}

loadVoices();
if (synthSupported) {
  window.speechSynthesis.onvoiceschanged = loadVoices;
}

loadAutoPromote();
if (autoPromoteEl) {
  autoPromoteEl.addEventListener('change', () => {
    saveAutoPromote(getAutoPromote());
  });
}

// ---- ASR (ears) – optional, Chrome/Edge only ----
let recognizer = null;
let recognizing = false;

// Whisper STT fallback (records audio and sends to server)
let whisperRecording = false;
let mediaRecorder = null;
let mediaStream = null;
let recordedChunks = [];

function canUseWhisperRecorder() {
  return !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
}

if ('webkitSpeechRecognition' in window) {
  recognizer = new webkitSpeechRecognition();
  recognizer.lang = 'en-US';
  recognizer.interimResults = false;
  recognizer.maxAlternatives = 1;

  recognizer.onstart = () => {
    recognizing = true;
    micBtn.classList.add('active');
    statusEl.textContent = 'Listening...';
  };
  recognizer.onend = () => {
    recognizing = false;
    micBtn.classList.remove('active');
    statusEl.textContent = synthSupported ? 'TTS ready' : '';
  };
  recognizer.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    inputEl.value = transcript;
    sendMessage();
  };
  recognizer.onerror = () => {
    recognizing = false;
    micBtn.classList.remove('active');
    statusEl.textContent = 'Mic error';
  };
}

// Prefer Whisper recorder if possible; otherwise fall back to webkitSpeechRecognition.
if (canUseWhisperRecorder()) {
  micBtn.disabled = false;
  micBtn.title = 'Click to start/stop recording (Whisper STT)';
} else if (recognizer) {
  micBtn.disabled = false;
  micBtn.title = 'Click to start/stop listening';
} else {
  micBtn.disabled = true;
  micBtn.title = 'Speech recognition not supported in this browser';
}

micBtn.addEventListener('click', () => {
  // Primary: record audio and send to Whisper via /api/stt
  if (canUseWhisperRecorder()) {
    if (!whisperRecording) {
    (async () => {
      try {
        recordedChunks = [];
        mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true });

        const preferredTypes = [
          'audio/webm;codecs=opus',
          'audio/webm',
          'audio/ogg;codecs=opus',
          'audio/ogg',
        ];
        let chosenType = '';
        for (const t of preferredTypes) {
          if (MediaRecorder.isTypeSupported(t)) {
            chosenType = t;
            break;
          }
        }

        mediaRecorder = chosenType ? new MediaRecorder(mediaStream, { mimeType: chosenType }) : new MediaRecorder(mediaStream);
        mediaRecorder.ondataavailable = (e) => {
          if (e.data && e.data.size > 0) recordedChunks.push(e.data);
        };
        mediaRecorder.onstop = async () => {
          try {
            const blob = new Blob(recordedChunks, { type: mediaRecorder.mimeType || 'application/octet-stream' });
            const fd = new FormData();
            const ext = (blob.type || '').includes('ogg') ? 'ogg' : 'webm';
            fd.append('file', blob, `speech.${ext}`);

            statusEl.textContent = 'Transcribing...';
            const resp = await fetch('/api/stt', { method: 'POST', body: fd });
            if (!resp.ok) {
              appendSystem(`STT error: ${resp.status} ${resp.statusText}`);
              statusEl.textContent = synthSupported ? 'TTS ready' : '';
              return;
            }
            const data = await resp.json();
            const transcript = (data && data.text ? String(data.text) : '').trim();
            if (!transcript) {
              statusEl.textContent = 'No speech detected';
              return;
            }
            inputEl.value = transcript;
            sendMessage();
          } catch (e) {
            appendSystem(`STT error: ${e}`);
          } finally {
            try {
              mediaStream?.getTracks?.().forEach((t) => t.stop());
            } catch (_) {}
            mediaStream = null;
            mediaRecorder = null;
            recordedChunks = [];
            statusEl.textContent = synthSupported ? 'TTS ready' : '';
          }
        };

        whisperRecording = true;
        micBtn.classList.add('active');
        statusEl.textContent = 'Recording (Whisper)...';
        mediaRecorder.start();
      } catch (e) {
        whisperRecording = false;
        micBtn.classList.remove('active');
        appendSystem(`Mic error: ${e}`);
        try {
          mediaStream?.getTracks?.().forEach((t) => t.stop());
        } catch (_) {}
        mediaStream = null;
      }
    })();
    } else {
    try {
      mediaRecorder?.stop();
    } catch (_) {}
    whisperRecording = false;
    micBtn.classList.remove('active');
    }
    return;
  }

  // Fallback: built-in webkitSpeechRecognition if available.
  if (recognizer) {
    if (!recognizing) {
      recognizer.start();
    } else {
      recognizer.stop();
    }
  }
});

// ---- talking to Domino Hub ----
async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  const persona = getPersona();
  appendMessage('user', text, persona);
  inputEl.value = '';
  inputEl.focus();

  // Streaming request (POST) so each persona can respond independently
  const controller = new AbortController();
  sendMessage._controller?.abort?.();
  sendMessage._controller = controller;

  // reset server audio queue for a new request
  serverAudioQueue.length = 0;
  if (currentServerAudio) {
    try {
      currentServerAudio.pause();
    } catch (_) {}
    currentServerAudio = null;
  }

  const seenPersonas = new Set();

  try {
    const resp = await fetch('/api/ask_stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        persona,
        text,
        session_id: getOrCreateSessionId(),
        context: { extra: { auto_promote: getAutoPromote() } },
      }),
      signal: controller.signal,
    });

    const ctype = (resp.headers.get('content-type') || '').toLowerCase();
    if (!resp.ok) {
      appendSystem(`Error: ${resp.status} ${resp.statusText}`);
      return;
    }

    // Fallback: if server returned JSON, handle like before
    if (!ctype.includes('text/event-stream')) {
      const data = await resp.json();
      const responseHadAudio = !!data.audio_b64;
      const effectivePersona = (data.persona || persona || 'domino').toLowerCase();
      appendMessage(effectivePersona, data.reply, effectivePersona, {
        tts_provider: data.tts_provider,
        has_audio: responseHadAudio,
      });
      speak(data.reply, effectivePersona, data);
      return;
    }

    statusEl.textContent = 'Waiting for responses...';

    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    let currentEvent = 'message';

    function handleSseBlock(block) {
      // block is lines separated by \n
      const lines = block.split('\n');
      let eventName = null;
      const dataLines = [];
      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventName = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          dataLines.push(line.slice(5).trim());
        }
      }
      if (!eventName) eventName = currentEvent;
      const dataStr = dataLines.join('\n');
      if (!dataStr) return;
      let payload;
      try {
        payload = JSON.parse(dataStr);
      } catch (_) {
        return;
      }

      if (eventName === 'meta') {
        // no-op UI prep; we could show targets later if desired
        return;
      }

      if (eventName === 'memory') {
        const mode = payload.mode || payload.status || payload.kind || 'memory';
        if (payload.kind === 'promoted_state') {
          if (payload.mode === 'applied') {
            appendSystem(`Promoted state updated: ${JSON.stringify(payload.patch || {})}`);
          } else if (payload.mode === 'suggested') {
            appendSystem(`Suggested promoted update: ${JSON.stringify(payload.patch || {})}`);
          } else if (payload.mode === 'error') {
            appendSystem(`Promoted-state error: ${payload.error || 'unknown'}`);
          } else {
            appendSystem(`Promoted-state: ${JSON.stringify(payload)}`);
          }
        } else {
          appendSystem(`${mode}: ${JSON.stringify(payload)}`);
        }
        return;
      }

      if (eventName === 'message') {
        const p = (payload.persona || 'domino').toLowerCase();
        seenPersonas.add(p);
        appendMessage(p, payload.reply || '', p, {
          tts_provider: null,
          has_audio: false,
        });
        statusEl.textContent = `Got ${seenPersonas.size} response(s)...`;
        return;
      }

      if (eventName === 'audio') {
        if (payload.audio_id) {
          enqueueServerAudioId(payload.audio_id, payload.tts_provider, payload.mime);
          if (synthSupported) window.speechSynthesis.cancel();
          playNextInQueue();
          statusEl.textContent = 'Playing audio queue...';
        }
        return;
      }

      if (eventName === 'error') {
        const p = (payload.persona || 'system').toLowerCase();
        appendSystem(`${p}: ${payload.error || 'unknown error'}`);
        return;
      }

      if (eventName === 'done') {
        statusEl.textContent = 'TTS ready';
        return;
      }
    }

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // Parse SSE frames separated by blank line
      let idx;
      while ((idx = buffer.indexOf('\n\n')) !== -1) {
        const block = buffer.slice(0, idx).trim();
        buffer = buffer.slice(idx + 2);
        if (!block || block.startsWith(':')) continue;
        handleSseBlock(block);
      }
    }
  } catch (e) {
    if (String(e).includes('AbortError')) {
      // Request replaced by a newer one
      return;
    }
    appendSystem(`Error: ${e}`);
  } finally {
    inputEl.disabled = false;
    sendBtn.disabled = false;
    if (!statusEl.textContent || statusEl.textContent === 'Waiting for responses...') {
      statusEl.textContent = 'TTS ready';
    }
  }
}

sendBtn.addEventListener('click', sendMessage);

inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});
