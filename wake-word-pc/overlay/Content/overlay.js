(() => {
  const connEl = document.getElementById('conn');
  const statusEl = document.getElementById('status');
  const hintEl = document.getElementById('hint');
  const tickerEl = document.getElementById('ticker');
  const audioUnlockBtn = document.getElementById('audioUnlock');
  const audioErrEl = document.getElementById('audioErr');

  /** @type {WebSocket | null} */
  let socket = null;
  let reconnectTimer = null;

  /** @type {HTMLAudioElement | null} */
  let audioEl = null;
  let audioUnlocked = false;
  let pendingTts = null;

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

  function setConn(state) {
    connEl.textContent = state;
  }

  function setStatus(state, hint, color) {
    statusEl.textContent = `status: ${state || '—'}`;
    hintEl.textContent = hint || '—';
    if (color) {
      statusEl.style.color = color;
      hintEl.style.color = color;
    } else {
      statusEl.style.color = 'white';
      hintEl.style.color = 'white';
    }
  }

  function appendTickerLine(label, text, color) {
    const line = document.createElement('div');
    line.className = 'ticker-line';
    line.style.color = color || 'white';
    line.textContent = `${label}: ${text}`;
    tickerEl.appendChild(line);

    // Keep last ~6 lines.
    while (tickerEl.children.length > 6) {
      tickerEl.removeChild(tickerEl.firstChild);
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
      setConn('OFFLINE');
      setStatus('error', 'WebSocket init failed', '#FFFFFF');
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      setConn('ONLINE');
      setStatus('listening', 'ONLINE', '#FFFFFF');
    };

    socket.onclose = () => {
      setConn('OFFLINE');
      setStatus('error', 'Disconnected', '#FFFFFF');
      scheduleReconnect();
    };

    socket.onerror = () => {
      setConn('OFFLINE');
      setStatus('error', 'Socket error', '#FFFFFF');
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
        setStatus(msg.state, msg.hint, msg.color);
        return;
      }

      if (msg.type === 'assistant_reply') {
        appendTickerLine(msg.persona || 'Assistant', msg.text || '', msg.color);
        return;
      }

      if (msg.type === 'tts_audio') {
        playTTSAudio(msg.format, msg.audio_b64);
        return;
      }

      if (msg.type === 'error') {
        setStatus('error', msg.message || 'Error', '#FFFFFF');
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
