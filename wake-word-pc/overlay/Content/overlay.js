(() => {
  const rootEl = document.getElementById('root');
  const kittEl = document.getElementById('kitt');
  const subtitleEl = document.getElementById('subtitle');

  /** @type {{
    *  animStyle: 'blocks' | 'wave' | 'kitt',
   *  colGap: number,
   *  segGap: number,
   *  glow: number,
   *  idleGlow: number,
   *  backdrop: number,
   *  subSize: number,
   *  subOffset: number,
   *  subShadow: number,
   * }} */
  const overlaySettings = {
    // Default to KITT-scan; settings.html can override.
    animStyle: 'kitt',
    colGap: 24,
    segGap: 4,
    glow: 18,
    idleGlow: 12,
    backdrop: 0.26,
    subSize: 36,
    subOffset: 0,
    subShadow: 70,
  };

  /** @type {WebSocket | null} */
  let socket = null;
  let reconnectTimer = null;

  /** @type {Array<{ text: string, color?: string, persona?: string }>} */
  const subtitleQueue = [];
  let subtitleLoopRunning = false;
  let lastAccent = '#00ffaa';

  const COLS = 7;
  // Use an odd segment count so there's a true single center segment.
  const SEGS = 17;
  /** @type {HTMLElement[][]} */
  const segEls = [];
  /** @type {HTMLElement | null} */
  let centerCoreEl = null;
  let kittAnimRunning = false;
  let kittAnimActiveUntil = 0;
  let statusIdleTimer = null;
  let lastMessageAt = 0;
  let wsState = 'init';
  let statusState = 'listening';
  let debugEnabled = false;
  let lastDebugPostAt = 0;
  let lastDebugText = '';

  // Simulation: smooth envelope + speech-like cadence.
  let simEnergy = 0;
  let simEnergyTarget = 0;
  /** @type {Array<{ ms: number, target: number }>} */
  let simPattern = [];
  let simPatternIdx = 0;
  let simPatternRemainingS = 0;
  let simLastTickS = 0;

  function setConnected(connected) {
    if (!rootEl) return;
    if (connected) rootEl.classList.add('connected');
    else rootEl.classList.remove('connected');
  }

  function getWebSocketUrl() {
    // Default assumes overlay runs on the same machine as the wake-word app.
    // If Streamer.bot runs on a different PC, pass a URL like:
    //   file:///.../index.html?ws=ws://SURFACE_IP:8765/ws
    // Or set window.DOMINO_WS_URL before loading overlay.js.
    try {
      const forced = (window.DOMINO_WS_URL && String(window.DOMINO_WS_URL).trim()) || '';
      if (forced) return forced;

      const params = new URLSearchParams(window.location.search || '');
      const ws = (params.get('ws') || '').trim();
      if (ws) return ws;
    } catch (_) {
      // ignore
    }
    return 'ws://127.0.0.1:8765/ws';
  }

  function clamp(v, a, b) {
    return Math.max(a, Math.min(b, v));
  }

  function parseNumber(v) {
    // URLSearchParams.get() returns null when a key is missing.
    // Number(null) === 0, which would incorrectly override settings to 0.
    if (v === null || typeof v === 'undefined') return null;
    if (typeof v === 'string' && v.trim() === '') return null;
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }

  function readSettingsFromUrl() {
    /** @type {Partial<typeof overlaySettings>} */
    const out = {};
    try {
      const params = new URLSearchParams(window.location.search || '');
      const style = String(params.get('style') || '').trim().toLowerCase();
      if (style === 'kitt' || style === 'blocks' || style === 'wave') out.animStyle = style;

      const colGap = parseNumber(params.get('colGap'));
      if (colGap !== null) out.colGap = colGap;
      const segGap = parseNumber(params.get('segGap'));
      if (segGap !== null) out.segGap = segGap;
      const glow = parseNumber(params.get('glow'));
      if (glow !== null) out.glow = glow;
      const idleGlow = parseNumber(params.get('idleGlow'));
      if (idleGlow !== null) out.idleGlow = idleGlow;
      const backdrop = parseNumber(params.get('backdrop'));
      if (backdrop !== null) out.backdrop = backdrop;
      const subSize = parseNumber(params.get('subSize'));
      if (subSize !== null) out.subSize = subSize;
      const subOffset = parseNumber(params.get('subOffset'));
      if (subOffset !== null) out.subOffset = subOffset;
      const subShadow = parseNumber(params.get('subShadow'));
      if (subShadow !== null) out.subShadow = subShadow;
    } catch (_) {
      // ignore
    }
    return out;
  }

  function readSettingsFromStorage() {
    /** @type {Partial<typeof overlaySettings>} */
    const out = {};
    try {
      const raw = localStorage.getItem('dominoOverlaySettings');
      if (!raw) return out;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') return out;
      if (parsed.animStyle === 'kitt' || parsed.animStyle === 'blocks' || parsed.animStyle === 'wave') out.animStyle = parsed.animStyle;
      if (typeof parsed.colGap === 'number') out.colGap = parsed.colGap;
      if (typeof parsed.segGap === 'number') out.segGap = parsed.segGap;
      if (typeof parsed.glow === 'number') out.glow = parsed.glow;
      if (typeof parsed.idleGlow === 'number') out.idleGlow = parsed.idleGlow;
      if (typeof parsed.backdrop === 'number') out.backdrop = parsed.backdrop;
      if (typeof parsed.subSize === 'number') out.subSize = parsed.subSize;
      if (typeof parsed.subOffset === 'number') out.subOffset = parsed.subOffset;
      if (typeof parsed.subShadow === 'number') out.subShadow = parsed.subShadow;
    } catch (_) {
      // ignore
    }
    return out;
  }

  function readSettingsFromUserSettings() {
    /** @type {Partial<typeof overlaySettings>} */
    const out = {};
    try {
      const src = window.DOMINO_OVERLAY_SETTINGS;
      if (!src || typeof src !== 'object') return out;
      if (src.animStyle === 'kitt' || src.animStyle === 'blocks' || src.animStyle === 'wave') out.animStyle = src.animStyle;
      if (typeof src.colGap === 'number') out.colGap = src.colGap;
      if (typeof src.segGap === 'number') out.segGap = src.segGap;
      if (typeof src.glow === 'number') out.glow = src.glow;
      if (typeof src.idleGlow === 'number') out.idleGlow = src.idleGlow;
      if (typeof src.backdrop === 'number') out.backdrop = src.backdrop;
      if (typeof src.subSize === 'number') out.subSize = src.subSize;
      if (typeof src.subOffset === 'number') out.subOffset = src.subOffset;
      if (typeof src.subShadow === 'number') out.subShadow = src.subShadow;
    } catch (_) {
      // ignore
    }
    return out;
  }

  function applySubtitleShadow(strength01) {
    if (!subtitleEl) return;
    const m = clamp(strength01, 0, 1);
    if (m <= 0.01) {
      subtitleEl.style.textShadow = 'none';
      return;
    }
    const a = (v) => clamp(v * m, 0, 0.95).toFixed(3);
    subtitleEl.style.textShadow = [
      `0 1px 0 rgba(0,0,0,${a(0.70)})`,
      `0 2px 0 rgba(0,0,0,${a(0.68)})`,
      `0 3px 0 rgba(0,0,0,${a(0.66)})`,
      `0 6px 12px rgba(0,0,0,${a(0.62)})`,
      `0 12px 26px rgba(0,0,0,${a(0.55)})`,
      `0 22px 48px rgba(0,0,0,${a(0.42)})`,
    ].join(', ');
  }

  function applyOverlaySettings(partial) {
    const s = partial || {};
    if (s.animStyle === 'kitt' || s.animStyle === 'blocks' || s.animStyle === 'wave') overlaySettings.animStyle = s.animStyle;
    if (typeof s.colGap === 'number') overlaySettings.colGap = clamp(s.colGap, 10, 80);
    if (typeof s.segGap === 'number') overlaySettings.segGap = clamp(s.segGap, 0, 16);
    if (typeof s.glow === 'number') overlaySettings.glow = clamp(s.glow, 0, 60);
    if (typeof s.idleGlow === 'number') overlaySettings.idleGlow = clamp(s.idleGlow, 0, 60);
    if (typeof s.backdrop === 'number') overlaySettings.backdrop = clamp(s.backdrop, 0, 0.75);
    if (typeof s.subSize === 'number') overlaySettings.subSize = clamp(s.subSize, 14, 96);
    if (typeof s.subOffset === 'number') overlaySettings.subOffset = clamp(s.subOffset, -140, 220);
    if (typeof s.subShadow === 'number') overlaySettings.subShadow = clamp(s.subShadow, 0, 100);

    if (rootEl) {
      rootEl.style.setProperty('--col-gap', `${Math.round(overlaySettings.colGap)}px`);
      rootEl.style.setProperty('--seg-gap', `${Math.round(overlaySettings.segGap)}px`);

      // Glow is expressed as two blur radii.
      const g2 = Math.round(overlaySettings.glow);
      const g1 = Math.max(0, Math.round(g2 * 0.55));
      rootEl.style.setProperty('--glow1', `${g1}px`);
      rootEl.style.setProperty('--glow2', `${g2}px`);

      rootEl.style.setProperty('--dim', `rgba(0, 0, 0, ${overlaySettings.backdrop.toFixed(3)})`);
      rootEl.style.setProperty('--sub-offset', `${Math.round(overlaySettings.subOffset)}px`);
    }

    if (subtitleEl) {
      subtitleEl.style.fontSize = `${Math.round(overlaySettings.subSize)}px`;
      applySubtitleShadow(overlaySettings.subShadow / 100);
    }
  }

  function isDebugEnabled() {
    try {
      if (window.DOMINO_DEBUG) return true;
      const params = new URLSearchParams(window.location.search || '');
      const v = (params.get('debug') || '').trim();
      return v === '1' || v.toLowerCase() === 'true';
    } catch (_) {
      return false;
    }
  }

  function isSimEnabled() {
    // Dev-only visual tuning mode.
    // Enable via window.DOMINO_SIMULATE = true or ?sim=1
    try {
      if (window.DOMINO_SIMULATE === false) return false;
      if (window.DOMINO_SIMULATE) return true;
      const params = new URLSearchParams(window.location.search || '');
      const v = (params.get('sim') || '').trim();
      return v === '1' || v.toLowerCase() === 'true';
    } catch (_) {
      return false;
    }
  }

  function startSimulation() {
    wsState = 'sim';
    setConnected(true);
    // In sim mode we want styling changes (including backdrop) to be visible.
    // Use backdrop=0 as the "no dim" escape hatch.
    setActive(overlaySettings.backdrop > 0.001);
    ensureKittBuilt();
    applyHostScaleWorkaround();

    startKittAnim();

    // Keep the anim loop "active" so we can blend baseline smoothly.
    kittAnimActiveUntil = Date.now() + 60 * 60 * 1000;

    // Simulate realistic cadence with pauses:
    // "Hi. My name is, Domino." -> rest, spike, rest/spike, spike, spike, half rest, spike, spike, spike
    simPattern = [
      { ms: 520, target: 0.00 },
      { ms: 260, target: 1.00 }, // Hi
      { ms: 260, target: 0.00 }, // .

      { ms: 180, target: 0.55 }, // My
      { ms: 160, target: 0.15 },
      { ms: 210, target: 0.85 }, // name
      { ms: 140, target: 0.40 },
      { ms: 170, target: 0.70 }, // is,
      { ms: 220, target: 0.35 }, // short comma pause (half rest)

      { ms: 190, target: 0.80 }, // Do-
      { ms: 190, target: 0.90 }, // mi-
      { ms: 240, target: 1.00 }, // no
      { ms: 520, target: 0.00 }, // . and reset
    ];
    simPatternIdx = 0;
    simPatternRemainingS = (simPattern[0].ms || 0) / 1000;
    simEnergyTarget = simPattern[0].target;
    simEnergy = 0;
    simLastTickS = performance.now() / 1000;

    // Keep the text stable so you can judge motion.
    if (subtitleEl) {
      subtitleEl.textContent = 'Hi. My name is, Domino.';
      subtitleEl.classList.remove('hide');
      void subtitleEl.offsetHeight;
      subtitleEl.classList.add('show');
      setAccent(lastAccent);
    }

    const lines = [
      'Domino online. Calibrating overlay visuals…',
      'This is simulated subtitle text for styling.',
      'Surface Pro 7 target: 2736×1824 (3:2).',
      'Adjust font size, spacing, and glow until it feels right.',
      'Once it looks good, disable DOMINO_SIMULATE.',
    ];
    let i = 0;

    function showLine() {
      const text = lines[i % lines.length];
      i += 1;
      if (!subtitleEl) return;
      subtitleEl.textContent = text;
      subtitleEl.classList.remove('hide');
      // Force reflow so transitions apply reliably
      void subtitleEl.offsetHeight;
      subtitleEl.classList.add('show');
      setAccent(lastAccent);
      // Pulse the "speaking" animation, then fall back to the single-center baseline.
      pokeKittActive(1700);
    }

    showLine();
    window.setInterval(showLine, 2600);
  }

  function emitDebugText(text) {
    if (!debugEnabled) return;
    const t = String(text || '');
    const now = Date.now();
    // Throttle to avoid spamming the host / preview UI.
    if (t === lastDebugText && now - lastDebugPostAt < 350) return;
    if (now - lastDebugPostAt < 120) return;
    lastDebugText = t;
    lastDebugPostAt = now;

    // If we're inside settings.html's iframe preview, send debug there.
    try {
      if (window.parent && window.parent !== window && window.parent.postMessage) {
        window.parent.postMessage({ type: 'domino_overlay_debug', text: t }, '*');
      }
    } catch (_) {
      // ignore
    }
  }

  /** @type {HTMLElement | null} */
  let debugHudEl = null;

  function ensureDebugHud() {
    if (!debugEnabled) return;
    if (debugHudEl) return;
    if (!rootEl) return;

    const el = document.createElement('pre');
    el.id = 'debugHud';
    el.style.position = 'fixed';
    el.style.left = '10px';
    el.style.top = '10px';
    el.style.zIndex = '9999';
    el.style.margin = '0';
    el.style.padding = '8px 10px';
    el.style.whiteSpace = 'pre-wrap';
    el.style.font = '12px/1.2 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace';
    el.style.color = 'rgba(255,255,255,0.92)';
    el.style.background = 'rgba(0,0,0,0.55)';
    el.style.border = '1px solid rgba(255,255,255,0.14)';
    el.style.borderRadius = '6px';
    el.style.pointerEvents = 'none';
    rootEl.appendChild(el);
    debugHudEl = el;
  }

  function updateDebugHud() {
    if (!debugEnabled) return;
    ensureDebugHud();
    if (!debugHudEl) return;

    let computedFont = '';
    try {
      if (subtitleEl) computedFont = window.getComputedStyle(subtitleEl).fontSize || '';
    } catch (_) {
      computedFont = '';
    }

    let fromUser = '';
    try {
      fromUser = window.DOMINO_OVERLAY_SETTINGS ? JSON.stringify(window.DOMINO_OVERLAY_SETTINGS) : '';
    } catch (_) {
      fromUser = '(unserializable)';
    }

    debugHudEl.textContent = [
      'DOMINO_DEBUG HUD',
      `simFlag: ${String(window.DOMINO_SIMULATE)}`,
      `dbgFlag: ${String(window.DOMINO_DEBUG)}`,
      `overlay.subSize: ${String(overlaySettings.subSize)}px`,
      `subtitle.computedFontSize: ${computedFont}`,
      `subtitle.text: ${(subtitleEl && subtitleEl.textContent) ? String(subtitleEl.textContent).slice(0, 80) : ''}`,
      `userSettings: ${fromUser}`,
    ].join('\n');
  }

  function applyHostScaleWorkaround() {
    // Streamer.bot / CEF overlay hosts sometimes render at a fractional devicePixelRatio
    // (or otherwise report a viewport that doesn't match the physical output), causing
    // content to appear tiny in the top-left.
    //
    // We do a best-effort transform scale on the root based on a few signals.
    try {
      if (!rootEl) return;

      const vw = Math.max(
        window.innerWidth || 0,
        (document.documentElement && document.documentElement.clientWidth) || 0,
        (window.visualViewport && window.visualViewport.width) || 0
      );
      const vh = Math.max(
        window.innerHeight || 0,
        (document.documentElement && document.documentElement.clientHeight) || 0,
        (window.visualViewport && window.visualViewport.height) || 0
      );
      if (!vw || !vh) return;

      const sw = (window.screen && (window.screen.availWidth || window.screen.width)) || 0;
      const sh = (window.screen && (window.screen.availHeight || window.screen.height)) || 0;

      let scale = 1;
      let rootScale = 1;
      let centerScale = 1;

      const dpr = Number(window.devicePixelRatio || 1) || 1;

      // If the host reports a tiny viewport but a normal screen size, scale to match.
      // Use CSS pixels for comparison: screen pixels / dpr.
      let sx = 1;
      let sy = 1;
      if (sw > 0 && sh > 0 && vw > 0 && vh > 0) {
        const swCss = sw / dpr;
        const shCss = sh / dpr;
        sx = swCss / vw;
        sy = shCss / vh;
        const s = Math.min(sx, sy);

        // Treat it as "broken" if the viewport is substantially smaller than the screen.
        // Also bias towards applying when the reported viewport is unusually tiny.
        const viewportLooksTiny = (vw < 900 || vh < 650);
        if ((viewportLooksTiny && s > 1.08) || s > 1.25) {
          scale = Math.max(scale, s);
        }
      }

      // If the viewport is normal-sized but the overlay is designed for 1080p,
      // scale the CENTERED HUD so it remains readable on 1440p/4K.
      // IMPORTANT: do NOT apply this by scaling #root (which is already 100vw/100vh)
      // or you'd push centered content off-screen.
      const baseW = 1920;
      const baseH = 1080;
      const baseScale = Math.min(vw / baseW, vh / baseH);

      // Only scale up; never shrink.
      if (baseScale > 1.05 && baseScale < 6) {
        centerScale = baseScale;
      }

      // Only apply when it looks obviously wrong.
      if (scale > 1.05 && scale < 12) {
        rootScale = scale;
        rootEl.style.transformOrigin = '0 0';
        rootEl.style.transform = `scale(${scale})`;
      } else {
        rootScale = 1;
        rootEl.style.transform = '';
        rootEl.style.transformOrigin = '';
      }

      // If we applied a root scale (tiny viewport host bug), don't also center-scale.
      if (rootScale > 1.01) centerScale = 1;
      rootEl.style.setProperty('--center-scale', String(centerScale));

      if (debugEnabled) {
        const simFlag = (typeof window.DOMINO_SIMULATE === 'undefined') ? 'undefined' : String(window.DOMINO_SIMULATE);
        let href = '';
        try { href = String(window.location && window.location.href ? window.location.href : ''); } catch (_) { href = ''; }
        if (href.length > 110) href = href.slice(0, 110) + '…';

        let subRect = '';
        try {
          if (subtitleEl && subtitleEl.getBoundingClientRect) {
            const r = subtitleEl.getBoundingClientRect();
            subRect = `${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}`;
          }
        } catch (_) {
          subRect = '';
        }

        emitDebugText(
          [
            `ws: ${wsState}`,
            `lastMsg: ${lastMessageAt ? (Date.now() - lastMessageAt) + 'ms ago' : 'never'}`,
            `sim: ${isSimEnabled() ? 'on' : 'off'} flag=${simFlag}`,
            `url: ${href}`,
            `vw/vh: ${Math.round(vw)}x${Math.round(vh)} dpr=${dpr}`,
            `sw/sh: ${sw}x${sh}`,
            `sx/sy: ${sx.toFixed(3)} ${sy.toFixed(3)}`,
            `centerScale: ${centerScale.toFixed(3)} (base=${baseScale.toFixed(3)})`,
            `scale: ${rootEl.style.transform ? rootEl.style.transform : 'none'}`,
            subRect ? `subtitle: ${subRect}` : 'subtitle: (none)',
          ].join('\n')
        );
      }
    } catch (_) {
      // best-effort
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

  function setStatusClasses(state) {
    if (!rootEl) return;
    const s = String(state || '').trim().toLowerCase();
    statusState = s || 'listening';

    // Keep class list small and explicit.
    rootEl.classList.toggle('state-recording', s === 'recording');
    rootEl.classList.toggle('state-thinking', s === 'thinking');
    // Clear the phase marker; it will be recomputed by the animation loop.
    rootEl.classList.remove('rest-phase');
  }

  function setStatusState(state) {
    const s = String(state || '').trim().toLowerCase();
    if (statusIdleTimer) {
      clearTimeout(statusIdleTimer);
      statusIdleTimer = null;
    }

    setStatusClasses(s);

    // Show the overlay while we're doing anything other than idle listening.
    if (s && s !== 'listening') {
      setActive(true);
      pokeKittActive(1800);
      return;
    }

    // Return to idle shortly after listening resumes (unless subtitles are still showing).
    statusIdleTimer = setTimeout(() => {
      statusIdleTimer = null;
      if (!subtitleLoopRunning) setActive(false);
    }, 350);
  }

  function setAccent(color) {
    const c = (color || '').trim();
    if (!c) return;
    lastAccent = c;
    if (rootEl) {
      rootEl.style.setProperty('--accent', c);
    }
  }

  function ensureKittBuilt() {
    if (!kittEl) return;
    if (segEls.length) return;
    kittEl.innerHTML = '';

    const centerCol = 3;
    const centerSeg = Math.round((SEGS - 1) / 2);

    for (let c = 0; c < COLS; c++) {
      const col = document.createElement('div');
      col.className = 'col';
      // Used by CSS to sample a single wide horizontal gradient across the whole bar.
      const pct = COLS > 1 ? (c / (COLS - 1)) * 100 : 50;
      col.style.setProperty('--col-pos', `${pct.toFixed(4)}%`);
      const segs = [];

      // Top-to-bottom order visually; we flip in activation logic.
      for (let s = 0; s < SEGS; s++) {
        const seg = document.createElement('div');
        seg.className = 'seg';
        if (c === centerCol && s === centerSeg) {
          seg.classList.add('center-core');
          centerCoreEl = seg;
        }
        seg.style.setProperty('--p', '0');
        col.appendChild(seg);
        segs.push(seg);
      }

      kittEl.appendChild(col);
      segEls.push(segs);
    }
  }

  function clamp01(v) {
    return Math.max(0, Math.min(1, v));
  }

  function setSegIntensity(seg, p) {
    const v = clamp01(p);
    seg.style.setProperty('--p', v.toFixed(4));
    if (v >= 0.995) seg.classList.add('on');
    else seg.classList.remove('on');
  }

  function renderBlocks(levels) {
    // Discrete on/off blocks, expanding from center.
    for (let c = 0; c < COLS; c++) {
      const level = clamp01(levels[c] || 0);
      const taper = [0.60, 0.75, 0.90, 1.10, 0.90, 0.75, 0.60][c] || 1.0;
      const nOn = Math.min(SEGS, Math.round(SEGS * level * taper));
      const segs = segEls[c];
      if (!segs) continue;

      const center = (SEGS - 1) / 2;
      const half = Math.floor(nOn / 2);
      const extra = (nOn % 2);
      const lo = Math.ceil(center - half);
      const hi = Math.floor(center + half + extra);

      for (let i = 0; i < SEGS; i++) {
        const on = (i >= lo && i <= hi && nOn > 0) ? 1 : 0;
        setSegIntensity(segs[i], on);
      }
    }
  }

  function renderClassicKitt(levels, opts) {
    const taperArr = [0.55, 0.70, 0.86, 1.20, 0.86, 0.70, 0.55];
    const idle = !!(opts && opts.idle);
    const idleCenterSeg = Math.round((SEGS - 1) / 2);
    const baseline = (opts && typeof opts.baseline === 'number') ? clamp01(opts.baseline) : (idle ? 1 : 0);

    for (let c = 0; c < COLS; c++) {
      const segs = segEls[c];
      if (!segs) continue;

      // Center column should stand out and be vertically asymmetric (top taller than bottom).
      // Keep the peak centered; make the top extend further via gentler falloff above.
      const centerBias = 0;
      const center = (SEGS - 1) / 2 + centerBias;

      const taper = taperArr[c] || 1.0;
      const a = clamp01((levels[c] || 0) * taper);

      const dc = Math.abs(c - 3);
      // Keep idle glow strictly within cols 2..4.
      const idleColAllowed = (c >= 2 && c <= 4);
      const idleColAtt = idleColAllowed ? (dc === 0 ? 1 : 0.55) : 0;

      // Spread grows with activity.
      const spread = 0.9 + a * 7.8;
      const falloffBase = 1.0 / spread;
      // IMPORTANT: when a column's level is 0 it must be completely dark.
      // Avoid a fixed brightness floor; it makes "near silent" show extra lit segments.
      const intensity = idle ? 0 : (a <= 0 ? 0 : a);

      for (let i = 0; i < SEGS; i++) {
        const above = i < center;
        const falloff = (c === 3)
          // IMPORTANT: smaller falloff => slower decay => taller.
          // Make ABOVE decay slower than BELOW.
          ? (falloffBase / (above ? 1.40 : 0.74))
          // For non-center columns, keep the top a bit tighter,
          // but match the center column's weaker bottom by using the same BELOW divisor.
          : (falloffBase / (above ? 0.86 : 0.74));
        const d = Math.abs(i - center);

        // Active energy: a smooth triangle-ish kernel.
        let p = 0;
        if (!idle) {
          const k = clamp01(1 - d * falloff);
          p = intensity * (k * k);
        }

        // Baseline during pauses: show a single center block, smoothly blended.
        let pIdle = 0;
        if (baseline > 0) {
          pIdle = (c === 3 && i === idleCenterSeg) ? baseline : 0;
        }

        setSegIntensity(segs[i], Math.max(p, pIdle));
      }
    }
  }

  function pokeKittActive(ms) {
    const until = Date.now() + (ms || 1200);
    if (until > kittAnimActiveUntil) kittAnimActiveUntil = until;
    startKittAnim();
  }

  function startKittAnim() {
    if (kittAnimRunning) return;
    ensureKittBuilt();
    if (!segEls.length) return;

    kittAnimRunning = true;
    const start = performance.now();

    let lastCoreGlowKey = '';
    function setCenterCoreGlowFromG2(g2) {
      if (!centerCoreEl) return;
      const g2n = Math.round(clamp(g2, 0, 60));
      const g1n = Math.max(0, Math.round(g2n * 0.55));
      const key = `${g1n}:${g2n}`;
      if (key === lastCoreGlowKey) return;
      lastCoreGlowKey = key;
      centerCoreEl.style.setProperty('--core-glow1', `${g1n}px`);
      centerCoreEl.style.setProperty('--core-glow2', `${g2n}px`);
    }

    function clearCenterCoreGlowOverride() {
      if (!centerCoreEl) return;
      if (!lastCoreGlowKey) return;
      lastCoreGlowKey = '';
      centerCoreEl.style.removeProperty('--core-glow1');
      centerCoreEl.style.removeProperty('--core-glow2');
    }

    const tick = (t) => {
      const now = Date.now();
      const active = now < kittAnimActiveUntil;
      const connected = !!(rootEl && rootEl.classList.contains('connected'));

      if (rootEl) rootEl.classList.remove('rest-phase');

      // During recording/thinking, keep the bar in the single-center-block rest pose.
      // This makes the effects obvious and prevents the whole stack from "pulsing".
      if (rootEl && overlaySettings.animStyle === 'kitt') {
        const inRecording = rootEl.classList.contains('state-recording');
        const inThinking = rootEl.classList.contains('state-thinking');
        if (inRecording || inThinking) {
          const dt = (t - start) / 1000;
          rootEl.classList.add('rest-phase');

          // Recording: pulse brightness + center-only glow.
          if (inRecording) {
            const pulseB = 0.78 + 0.22 * (0.5 + 0.5 * Math.sin(dt * 2 * Math.PI * 1.55));
            const pulseG = 0.82 + 0.48 * (0.5 + 0.5 * Math.sin(dt * 2 * Math.PI * 1.25));
            setCenterCoreGlowFromG2(overlaySettings.glow * pulseG);
            renderClassicKitt([0,0,0,0,0,0,0], { idle: false, baseline: clamp01(pulseB) });
          } else {
            // Thinking: steady brightness, no extra glow override.
            clearCenterCoreGlowOverride();
            renderClassicKitt([0,0,0,0,0,0,0], { idle: false, baseline: 1 });
          }

          requestAnimationFrame(tick);
          return;
        }
      }
      if (!active && !subtitleLoopRunning && !connected) {
        clearCenterCoreGlowOverride();
        // When disconnected, keep non-blocks styles showing the single resting segment
        // instead of going fully dark between spikes.
        if (overlaySettings.animStyle !== 'blocks') {
          renderClassicKitt([0,0,0,0,0,0,0], { idle: true });
          requestAnimationFrame(tick);
          return;
        }

        // Blocks style: clear display and stop.
        renderBlocks([0,0,0,0,0,0,0]);
        kittAnimRunning = false;
        return;
      }

      if (!active && connected) {
        clearCenterCoreGlowOverride();
        if (overlaySettings.animStyle === 'blocks') {
          // Subtle idle pulse while connected (no backdrop dim).
          const dtIdle = (t - start) / 1000;
          const base = 0.10 + 0.06 * (0.5 + 0.5 * Math.sin(dtIdle * 1.6));
          const levels = [
            base * 0.70,
            base * 0.85,
            base * 0.95,
            base * 1.05,
            base * 0.95,
            base * 0.85,
            base * 0.70,
          ];
          renderBlocks(levels);
        } else {
          // Wave/KITT-scan: keep a fixed center glow in idle.
          renderClassicKitt([0,0,0,0,0,0,0], { idle: true });
        }
        requestAnimationFrame(tick);
        return;
      }

      const dt = (t - start) / 1000;

      // Update simulation envelope if enabled.
      if (wsState === 'sim' && simPattern.length) {
        const nowS = t / 1000;
        const dS = simLastTickS ? Math.max(0, nowS - simLastTickS) : 0;
        simLastTickS = nowS;

        // Step pattern timers.
        simPatternRemainingS -= dS;
        while (simPatternRemainingS <= 0 && simPattern.length) {
          simPatternIdx = (simPatternIdx + 1) % simPattern.length;
          const step = simPattern[simPatternIdx];
          simEnergyTarget = clamp01(step.target);
          simPatternRemainingS += Math.max(0.03, (step.ms || 0) / 1000);
        }

        // Smoothly approach target: fast attack, slower release.
        const rate = simEnergyTarget > simEnergy ? 14.0 : 7.0;
        const a = 1 - Math.exp(-rate * dS);
        simEnergy = simEnergy + (simEnergyTarget - simEnergy) * a;
      }

      if (overlaySettings.animStyle === 'kitt') {
        // KITT-style dashboard voicebox:
        // starts in the middle and expands outward, mirrored.
        // Use the full 7-column display (0..6) from center outward.
        const maxR = 3; // distance 0..3 -> cols 0..6 can light
        const speed = 1.55; // cycles/sec

        // Drive both horizontal expansion and vertical height from the SAME value
        // so they peak at the same time.
        const cycle = (dt * speed) % (2 * maxR);
        const rCycle = cycle <= maxR ? cycle : (2 * maxR - cycle);
        const e = (wsState === 'sim') ? clamp01(simEnergy) : clamp01(rCycle / maxR);
        const e2 = e * e;
        const e3 = e2 * e;
        const r = maxR * e;

        // Soft edge so the expansion doesn't look like hard steps.
        const edgeSoft = 0.60;
        const base = 0.02;

        const smoothstep01 = (x) => {
          const u = clamp01(x);
          return u * u * (3 - 2 * u);
        };

        const levels = [];
        for (let c = 0; c < COLS; c++) {
          const dc = Math.abs(c - 3);
          if (dc > maxR) {
            levels.push(0);
            continue;
          }

          // Fill from center outward: columns within radius r are "on".
          // 1 when dc <= r - edgeSoft, 0 when dc >= r + edgeSoft.
          const u = (r - dc + edgeSoft) / (2 * edgeSoft);
          const fill = smoothstep01(u);

          // Slight center emphasis so the middle feels like the "origin".
          const centerEmphasis = 1 - 0.08 * dc;
          // As energy falls, smoothly collapse all animated intensity to zero.
          // Keep outer columns steeper (e^3) to avoid extra lit segments near rest,
          // but let the CENTER decay a bit slower (e^2) so it doesn't fade out before rest.
          const fall = (dc === 0) ? e2 : e3;
          const lvl = clamp01((base + (1 - base) * fill) * centerEmphasis) * fall;
          levels.push(lvl);
        }

        // Resting state: as energy -> 0, blend UP a single center segment smoothly.
        // This guarantees the true rest look without any discrete "mode switch".
        // Keep the center "rest" segment present at the bottom of the cycle
        // in both sim and live modes, so it never fades out then jumps back.
        // Blend the single rest segment in earlier to avoid a "dark gap" at the tail.
        const baseline = 1 - smoothstep01(e / 0.28);
        const restPhase = baseline > 0.82;
        if (rootEl) rootEl.classList.toggle('rest-phase', restPhase);

        // No per-state overrides here anymore; recording/thinking are handled
        // by the force-rest branch above.
        clearCenterCoreGlowOverride();
        renderClassicKitt(levels, { idle: false, baseline });
      } else if (overlaySettings.animStyle === 'wave') {
        clearCenterCoreGlowOverride();
        // Wave: previous "talking" motion (kept as an option).
        const e = clamp01(0.10 + 0.90 * (0.55 + 0.45 * Math.sin(dt * 6.2)));
        const e2 = clamp01(0.30 + 0.70 * (0.55 + 0.45 * Math.sin(dt * 3.1 + 0.8)));
        const energy = clamp01(0.15 + 0.85 * (0.62 * e + 0.38 * e2));
        const shape = [0.22, 0.42, 0.70, 1.0, 0.70, 0.42, 0.22];
        const levels = [];
        for (let c = 0; c < COLS; c++) {
          const wobble = 0.96 + 0.08 * Math.sin(dt * 8.7 + (c - 3) * 0.55);
          levels.push(clamp01(energy * (shape[c] || 0.2) * wobble));
        }
        renderClassicKitt(levels, { idle: false });
      } else {
        clearCenterCoreGlowOverride();
        // Blocks: bouncy, slightly phase-shifted.
        const base = 0.55 + 0.45 * Math.sin(dt * 5.2);
        const levels = [];
        for (let c = 0; c < COLS; c++) {
          const phase = (c - 3) * 0.42;
          const wobble = 0.5 + 0.5 * Math.sin(dt * 6.4 + phase);
          const wobble2 = 0.5 + 0.5 * Math.sin(dt * 3.3 + phase * 0.7);
          const lvl = clamp01(0.15 + 0.85 * (0.55 * wobble + 0.45 * wobble2) * (0.65 + 0.35 * base));
          levels.push(lvl);
        }
        renderBlocks(levels);
      }

      requestAnimationFrame(tick);
    };

    requestAnimationFrame(tick);
  }

  function splitIntoSubtitleBlocks(text) {
    const t = String(text || '').replace(/\s+/g, ' ').trim();
    if (!t) return [];

    // Start with sentence-ish splits, then re-pack into readable blocks.
    // NOTE: avoid regex lookbehind for older embedded Chromium/CEF builds.
    const raw = t.split(/([.!?])\s+/g).filter(Boolean);
    const parts = [];
    for (let i = 0; i < raw.length; i++) {
      const chunk = raw[i];
      if (!chunk) continue;
      // If this token is punctuation, append it to the previous sentence.
      if (chunk === '.' || chunk === '!' || chunk === '?') {
        if (parts.length) parts[parts.length - 1] = parts[parts.length - 1] + chunk;
        else parts.push(chunk);
        continue;
      }
      parts.push(chunk);
    }
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
        pokeKittActive(2400);

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
    const url = getWebSocketUrl();

    try {
      socket = new WebSocket(url);
    } catch (e) {
      setActive(false);
      scheduleReconnect();
      return;
    }

    socket.onopen = () => {
      try { console.log('[overlay] ws open', url); } catch {}
      wsState = 'open';
      setConnected(true);
      // Stay idle until we have content to display.
      setActive(false);
      ensureKittBuilt();
      applyHostScaleWorkaround();

      // Keep a subtle idle animation running when connected.
      startKittAnim();

      // With debug enabled, keep a subtle activity indicator visible.
      if (debugEnabled) {
        setActive(true);
        pokeKittActive(900);
      }
    };

    socket.onclose = () => {
      try { console.log('[overlay] ws close', url); } catch {}
      wsState = 'closed';
      setConnected(false);
      setActive(false);
      scheduleReconnect();
    };

    socket.onerror = () => {
      try { console.log('[overlay] ws error', url); } catch {}
      wsState = 'error';
      setConnected(false);
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

      lastMessageAt = Date.now();
      if (debugEnabled) applyHostScaleWorkaround();

      if (msg.type === 'status') {
        // Status is mostly a state machine signal; don't let the default "white" idle color
        // overwrite the persona accent (it makes the whole overlay look stuck in white).
        setStatusState(msg.state);
        const c = String(msg.color || '').trim();
        if (c && c.toLowerCase() !== '#ffffff' && c.toLowerCase() !== 'white') {
          setAccent(c);
        }
        return;
      }

      if (msg.type === 'assistant_reply') {
        setAccent(msg.color || lastAccent);
        enqueueSubtitles(msg.text || '', msg.color, msg.persona);
        pokeKittActive(3200);
        return;
      }

      if (msg.type === 'tts_audio') {
        setAccent(msg.color || lastAccent);
        // Overlay is click-through; do not attempt audio playback here.
        // Use this event only to keep visuals active.
        pokeKittActive(3000);
        return;
      }

      if (msg.type === 'error') {
        // If an error arrives, briefly show it as a subtitle so it is visible.
        enqueueSubtitles(msg.message || 'Error', '#FFFFFF', 'System');
        return;
      }
    };
  }

  window.addEventListener('resize', () => {
    applyHostScaleWorkaround();
  });

  // Live settings from settings.html preview.
  window.addEventListener('message', (ev) => {
    try {
      const data = ev && ev.data;
      if (!data || data.type !== 'domino_overlay_settings') return;
      if (!data.settings || typeof data.settings !== 'object') return;
      applyOverlaySettings(data.settings);
    } catch (_) {
      // ignore
    }
  });

  // Apply once at startup too.
  debugEnabled = isDebugEnabled();
  // Settings precedence:
  //   defaults -> (storage only if no user-settings) -> user-settings.js -> URL params
  // Rationale: the settings UI uses localStorage for its own state, but the *overlay*
  // should treat user-settings.js as the authoritative persisted config.
  const userOverrides = readSettingsFromUserSettings();
  const hasUserOverrides = Object.keys(userOverrides).length > 0;
  if (!hasUserOverrides) applyOverlaySettings(readSettingsFromStorage());
  applyOverlaySettings(userOverrides);
  applyOverlaySettings(readSettingsFromUrl());
  applyHostScaleWorkaround();

  // Convenience: when debug is on, default simulation to ON unless explicitly disabled.
  // This makes visual tuning predictable in desktop overlay hosts.
  try {
    if (debugEnabled && typeof window.DOMINO_SIMULATE === 'undefined') window.DOMINO_SIMULATE = true;
  } catch (_) {
    // ignore
  }

  if (isSimEnabled()) {
    startSimulation();
  } else {
    connect();
  }
})();
