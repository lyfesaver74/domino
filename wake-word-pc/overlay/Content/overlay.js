(() => {
  const rootEl = document.getElementById('root');
  const subtitleEl = document.getElementById('subtitle');
  const audioUnlockBtn = document.getElementById('audioUnlock');
  const audioErrEl = document.getElementById('audioErr');

  /** @type {WebSocket | null} */
  let socket = null;
  let reconnectTimer = null;

  /** @type {HTMLAudioElement | null} */
  let audioEl = null;
  let audioUnlocked = false;
  let pendingTts = null;

  /** @type {Array<{ text: string, color?: string, persona?: string }>} */
  const subtitleQueue = [];
  let subtitleLoopRunning = false;
  let lastAccent = '#00ffaa';

  function setAudioError(message) {
    if (!audioErrEl) return;
    if (!message) {
      audioErrEl.style.display = 'none';
      audioErrEl.textContent = '';
      return;
    }
    audioErrEl.style.display = 'block';
    audioErrEl.textContent = `Audio: ${message}`;
  }

  async function unlockAudio() {
    try {
      if (!audioEl) audioEl = new Audio();
      // A short silent WAV (base64) to satisfy gesture-locked audio stacks.
      audioEl.src = 'data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAESsAACJWAAACABAAZGF0YQAAAAA=';
      await audioEl.play();
      audioEl.pause();
      audioEl.currentTime = 0;
      audioUnlocked = true;
      if (audioUnlockBtn) audioUnlockBtn.style.display = 'none';
      setAudioError('');

      if (pendingTts) {
        const { format, audioB64 } = pendingTts;
        pendingTts = null;
        playTTSAudio(format, audioB64);
      }
    } catch (e) {
      audioUnlocked = false;
      const msg = (e && e.message) ? e.message : String(e || 'play() blocked');
      setAudioError(msg);
    }
  }

  function mimeForFormat(fmt) {
    const f = String(fmt || '').toLowerCase();
    if (f === 'wav' || f === 'wave') return 'audio/wav';
    if (f === 'mp3' || f === 'mpeg') return 'audio/mpeg';
    if (f === 'ogg') return 'audio/ogg';
    if (f === 'flac') return 'audio/flac';
    return 'application/octet-stream';
  }

  function playTTSAudio(format, audioB64) {
    if (!audioB64) return;

    // Don’t even attempt play() until we have a user gesture.
    // This avoids spamming autoplay errors in normal browsers.
    if (!audioUnlocked) {
      pendingTts = { format, audioB64 };
      if (audioUnlockBtn) audioUnlockBtn.style.display = 'inline-block';
      setAudioError('Click Enable Audio to allow playback.');
      return;
    }

    const mime = mimeForFormat(format);
    const url = `data:${mime};base64,${audioB64}`;

    try {
      if (!audioEl) audioEl = new Audio();
      audioEl.src = url;
      audioEl.currentTime = 0;
      // Autoplay is often blocked in normal browsers unless we have a user gesture.
      const p = audioEl.play();
      if (p && typeof p.catch === 'function') {
        p.catch((e) => {
          const msg = (e && e.message) ? e.message : String(e || 'play() blocked');
          setAudioError(msg);
          if (audioUnlockBtn) audioUnlockBtn.style.display = 'inline-block';
        });
      }
    } catch {
      if (audioUnlockBtn) audioUnlockBtn.style.display = 'inline-block';
    }
  }

  function setActive(active) {
    if (!rootEl) return;
    if (active) {
      rootEl.classList.remove('idle');
      rootEl.classList.add('active');
    } else {
      rootEl.classList.remove('active');
      rootEl.classList.add('idle');
    }
  }

  function setAccent(color) {
    const c = (color || '').trim();
    if (!c) return;
    lastAccent = c;
    if (rootEl) {
      rootEl.style.setProperty('--accent', c);
      // Slightly opaque background dim while active
      rootEl.style.setProperty('--dim', 'rgba(0, 0, 0, 0.26)');
    }
  }

  function splitIntoSubtitleBlocks(text) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    if (!t) return [];

    // Start with sentence-ish splits, then re-pack into readable blocks.
    const parts = t.split(/(?<=[.!?])\s+/g).filter(Boolean);
    const blocks = [];
    let buf = '';
    const maxChars = 92;

    for (const p of parts.length ? parts : [t]) {
      if (!buf) {
        buf = p;
        continue;
      }
      if ((buf + ' ' + p).length <= maxChars) {
        buf = buf + ' ' + p;
      } else {
        blocks.push(buf);
        buf = p;
      }
    }
    if (buf) blocks.push(buf);

    // If still too long (no punctuation case), hard-wrap by words.
    const finalBlocks = [];
    for (const b of blocks) {
      if (b.length <= maxChars + 25) {
        finalBlocks.push(b);
        continue;
      }
      const words = b.split(' ');
      let line = '';
      for (const w of words) {
        const candidate = line ? (line + ' ' + w) : w;
        if (candidate.length <= maxChars) {
          line = candidate;
        } else {
          if (line) finalBlocks.push(line);
          line = w;
        }
      }
      if (line) finalBlocks.push(line);
    }

    return finalBlocks;
  }

  function enqueueSubtitles(text, color, persona) {
    const blocks = splitIntoSubtitleBlocks(text);
    if (!blocks.length) return;
    for (const b of blocks) {
      subtitleQueue.push({ text: b, color: color || undefined, persona: persona || undefined });
    }
    runSubtitleLoop();
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function runSubtitleLoop() {
    if (subtitleLoopRunning) return;
    subtitleLoopRunning = true;
    try {
      while (subtitleQueue.length) {
        const item = subtitleQueue.shift();
        if (!item) continue;

        setAccent(item.color || lastAccent);
        setActive(true);

        if (subtitleEl) {
          subtitleEl.textContent = item.text || '';
          subtitleEl.classList.remove('hide');
          // Force reflow so transitions apply reliably
          void subtitleEl.offsetHeight;
          subtitleEl.classList.add('show');
        }

        const len = (item.text || '').length;
        // Subtitle pacing: quick in/out with a slightly cinematic feel.
        const holdMs = Math.max(1400, Math.min(5200, 900 + len * 38));
        await sleep(holdMs);

        if (subtitleEl) {
          subtitleEl.classList.remove('show');
          subtitleEl.classList.add('hide');
        }
        await sleep(260);
      }
    } finally {
      subtitleLoopRunning = false;
      // Clear text and go idle shortly after last block.
      if (subtitleEl) {
        subtitleEl.textContent = '';
        subtitleEl.classList.remove('show');
        subtitleEl.classList.remove('hide');
      }
      setActive(false);
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 800);
  }

  function connect() {
    const url = 'ws://127.0.0.1:8765/ws';

    try {
      socket = new WebSocket(url);
    } catch (e) {
      setActive(false);
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      // Stay idle until we have content to display.
      setActive(false);
    };

    socket.onclose = () => {
      setActive(false);
      scheduleReconnect();
    };

    socket.onerror = () => {
      setActive(false);
    };

    socket.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }

      if (!msg || typeof msg.type !== 'string') return;

      if (msg.type === 'status') {
        // Only use status as a hint for accent color; keep the overlay visually quiet unless speaking.
        setAccent(msg.color || lastAccent);
        return;
      }

      if (msg.type === 'assistant_reply') {
        setAccent(msg.color || lastAccent);
        enqueueSubtitles(msg.text || '', msg.color, msg.persona);
        return;
      }

      if (msg.type === 'tts_audio') {
        setAccent(msg.color || lastAccent);
        playTTSAudio(msg.format, msg.audio_b64);
        return;
      }

      if (msg.type === 'error') {
        // If an error arrives, briefly show it as a subtitle so it is visible.
        enqueueSubtitles(msg.message || 'Error', '#FFFFFF', 'System');
        return;
      }
    };
  }

  if (audioUnlockBtn) {
    audioUnlockBtn.addEventListener('click', () => {
      unlockAudio();
    });
  }

  connect();
})();
