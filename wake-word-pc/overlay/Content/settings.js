(() => {
  'use strict';

  const els = {
    frame: /** @type {HTMLIFrameElement|null} */ (document.getElementById('frame')),
    previewUrl: /** @type {HTMLInputElement|null} */ (document.getElementById('previewUrl')),
    btnCopyUrl: document.getElementById('btnCopyUrl'),
    btnReset: document.getElementById('btnReset'),
    btnSave: document.getElementById('btnSave'),

    previewSim: /** @type {HTMLInputElement|null} */ (document.getElementById('previewSim')),
    previewDebug: /** @type {HTMLInputElement|null} */ (document.getElementById('previewDebug')),

    animStyle: /** @type {HTMLSelectElement|null} */ (document.getElementById('animStyle')),

    colGap: /** @type {HTMLInputElement|null} */ (document.getElementById('colGap')),
    colGapVal: document.getElementById('colGapVal'),

    segGap: /** @type {HTMLInputElement|null} */ (document.getElementById('segGap')),
    segGapVal: document.getElementById('segGapVal'),

    glow: /** @type {HTMLInputElement|null} */ (document.getElementById('glow')),
    glowVal: document.getElementById('glowVal'),

    idleGlow: /** @type {HTMLInputElement|null} */ (document.getElementById('idleGlow')),
    idleGlowVal: document.getElementById('idleGlowVal'),

    backdrop: /** @type {HTMLInputElement|null} */ (document.getElementById('backdrop')),
    backdropVal: document.getElementById('backdropVal'),

    subSize: /** @type {HTMLInputElement|null} */ (document.getElementById('subSize')),
    subSizeVal: document.getElementById('subSizeVal'),

    subOffset: /** @type {HTMLInputElement|null} */ (document.getElementById('subOffset')),
    subOffsetVal: document.getElementById('subOffsetVal'),

    subShadow: /** @type {HTMLInputElement|null} */ (document.getElementById('subShadow')),
    subShadowVal: document.getElementById('subShadowVal'),

    debugText: document.getElementById('debugText'),
  };

  const DEFAULTS = {
    animStyle: 'kitt',
    colGap: 24,
    segGap: 4,
    glow: 18,
    idleGlow: 12,
    backdrop: 0.26,
    subSize: 36,
    subOffset: 0,
    subShadow: 70,

    // These two controls exist on the settings page and are meaningful for the real overlay too.
    // They map to overlay.js's window.DOMINO_SIMULATE / window.DOMINO_DEBUG.
    previewSim: true,
    previewDebug: true,
  };

  function clamp(v, a, b) {
    return Math.max(a, Math.min(b, v));
  }

  function readState() {
    try {
      const raw = localStorage.getItem('dominoOverlaySettings');
      if (!raw) return { ...DEFAULTS };
      const parsed = JSON.parse(raw);
      return { ...DEFAULTS, ...(parsed || {}) };
    } catch {
      return { ...DEFAULTS };
    }
  }

  function writeState(state) {
    try {
      localStorage.setItem('dominoOverlaySettings', JSON.stringify(state));
    } catch {
      // ignore
    }
  }

  function setUiFromState(state) {
    if (els.animStyle) els.animStyle.value = String(state.animStyle || DEFAULTS.animStyle);

    if (els.previewSim) els.previewSim.checked = !!(state.previewSim ?? DEFAULTS.previewSim);
    if (els.previewDebug) els.previewDebug.checked = !!(state.previewDebug ?? DEFAULTS.previewDebug);

    if (els.colGap) els.colGap.value = String(state.colGap ?? DEFAULTS.colGap);
    if (els.segGap) els.segGap.value = String(state.segGap ?? DEFAULTS.segGap);
    if (els.glow) els.glow.value = String(state.glow ?? DEFAULTS.glow);
    if (els.idleGlow) els.idleGlow.value = String(state.idleGlow ?? DEFAULTS.idleGlow);
    if (els.backdrop) els.backdrop.value = String(state.backdrop ?? DEFAULTS.backdrop);
    if (els.subSize) els.subSize.value = String(state.subSize ?? DEFAULTS.subSize);
    if (els.subOffset) els.subOffset.value = String(state.subOffset ?? DEFAULTS.subOffset);
    if (els.subShadow) els.subShadow.value = String(state.subShadow ?? DEFAULTS.subShadow);
  }

  function stateFromUi() {
    const state = { ...DEFAULTS };
    if (els.animStyle) state.animStyle = els.animStyle.value || DEFAULTS.animStyle;

    state.previewSim = !!(els.previewSim?.checked);
    state.previewDebug = !!(els.previewDebug?.checked);

    state.colGap = clamp(Number(els.colGap?.value ?? DEFAULTS.colGap), 10, 60);
    state.segGap = clamp(Number(els.segGap?.value ?? DEFAULTS.segGap), 0, 12);
    state.glow = clamp(Number(els.glow?.value ?? DEFAULTS.glow), 0, 40);
    state.idleGlow = clamp(Number(els.idleGlow?.value ?? DEFAULTS.idleGlow), 0, 35);
    state.backdrop = clamp(Number(els.backdrop?.value ?? DEFAULTS.backdrop), 0, 0.55);
    state.subSize = clamp(Number(els.subSize?.value ?? DEFAULTS.subSize), 18, 72);
    state.subOffset = clamp(Number(els.subOffset?.value ?? DEFAULTS.subOffset), -80, 120);
    state.subShadow = clamp(Number(els.subShadow?.value ?? DEFAULTS.subShadow), 0, 100);

    return state;
  }

  function fmtPx(n) {
    return `${Math.round(n)}px`;
  }

  function updateValueLabels(state) {
    if (els.colGapVal) els.colGapVal.textContent = fmtPx(state.colGap);
    if (els.segGapVal) els.segGapVal.textContent = fmtPx(state.segGap);
    if (els.glowVal) els.glowVal.textContent = fmtPx(state.glow);
    if (els.idleGlowVal) els.idleGlowVal.textContent = fmtPx(state.idleGlow);
    if (els.backdropVal) els.backdropVal.textContent = String(Number(state.backdrop).toFixed(2));
    if (els.subSizeVal) els.subSizeVal.textContent = fmtPx(state.subSize);
    if (els.subOffsetVal) els.subOffsetVal.textContent = fmtPx(state.subOffset);
    if (els.subShadowVal) els.subShadowVal.textContent = `${Math.round(state.subShadow)}%`;
  }

  function buildPreviewUrl(state) {
    const url = new URL('index.html', window.location.href);

    if (state.previewSim) url.searchParams.set('sim', '1');
    if (state.previewDebug) url.searchParams.set('debug', '1');

    url.searchParams.set('style', state.animStyle);
    url.searchParams.set('colGap', String(state.colGap));
    url.searchParams.set('segGap', String(state.segGap));
    url.searchParams.set('glow', String(state.glow));
    url.searchParams.set('idleGlow', String(state.idleGlow));
    url.searchParams.set('backdrop', String(state.backdrop));
    url.searchParams.set('subSize', String(state.subSize));
    url.searchParams.set('subOffset', String(state.subOffset));
    url.searchParams.set('subShadow', String(state.subShadow));

    return url.toString();
  }

  function jsNumber(n) {
    const v = Number(n);
    return Number.isFinite(v) ? v : 0;
  }

  function buildUserSettingsJs(state) {
    const sim = !!state.previewSim;
    const dbg = !!state.previewDebug;
    return [
      '// Auto-generated user overrides for the Domino overlay.',
      '//',
      '// Replace overlay/Content/user-settings.js with this file, then restart HtmlWindowsOverlay.exe',
      '',
      `window.DOMINO_SIMULATE = ${sim ? 'true' : 'false'};`,
      `window.DOMINO_DEBUG = ${dbg ? 'true' : 'false'};`,
      '',
      'window.DOMINO_OVERLAY_SETTINGS = {',
      `  animStyle: ${JSON.stringify(String(state.animStyle || DEFAULTS.animStyle))},`,
      `  colGap: ${jsNumber(state.colGap)},`,
      `  segGap: ${jsNumber(state.segGap)},`,
      `  glow: ${jsNumber(state.glow)},`,
      `  idleGlow: ${jsNumber(state.idleGlow)},`,
      `  backdrop: ${jsNumber(state.backdrop)},`,
      `  subSize: ${jsNumber(state.subSize)},`,
      `  subOffset: ${jsNumber(state.subOffset)},`,
      `  subShadow: ${jsNumber(state.subShadow)},`,
      '};',
      '',
    ].join('\n');
  }

  function downloadTextFile(filename, contents) {
    const blob = new Blob([contents], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(() => URL.revokeObjectURL(url), 250);
  }

  function saveUserSettingsToDisk(state) {
    downloadTextFile('user-settings.js', buildUserSettingsJs(state));
  }

  function postSettingsToFrame(state) {
    if (!els.frame || !els.frame.contentWindow) return;
    els.frame.contentWindow.postMessage(
      {
        type: 'domino_overlay_settings',
        settings: state,
      },
      '*'
    );
  }

  function sync(state, opts = { reloadFrame: false }) {
    writeState(state);
    updateValueLabels(state);

    const previewUrl = buildPreviewUrl(state);
    if (els.previewUrl) els.previewUrl.value = previewUrl;

    if (els.frame) {
      if (!els.frame.src || opts.reloadFrame) {
        els.frame.src = previewUrl;
      } else {
        postSettingsToFrame(state);
      }
    }
  }

  function hook(el, onChange) {
    if (!el) return;
    el.addEventListener('input', onChange);
    el.addEventListener('change', onChange);
  }

  let state = readState();
  setUiFromState(state);
  sync(state, { reloadFrame: true });

  const onAnyChange = () => {
    state = stateFromUi();
    sync(state, { reloadFrame: false });
  };

  hook(els.animStyle, () => {
    state = stateFromUi();
    // animation style changes are easier with a reload
    sync(state, { reloadFrame: true });
  });

  // Preview controls: always reload the iframe when they change
  hook(els.previewSim, () => {
    state = stateFromUi();
    sync(state, { reloadFrame: true });
  });
  hook(els.previewDebug, () => {
    state = stateFromUi();
    sync(state, { reloadFrame: true });
  });

  hook(els.colGap, onAnyChange);
  hook(els.segGap, onAnyChange);
  hook(els.glow, onAnyChange);
  hook(els.idleGlow, onAnyChange);
  hook(els.backdrop, onAnyChange);
  hook(els.subSize, onAnyChange);
  hook(els.subOffset, onAnyChange);
  hook(els.subShadow, onAnyChange);

  els.btnReset?.addEventListener('click', () => {
    state = { ...DEFAULTS };
    setUiFromState(state);
    sync(state, { reloadFrame: true });
  });

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      try {
        const ta = document.createElement('textarea');
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand('copy');
        document.body.removeChild(ta);
        return true;
      } catch {
        return false;
      }
    }
  }

  els.btnCopyUrl?.addEventListener('click', async () => {
    const url = els.previewUrl?.value || buildPreviewUrl(state);
    await copyText(url);
  });

  els.btnSave?.addEventListener('click', () => {
    saveUserSettingsToDisk(state);
  });

  window.addEventListener('message', (ev) => {
    try {
      // Only accept messages from the preview iframe.
      if (!els.frame || !els.frame.contentWindow || ev.source !== els.frame.contentWindow) return;
      const data = ev.data;
      if (!data || data.type !== 'domino_overlay_debug') return;
      if (!els.debugText) return;
      els.debugText.textContent = String(data.text || '');
    } catch {
      // ignore
    }
  });
})();
