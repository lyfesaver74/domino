(() => {
  const $ = (id) => document.getElementById(id);

  const toast = (msg) => {
    const t = $("toast");
    t.textContent = msg;
    t.scrollTop = t.scrollHeight;
  };

  const apiFetch = async (path, opts = {}) => {
    const res = await fetch("/api" + path, {
      ...opts,
      headers: {
        Accept: "application/json",
        ...(opts.headers || {}),
      },
    });
    return res;
  };

  let lastBlob = null;
  let lastExt = "wav";

  async function checkHealth() {
    try {
      const res = await apiFetch("/v1/health");
      const pill = $("statusPill");
      if (res.ok) {
        pill.textContent = "API OK";
        pill.className = "pill ok";
        return true;
      }
      pill.textContent = "API error (" + res.status + ")";
      pill.className = "pill bad";
      return false;
    } catch {
      const pill = $("statusPill");
      pill.textContent = "API unreachable";
      pill.className = "pill bad";
      return false;
    }
  }

  async function refreshRefs() {
    toast("Refreshing reference list…");
    const list = $("refList");
    list.innerHTML = "";

    const sel = $("ttsRef");
    const keep = sel.value;
    sel.innerHTML = '<option value="">(none)</option>';

    const res = await apiFetch("/v1/references/list");
    const ct = res.headers.get("content-type") || "";
    let data = null;

    try {
      // Most builds will honor Accept: application/json
      if (ct.includes("application/json")) {
        data = await res.json();
      } else {
        // Fallback: try json anyway
        data = await res.json();
      }
    } catch {
      toast(
        "Could not parse list response as JSON. If your server forces msgpack, we can add a msgpack decoder.\nStatus: " +
          res.status,
      );
      return;
    }

    // data could be { reference_ids: [...] } or just [...]
    const ids = Array.isArray(data)
      ? data
      : data.reference_ids || data.references || data.items || [];

    if (!Array.isArray(ids)) {
      toast("Unexpected list format:\n" + JSON.stringify(data, null, 2));
      return;
    }

    if (ids.length === 0) {
      list.innerHTML = '<div class="small">No references yet.</div>';
    } else {
      for (const id of ids) {
        const div = document.createElement("div");
        div.className = "item";
        div.innerHTML = `
          <div>
            <b>${id}</b><div class="meta">reference_id</div>
          </div>
          <div class="row row-actions">
            <button class="secondary" data-use="${id}">Use</button>
            <button class="danger" data-del="${id}">Delete</button>
          </div>
        `;
        list.appendChild(div);

        const opt = document.createElement("option");
        opt.value = id;
        opt.textContent = id;
        sel.appendChild(opt);
      }
    }

    // restore selection if possible
    if (keep && [...sel.options].some((o) => o.value === keep)) sel.value = keep;

    // wire buttons
    list.querySelectorAll("[data-use]").forEach((btn) => {
      btn.addEventListener("click", () => {
        $("ttsRef").value = btn.getAttribute("data-use");
        toast("Selected reference_id: " + $("ttsRef").value);
      });
    });

    list.querySelectorAll("[data-del]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const id = btn.getAttribute("data-del");
        if (!confirm(`Delete reference "${id}"?`)) return;
        await deleteRef(id);
      });
    });

    toast("Reference list updated (" + ids.length + ").");
  }

  async function addRef() {
    const id = $("addId").value.trim();
    const file = $("addAudio").files?.[0];
    const text = $("addText").value.trim();

    if (!id) return toast("Add reference: missing ID.");
    if (!file) return toast("Add reference: please choose an audio file.");
    if (!text) return toast("Add reference: transcript is required.");

    toast(`Uploading reference "${id}"…`);

    const fd = new FormData();
    fd.append("id", id);
    fd.append("audio", file);
    fd.append("text", text);

    const res = await fetch("/api/v1/references/add", {
      method: "POST",
      body: fd,
      headers: { Accept: "application/json" },
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      toast(`Add failed (${res.status}).\n${body}`);
      return;
    }

    // server might respond json or msgpack; just show something
    const bodyText = await res.text().catch(() => "(ok)");
    toast(`Added "${id}" ✅\n${bodyText}`);

    await refreshRefs();
  }

  async function deleteRef(id) {
    if (!id) return;

    toast(`Deleting "${id}"…`);

    // Fish server expects the reference_id in the request body.
    // Try JSON first; fall back to form-urlencoded if the server rejects JSON.
    try {
      let res = await fetch("/api/v1/references/delete", {
        method: "DELETE",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ reference_id: id }),
      });

      if (!res.ok) {
        res = await fetch("/api/v1/references/delete", {
          method: "DELETE",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: `reference_id=${encodeURIComponent(id)}`,
        });
      }

      if (!res.ok) {
        const text = await res.text().catch(() => "");
        throw new Error(`Delete failed (${res.status}). ${text}`);
      }

      toast(`Deleted "${id}" ✅`);
      await refreshRefs();
    } catch (e) {
      toast(`Delete failed ❌ ${e?.message || e}`);
    }
  }

  async function speak() {
    const text = $("ttsText").value.trim();
    const reference_id = $("ttsRef").value || null;
    const format = $("ttsFormat").value;
    const normalize = $("ttsNormalize").value === "true";

    if (!text) return toast("TTS: text is empty.");

    const payload = {
      text,
      format,
      reference_id,
      references: [],
      normalize,
      streaming: false,
    };

    toast("Generating audio…");

    const res = await fetch("/api/v1/tts", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "audio/wav, application/octet-stream",
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => "");
      toast(`TTS failed (${res.status}).\n${body}`);
      return;
    }

    const blob = await res.blob();
    lastBlob = blob;
    lastExt = format;

    const url = URL.createObjectURL(blob);
    const player = $("player");
    player.src = url;
    player.play().catch(() => {});
    $("btnDownload").disabled = false;

    toast(
      `Audio ready ✅ (${Math.round(blob.size / 1024)} KB)\nreference_id=${reference_id ?? "(none)"}`,
    );
  }

  function downloadLast() {
    if (!lastBlob) return;
    const a = document.createElement("a");
    const url = URL.createObjectURL(lastBlob);
    a.href = url;
    a.download = "fish_tts." + (lastExt || "wav");
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 3000);
  }

  document.addEventListener("DOMContentLoaded", () => {
    $("btnRefresh").addEventListener("click", refreshRefs);
    $("btnHealth").addEventListener("click", async () => {
      await checkHealth();
      toast("Health checked.");
    });
    $("btnAdd").addEventListener("click", addRef);
    $("btnClearAdd").addEventListener("click", () => {
      $("addId").value = "";
      $("addText").value = "";
      $("addAudio").value = "";
      toast("Cleared.");
    });
    $("btnSpeak").addEventListener("click", speak);
    $("btnDownload").addEventListener("click", downloadLast);

    (async () => {
      const ok = await checkHealth();
      if (ok) await refreshRefs();
      else toast("API unreachable. Make sure fish-speech-server is up and Nginx proxy is configured.");
    })();
  });
})();
