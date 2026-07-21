/* Self-contained player — no npm at runtime. hls.min.js is vendored next to this file. */
const $ = (id) => document.getElementById(id);

const el = {
  station: $("station-name"),
  host: $("host-line"),
  kind: $("kind-badge"),
  title: $("title"),
  meta: $("meta-line"),
  status: $("status-line"),
  health: $("health-line"),
  queue: $("queue-list"),
  play: $("btn-play"),
  stop: $("btn-stop"),
  volume: $("volume"),
  player: $("player"),
  liveDot: $("live-dot"),
};

let hls = null;
let streamAttached = false;

async function api(path, opts) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.detail?.message || data?.detail || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

function setVolume() {
  el.player.volume = Number(el.volume.value);
}

function attachStream() {
  const playlist = "/stream/playlist.m3u8";
  const wav = "/stream/current.wav";

  if (hls) {
    hls.destroy();
    hls = null;
  }

  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      manifestLoadingMaxRetry: 6,
      levelLoadingMaxRetry: 6,
    });
    hls.loadSource(playlist);
    hls.attachMedia(el.player);
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) {
        console.warn("HLS fatal, trying WAV", data);
        el.player.src = wav;
        el.player.play().catch(() => {});
      }
    });
    el.player.play().catch(() => {});
    streamAttached = true;
    return;
  }

  if (el.player.canPlayType("application/vnd.apple.mpegurl")) {
    el.player.src = playlist;
    el.player.play().catch(() => {});
    streamAttached = true;
    return;
  }

  el.player.src = wav;
  el.player.play().catch(() => {});
  streamAttached = true;
}

function detachStream() {
  if (hls) {
    hls.destroy();
    hls = null;
  }
  el.player.pause();
  el.player.removeAttribute("src");
  el.player.load();
  streamAttached = false;
}

function renderNow(now) {
  const state = now.state || "stopped";
  el.liveDot.classList.toggle("live", state === "playing");

  if (state === "buffering") {
    el.status.textContent = now.buffering_message || "Buffering…";
  } else if (state === "playing") {
    el.status.textContent = "On air";
  } else {
    el.status.textContent = "Stopped";
  }

  const seg = now.segment;
  if (seg) {
    el.kind.textContent = seg.kind === "song" ? "Song" : "Talk";
    el.kind.className = "badge " + (seg.kind || "");
    el.title.textContent = seg.title || "—";
    const bits = [];
    if (seg.genre_id) bits.push(seg.genre_id.replaceAll("_", " "));
    if (seg.duration_ms) bits.push(`${Math.round(seg.duration_ms / 1000)}s`);
    el.meta.textContent = bits.join(" · ");
  } else if (state === "buffering") {
    el.kind.textContent = "…";
    el.kind.className = "badge";
    el.title.textContent = "Spinning up the station";
    el.meta.textContent = "Live generation — can take a few minutes on first Play";
  } else {
    el.kind.textContent = "—";
    el.kind.className = "badge";
    el.title.textContent = "Press Play to go on air";
    el.meta.textContent = "";
  }

  if (state === "playing" && !streamAttached) attachStream();
  if (state === "stopped" && streamAttached) detachStream();
}

function renderQueue(items) {
  el.queue.innerHTML = "";
  if (!items?.length) {
    const li = document.createElement("li");
    li.innerHTML = `<span class="q-kind">empty</span><span>Queue fills while you listen</span>`;
    el.queue.appendChild(li);
    return;
  }
  for (const s of items) {
    const li = document.createElement("li");
    li.innerHTML = `<span>${escapeHtml(s.title)}</span><span class="q-kind">${s.kind}</span>`;
    el.queue.appendChild(li);
  }
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

async function refresh() {
  try {
    const [now, queue] = await Promise.all([api("/api/now"), api("/api/queue")]);
    renderNow(now);
    renderQueue(queue.queue || []);
  } catch (e) {
    el.status.textContent = "Backend offline";
    el.health.textContent = String(e.message || e);
    el.health.classList.add("bad");
  }
}

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const parts = Object.entries(h.components || {}).map(
      ([k, v]) => `${k}:${v.ok ? "ok" : "no"}`
    );
    el.health.textContent =
      (h.ok ? "Ready · self-contained · " : "Not ready · ") + parts.join(" · ");
    el.health.classList.toggle("bad", !h.ok && !h.degraded);
    el.play.disabled = !h.ok && !h.degraded;
  } catch (e) {
    el.health.textContent = "Health check failed";
    el.health.classList.add("bad");
  }
}

el.play.addEventListener("click", async () => {
  el.play.disabled = true;
  try {
    await api("/api/control", {
      method: "POST",
      body: JSON.stringify({ action: "play" }),
    });
    el.status.textContent = "Starting… (live generation)";
    streamAttached = false;
  } catch (e) {
    el.status.textContent = e.message || "Play failed";
  } finally {
    el.play.disabled = false;
    refresh();
  }
});

el.stop.addEventListener("click", async () => {
  try {
    await api("/api/control", {
      method: "POST",
      body: JSON.stringify({ action: "stop" }),
    });
    detachStream();
  } catch (e) {
    el.status.textContent = e.message || "Stop failed";
  }
  refresh();
});

el.volume.addEventListener("input", setVolume);
setVolume();

(async () => {
  try {
    const cfg = await api("/api/config");
    el.station.textContent = cfg.name || "AI Radio";
    el.host.textContent = `Host: ${cfg.host_name || "—"} · self-contained · local`;
    document.title = cfg.name || "AI Radio";
  } catch {
    /* ignore */
  }
  await refreshHealth();
  await refresh();
  setInterval(refresh, 1000);
  setInterval(refreshHealth, 10000);
})();
