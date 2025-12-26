/* Domino Hub Console (modern UI)
 * - Chat: SSE streaming via /api/ask_stream
 * - Audio: plays queued server audio /api/audio/{id}
 * - STT: Whisper recorder via /api/stt (or webkitSpeechRecognition fallback)
 * - Settings: reads/patches promoted-state via /api/memory/promoted
 */

const chatEl = document.getElementById('chat');
const inputEl = document.getElementById('text-input');
const sendBtn = document.getElementById('send-btn');
const micBtn = document.getElementById('mic-btn');
const playBtn = document.getElementById('play-btn');
const autoPromoteEl = document.getElementById('auto-promote');
const personaEl = document.getElementById('persona');

const statusTextEl = document.getElementById('status-text');
const statusDotEl = document.getElementById('status-dot');
const sessionLabelEl = document.getElementById('session-label');

const navChatBtn = document.getElementById('nav-chat');
const navSettingsBtn = document.getElementById('nav-settings');
const pageChat = document.getElementById('page-chat');
const pageSettings = document.getElementById('page-settings');

const btnNewSession = document.getElementById('new-session');
const btnClearHistory = document.getElementById('clear-history');
const btnJumpSettings = document.getElementById('jump-settings');
const btnRefreshStatus = document.getElementById('refresh-status');

// Status KV (chat side panel)
const kvHub = document.getElementById('kv-hub');
const kvMistral = document.getElementById('kv-mistral');
const kvTts = document.getElementById('kv-tts');
const kvFish = document.getElementById('kv-fish');
const kvHa = document.getElementById('kv-ha');
const kvGemini = document.getElementById('kv-gemini');
const kvTime = document.getElementById('kv-time');

// Settings controls
const btnSettingsReload = document.getElementById('settings-reload');
const btnSettingsSave = document.getElementById('settings-save');
const setTimezone = document.getElementById('set-timezone');
const setLocation = document.getElementById('set-location');
const setUnits = document.getElementById('set-units');
const setRetrieval = document.getElementById('set-retrieval');
const setWorkingRules = document.getElementById('set-working-rules');
const setTechStack = document.getElementById('set-tech-stack');
const setHa = document.getElementById('set-ha');
const setMistral = document.getElementById('set-mistral');
const setFish = document.getElementById('set-fish');
const setWhisper = document.getElementById('set-whisper');
const setWhisperTimeout = document.getElementById('set-whisper-timeout');

// Fish advanced
const setFishTimeout = document.getElementById('set-fish-timeout');
const setFishFormat = document.getElementById('set-fish-format');
const setFishNormalize = document.getElementById('set-fish-normalize');
const setFishChunk = document.getElementById('set-fish-chunk');
const setFishMaxNewTokens = document.getElementById('set-fish-max-new-tokens');
const setFishTemperature = document.getElementById('set-fish-temperature');
const setFishTopP = document.getElementById('set-fish-top-p');
const setFishRepetitionPenalty = document.getElementById('set-fish-repetition-penalty');
const setFishRefDomino = document.getElementById('set-fish-ref-domino');
const setFishRefPenny = document.getElementById('set-fish-ref-penny');
const setFishRefJimmy = document.getElementById('set-fish-ref-jimmy');
const setTtsDomino = document.getElementById('set-tts-domino');
const setTtsPenny = document.getElementById('set-tts-penny');
const setTtsJimmy = document.getElementById('set-tts-jimmy');

// Diagnostics (read-only)
const diagStatus = document.getElementById('diag-status');
const diagMistralUrl = document.getElementById('diag-mistral-url');
const diagWhisperUrl = document.getElementById('diag-whisper-url');
const diagFishUrl = document.getElementById('diag-fish-url');
const diagHasOpenai = document.getElementById('diag-has-openai');
const diagTtsProvider = document.getElementById('diag-tts-provider');
const diagFishEnabled = document.getElementById('diag-fish-enabled');
const diagHaEnabled = document.getElementById('diag-ha-enabled');
const diagGeminiEnabled = document.getElementById('diag-gemini-enabled');

// Tests
const whisperTestFile = document.getElementById('whisper-test-file');
const whisperTestBtn = document.getElementById('whisper-test-btn');
const whisperTestOut = document.getElementById('whisper-test-out');

const fishTestText = document.getElementById('fish-test-text');
const fishTestPersona = document.getElementById('fish-test-persona');
const fishTestRef = document.getElementById('fish-test-ref');
const fishTestBtn = document.getElementById('fish-test-btn');

function qsBool(v) {
  return v ? 'true' : 'false';
}

function setStatus(text, level) {
  if (statusTextEl) statusTextEl.textContent = text || '';
  if (statusDotEl) {
    statusDotEl.classList.remove('ok', 'warn');
    if (level === 'ok') statusDotEl.classList.add('ok');
    if (level === 'warn') statusDotEl.classList.add('warn');
  }
}

function setActivePage(page) {
  const isChat = page === 'chat';
  if (pageChat) pageChat.classList.toggle('is-hidden', !isChat);
  if (pageSettings) pageSettings.classList.toggle('is-hidden', isChat);

  if (navChatBtn) navChatBtn.classList.toggle('is-active', isChat);
  if (navSettingsBtn) navSettingsBtn.classList.toggle('is-active', !isChat);
}

function safeJson(v) {
  try {
    return JSON.stringify(v);
  } catch (_) {
    return String(v);
  }
}

function clearChatUI() {
  if (chatEl) chatEl.textContent = '';
}

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

function setSessionId(newId) {
  try {
    const key = 'dominoHubSessionId';
    localStorage.setItem(key, newId);
  } catch (_) {}
}

function refreshSessionLabel() {
  const sid = getOrCreateSessionId();
  if (sessionLabelEl) sessionLabelEl.textContent = `Session: ${sid || 'default'}`;
}

// ---- persona + UI helpers ----
function getPersona() {
  return (personaEl && personaEl.value ? personaEl.value : 'auto');
}

function personaLabel(p) {
  const key = (p || '').toLowerCase();
  if (key === 'domino') return 'Domino';
  if (key === 'penny') return 'Penny';
  if (key === 'jimmy') return 'Jimmy';
  if (key === 'system') return 'System';
  if (key === 'user') return 'You';
  return 'Auto';
}

function appendMessage(role, text, persona, meta) {
  if (!chatEl) return;

  const div = document.createElement('div');
  div.classList.add('msg');

  const personaKey = (persona || '').toLowerCase();
  if (role === 'user') {
    div.classList.add('user');
  } else {
    div.classList.add(personaKey || 'domino');
  }

  const head = document.createElement('div');
  head.className = 'msg-head';
  const who = document.createElement('div');
  who.className = 'msg-who';
  who.textContent = role === 'user' ? 'You' : personaLabel(personaKey);
  head.appendChild(who);
  div.appendChild(head);

  const body = document.createElement('div');
  body.className = 'msg-body';
  body.textContent = text || '';
  div.appendChild(body);

  if (meta && meta.has_audio && meta.tts_provider) {
    const metaEl = document.createElement('div');
    metaEl.className = 'msg-meta';
    metaEl.textContent = `Audio: ${meta.tts_provider}`;
    div.appendChild(metaEl);
  }

  chatEl.appendChild(div);
  chatEl.scrollTop = chatEl.scrollHeight;
}

function appendSystem(text) {
  if (!chatEl) return;
  const div = document.createElement('div');
  div.classList.add('msg', 'system');

  const head = document.createElement('div');
  head.className = 'msg-head';
  const who = document.createElement('div');
  who.className = 'msg-who';
  who.textContent = 'System';
  head.appendChild(who);
  div.appendChild(head);

  const body = document.createElement('div');
  body.className = 'msg-body';
  body.textContent = text || '';
  div.appendChild(body);

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
    setStatus('Ready', 'ok');
  } else if (synthSupported) {
    setStatus('Loading voices…', 'warn');
  } else {
    setStatus('No browser TTS', 'warn');
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
    if (ttsInfo.tts_provider) setStatus(`Audio via ${ttsInfo.tts_provider}`, 'ok');
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
    setStatus('Listening…', 'warn');
  };
  recognizer.onend = () => {
    recognizing = false;
    micBtn.classList.remove('active');
    setStatus('Ready', 'ok');
  };
  recognizer.onresult = (event) => {
    const transcript = event.results[0][0].transcript;
    inputEl.value = transcript;
    sendMessage();
  };
  recognizer.onerror = () => {
    recognizing = false;
    micBtn.classList.remove('active');
    setStatus('Mic error', 'warn');
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

            setStatus('Transcribing…', 'warn');
            const resp = await fetch('/api/stt', { method: 'POST', body: fd });
            if (!resp.ok) {
              appendSystem(`STT error: ${resp.status} ${resp.statusText}`);
              setStatus('Ready', 'ok');
              return;
            }
            const data = await resp.json();
            const transcript = (data && data.text ? String(data.text) : '').trim();
            if (!transcript) {
              setStatus('No speech detected', 'warn');
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
            setStatus('Ready', 'ok');
          }
        };

        whisperRecording = true;
        micBtn.classList.add('active');
        setStatus('Recording…', 'warn');
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
      setStatus('Request failed', 'warn');
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
      setStatus('Ready', 'ok');
      return;
    }

    setStatus('Waiting for responses…', 'warn');

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
        setStatus(`Got ${seenPersonas.size} response(s)…`, 'warn');
        return;
      }

      if (eventName === 'audio') {
        if (payload.audio_id) {
          enqueueServerAudioId(payload.audio_id, payload.tts_provider, payload.mime);
          if (synthSupported) window.speechSynthesis.cancel();
          playNextInQueue();
          setStatus('Playing audio…', 'warn');
        }
        return;
      }

      if (eventName === 'error') {
        const p = (payload.persona || 'system').toLowerCase();
        appendSystem(`${p}: ${payload.error || 'unknown error'}`);
        return;
      }

      if (eventName === 'done') {
        setStatus('Ready', 'ok');
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
    refreshSessionLabel();
  }
}

sendBtn.addEventListener('click', sendMessage);

inputEl.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// -------------------------
// Settings + status
// -------------------------

function normalizeOptionalText(v) {
  const s = (v == null ? '' : String(v)).trim();
  return s ? s : null;
}

async function fetchJson(url, opts) {
  const resp = await fetch(url, opts);
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return await resp.json();
}

function fillPromotedState(state) {
  const promoted = state || {};
  if (setTimezone) setTimezone.value = promoted.timezone || '';
  if (setLocation) setLocation.value = promoted.location || '';
  if (setUnits) setUnits.value = promoted.preferred_units || '';
  if (setWorkingRules) setWorkingRules.value = promoted.working_rules || '';
  if (setTechStack) setTechStack.value = promoted.tech_stack || '';
  if (setRetrieval) setRetrieval.value = qsBool(!!promoted.retrieval_enabled);

  const baseUrls = promoted.base_urls || {};
  if (setHa) setHa.value = baseUrls.ha || '';
  if (setMistral) setMistral.value = baseUrls.mistral || '';
  if (setFish) setFish.value = baseUrls.fish || '';
  if (setWhisper) setWhisper.value = baseUrls.whisper || '';

  const whisper = promoted.whisper_stt || {};
  if (setWhisperTimeout) setWhisperTimeout.value = whisper.timeout_sec != null ? String(whisper.timeout_sec) : '';

  const fish = promoted.fish_tts || {};
  if (setFishTimeout) setFishTimeout.value = fish.timeout_sec != null ? String(fish.timeout_sec) : '';
  if (setFishFormat) setFishFormat.value = fish.format || 'wav';
  if (setFishNormalize) setFishNormalize.value = qsBool(fish.normalize !== false);
  if (setFishChunk) setFishChunk.value = fish.chunk_length != null ? String(fish.chunk_length) : '';
  if (setFishMaxNewTokens) setFishMaxNewTokens.value = fish.max_new_tokens != null ? String(fish.max_new_tokens) : '';
  if (setFishTemperature) setFishTemperature.value = fish.temperature != null ? String(fish.temperature) : '';
  if (setFishTopP) setFishTopP.value = fish.top_p != null ? String(fish.top_p) : '';
  if (setFishRepetitionPenalty) setFishRepetitionPenalty.value = fish.repetition_penalty != null ? String(fish.repetition_penalty) : '';

  const refs = fish.refs || {};
  if (setFishRefDomino) setFishRefDomino.value = refs.domino || '';
  if (setFishRefPenny) setFishRefPenny.value = refs.penny || '';
  if (setFishRefJimmy) setFishRefJimmy.value = refs.jimmy || '';

  const tts = promoted.tts_overrides || {};
  if (setTtsDomino) setTtsDomino.value = tts.domino || 'auto';
  if (setTtsPenny) setTtsPenny.value = tts.penny || 'auto';
  if (setTtsJimmy) setTtsJimmy.value = tts.jimmy || 'auto';
}

async function loadPromotedState() {
  const state = await fetchJson('/api/memory/promoted');
  fillPromotedState(state);
  return state;
}

function collectPromotedPatch() {
  const fishNormalizeVal = (setFishNormalize?.value || 'true') === 'true';
  const fishRefs = {
    domino: normalizeOptionalText(setFishRefDomino?.value),
    penny: normalizeOptionalText(setFishRefPenny?.value),
    jimmy: normalizeOptionalText(setFishRefJimmy?.value),
  };

  return {
    timezone: normalizeOptionalText(setTimezone?.value),
    location: normalizeOptionalText(setLocation?.value),
    preferred_units: normalizeOptionalText(setUnits?.value),
    working_rules: normalizeOptionalText(setWorkingRules?.value),
    tech_stack: normalizeOptionalText(setTechStack?.value),
    retrieval_enabled: (setRetrieval?.value || 'false') === 'true',
    tts_overrides: {
      domino: (setTtsDomino?.value || 'auto'),
      penny: (setTtsPenny?.value || 'auto'),
      jimmy: (setTtsJimmy?.value || 'auto'),
    },
    base_urls: {
      ha: normalizeOptionalText(setHa?.value),
      mistral: normalizeOptionalText(setMistral?.value),
      fish: normalizeOptionalText(setFish?.value),
      whisper: normalizeOptionalText(setWhisper?.value),
    },

    whisper_stt: {
      timeout_sec: normalizeOptionalText(setWhisperTimeout?.value) ? Number(setWhisperTimeout.value) : null,
    },

    fish_tts: {
      timeout_sec: normalizeOptionalText(setFishTimeout?.value) ? Number(setFishTimeout.value) : null,
      format: normalizeOptionalText(setFishFormat?.value),
      normalize: fishNormalizeVal,
      chunk_length: normalizeOptionalText(setFishChunk?.value) ? Number(setFishChunk.value) : null,
      max_new_tokens: normalizeOptionalText(setFishMaxNewTokens?.value) ? Number(setFishMaxNewTokens.value) : null,
      temperature: normalizeOptionalText(setFishTemperature?.value) ? Number(setFishTemperature.value) : null,
      top_p: normalizeOptionalText(setFishTopP?.value) ? Number(setFishTopP.value) : null,
      repetition_penalty: normalizeOptionalText(setFishRepetitionPenalty?.value) ? Number(setFishRepetitionPenalty.value) : null,
      refs: fishRefs,
    },
  };
}

async function savePromotedState() {
  const patch = collectPromotedPatch();
  const resp = await fetchJson('/api/memory/promoted', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  fillPromotedState(resp);
  return resp;
}

function fillHealth(health) {
  if (!health) return;
  const ok = health.status === 'ok';
  setStatus(ok ? 'Ready' : 'Degraded', ok ? 'ok' : 'warn');

  if (kvHub) kvHub.textContent = health.status || 'unknown';
  if (kvMistral) kvMistral.textContent = health.mistral_base_url || '—';
  if (kvTts) kvTts.textContent = health.tts_provider || '—';
  if (kvFish) kvFish.textContent = qsBool(!!health.fish_enabled);
  if (kvHa) kvHa.textContent = qsBool(!!health.ha_enabled);
  if (kvGemini) kvGemini.textContent = qsBool(!!health.gemini_enabled);

  if (diagStatus) diagStatus.textContent = health.status || '—';
  if (diagMistralUrl) diagMistralUrl.textContent = health.mistral_base_url || '—';
  if (diagWhisperUrl) diagWhisperUrl.textContent = health.whisper_base_url || '—';
  if (diagFishUrl) diagFishUrl.textContent = health.fish_base_url || '—';
  if (diagHasOpenai) diagHasOpenai.textContent = qsBool(!!health.has_openai);
  if (diagTtsProvider) diagTtsProvider.textContent = health.tts_provider || '—';
  if (diagFishEnabled) diagFishEnabled.textContent = qsBool(!!health.fish_enabled);
  if (diagHaEnabled) diagHaEnabled.textContent = qsBool(!!health.ha_enabled);
  if (diagGeminiEnabled) diagGeminiEnabled.textContent = qsBool(!!health.gemini_enabled);
}

async function testWhisperStt() {
  if (!whisperTestFile || !whisperTestOut) return;
  const f = whisperTestFile.files && whisperTestFile.files[0];
  if (!f) {
    whisperTestOut.value = 'Choose an audio file first.';
    return;
  }
  whisperTestOut.value = 'Transcribing…';
  try {
    const fd = new FormData();
    fd.append('file', f, f.name);
    const resp = await fetch('/api/stt', { method: 'POST', body: fd });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      whisperTestOut.value = data.detail ? String(data.detail) : `${resp.status} ${resp.statusText}`;
      return;
    }
    whisperTestOut.value = (data.text || '').trim() || '(no text returned)';
  } catch (e) {
    whisperTestOut.value = String(e);
  }
}

async function testFishTts() {
  if (!fishTestBtn || !fishTestText) return;
  const text = (fishTestText.value || '').trim();
  if (!text) {
    appendSystem('Fish test: enter some text first.');
    return;
  }
  const persona = (fishTestPersona?.value || 'domino').toLowerCase();
  const refOverride = normalizeOptionalText(fishTestRef?.value);
  try {
    fishTestBtn.disabled = true;
    setStatus('Fish TTS…', 'warn');
    const data = await fetchJson('/api/tts/test', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona, text, provider: 'fish', reference_id: refOverride }),
    });
    if (data && data.audio_id) {
      enqueueServerAudioId(data.audio_id, data.tts_provider || 'fish', data.mime);
      playNextInQueue();
      setStatus('Playing audio…', 'warn');
    } else {
      appendSystem('Fish test: no audio returned.');
      setStatus('Ready', 'ok');
    }
  } catch (e) {
    appendSystem(`Fish test failed: ${e}`);
    setStatus('Ready', 'ok');
  } finally {
    fishTestBtn.disabled = false;
  }
}

async function refreshStatus() {
  try {
    const health = await fetchJson('/health');
    fillHealth(health);
  } catch (e) {
    setStatus('Offline', 'warn');
    if (kvHub) kvHub.textContent = 'offline';
  }

  try {
    const sid = getOrCreateSessionId();
    const time = await fetchJson(`/api/time?session_id=${encodeURIComponent(sid || 'default')}`);
    if (kvTime) kvTime.textContent = time.display || time.iso || '—';
  } catch (_) {
    if (kvTime) kvTime.textContent = '—';
  }
}

// -------------------------
// Navigation + actions
// -------------------------

function wireNav() {
  if (navChatBtn) navChatBtn.addEventListener('click', () => setActivePage('chat'));
  if (navSettingsBtn) navSettingsBtn.addEventListener('click', () => setActivePage('settings'));
  if (btnJumpSettings) btnJumpSettings.addEventListener('click', () => setActivePage('settings'));

  if (btnRefreshStatus) btnRefreshStatus.addEventListener('click', () => refreshStatus());

  if (btnSettingsReload) {
    btnSettingsReload.addEventListener('click', async () => {
      try {
        setStatus('Reloading…', 'warn');
        await loadPromotedState();
        await refreshStatus();
        setStatus('Ready', 'ok');
      } catch (e) {
        appendSystem(`Settings reload failed: ${e}`);
        setStatus('Reload failed', 'warn');
      }
    });
  }

  if (btnSettingsSave) {
    btnSettingsSave.addEventListener('click', async () => {
      try {
        btnSettingsSave.disabled = true;
        setStatus('Saving…', 'warn');
        await savePromotedState();
        await refreshStatus();
        appendSystem('Saved promoted-state settings.');
        setStatus('Ready', 'ok');
      } catch (e) {
        appendSystem(`Settings save failed: ${e}`);
        setStatus('Save failed', 'warn');
      } finally {
        btnSettingsSave.disabled = false;
      }
    });
  }

  if (btnNewSession) {
    btnNewSession.addEventListener('click', () => {
      const newId = (crypto?.randomUUID ? crypto.randomUUID() : String(Date.now()) + '-' + String(Math.random()).slice(2));
      setSessionId(newId);
      refreshSessionLabel();
      appendSystem('Started a new session.');
    });
  }

  if (btnClearHistory) {
    btnClearHistory.addEventListener('click', async () => {
      try {
        const sid = getOrCreateSessionId();
        await fetchJson(`/api/memory/history/clear?session_id=${encodeURIComponent(sid || 'default')}`, { method: 'POST' });
        clearChatUI();
        appendSystem('Cleared chat history for this session.');
      } catch (e) {
        appendSystem(`Clear history failed: ${e}`);
      }
    });
  }

  if (whisperTestBtn) {
    whisperTestBtn.addEventListener('click', () => testWhisperStt());
  }

  if (fishTestBtn) {
    fishTestBtn.addEventListener('click', () => testFishTts());
  }
}

// -------------------------
// Boot
// -------------------------

wireNav();
setActivePage('chat');
refreshSessionLabel();

// Initialize status + settings in the background
(async () => {
  try {
    setStatus('Loading…', 'warn');
    await refreshStatus();
    await loadPromotedState();
    setStatus('Ready', 'ok');
  } catch (e) {
    appendSystem(`Init error: ${e}`);
    setStatus('Init failed', 'warn');
  }
})();
