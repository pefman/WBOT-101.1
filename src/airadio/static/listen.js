/* Listen-only player — stream + now playing, no desk controls. */
const $ = (id) => document.getElementById(id);

const el = {
  stage: $("stage"),
  station: $("station-label"),
  dot: $("dot"),
  live: $("live-label"),
  cover: $("cover"),
  glyph: $("glyph"),
  coverImg: $("cover-img"),
  kind: $("kind"),
  title: $("title"),
  artist: $("artist"),
  meta: $("meta"),
  player: $("player"),
  gate: $("gate"),
};

let hls = null;
let lastStreamId = null;
let streamAttached = false;
let unlocked = false;

function prettyGenre(id) {
  if (!id) return "";
  return String(id)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function setCover(url, glyph) {
  if (el.glyph) el.glyph.textContent = glyph || "◉";
  if (url && el.coverImg) {
    el.cover.classList.add("has-art");
    el.coverImg.hidden = false;
    el.coverImg.src = url + (url.includes("?") ? "&" : "?") + "t=" + Date.now();
    if (el.glyph) el.glyph.hidden = true;
  } else {
    el.cover.classList.remove("has-art");
    if (el.coverImg) {
      el.coverImg.hidden = true;
      el.coverImg.removeAttribute("src");
    }
    if (el.glyph) el.glyph.hidden = false;
  }
}

function attachStream(streamId) {
  const bust = streamId != null ? `?v=${streamId}` : `?t=${Date.now()}`;
  const playlist = `/stream/playlist.m3u8${bust}`;
  const wav = `/stream/current.wav${bust}`;

  if (hls) {
    hls.destroy();
    hls = null;
  }

  if (window.Hls && Hls.isSupported()) {
    hls = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      manifestLoadingMaxRetry: 8,
      levelLoadingMaxRetry: 8,
    });
    hls.loadSource(playlist);
    hls.attachMedia(el.player);
    hls.on(Hls.Events.MANIFEST_PARSED, () => {
      el.player.play().then(onPlayOk).catch(onPlayBlocked);
    });
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (data.fatal) {
        el.player.src = wav;
        el.player.play().then(onPlayOk).catch(onPlayBlocked);
      }
    });
    streamAttached = true;
    return;
  }

  if (el.player.canPlayType("application/vnd.apple.mpegurl")) {
    el.player.src = playlist;
    el.player.play().then(onPlayOk).catch(onPlayBlocked);
    streamAttached = true;
    return;
  }

  el.player.src = wav;
  el.player.play().then(onPlayOk).catch(onPlayBlocked);
  streamAttached = true;
}

function softSwap(streamId) {
  const wav = `/stream/current.wav?v=${streamId}&t=${Date.now()}`;
  const next = document.createElement("audio");
  next.playsInline = true;
  next.preload = "auto";
  next.src = wav;
  next.volume = el.player.volume;
  let done = false;
  const commit = () => {
    if (done) return;
    done = true;
    next
      .play()
      .then(() => {
        if (hls) {
          hls.destroy();
          hls = null;
        }
        const old = el.player;
        old.pause();
        old.removeAttribute("src");
        try {
          old.load();
        } catch {
          /* ignore */
        }
        if (old.parentNode) old.replaceWith(next);
        next.id = "player";
        el.player = next;
        streamAttached = true;
        onPlayOk();
      })
      .catch(() => attachStream(streamId));
  };
  next.addEventListener("canplay", commit, { once: true });
  next.addEventListener("error", () => attachStream(streamId), { once: true });
  next.load();
}

function onPlayOk() {
  unlocked = true;
  if (el.gate) el.gate.hidden = true;
}

function onPlayBlocked() {
  unlocked = false;
  if (el.gate) el.gate.hidden = false;
}

function renderNow(now) {
  const state = now.state || "stopped";
  const onAir = state === "playing" || state === "buffering";
  el.dot.classList.toggle("on", state === "playing");
  el.live.textContent =
    state === "playing"
      ? "Live"
      : state === "buffering"
        ? "Buffering"
        : "Off air";

  if (now.station_name && el.station) {
    el.station.textContent = now.station_name;
    document.title = `Listen · ${now.station_name}`;
  }

  const seg = now.segment;
  el.stage.classList.toggle("off-air", !seg || state === "stopped");

  if (!seg) {
    el.kind.textContent = "—";
    el.title.textContent =
      state === "buffering"
        ? now.buffering_message || "Spinning up…"
        : "Station is off air";
    el.artist.textContent = "";
    el.meta.textContent = "";
    setCover(null, "◌");
  } else if (seg.kind === "song") {
    el.kind.textContent = "Now playing";
    el.title.textContent = seg.title || "Untitled";
    el.artist.textContent = seg.artist || "";
    const bits = [];
    if (seg.genre_id) bits.push(prettyGenre(seg.genre_id));
    el.meta.textContent = bits.join(" · ");
    setCover(seg.cover_url || null, "◉");
  } else {
    el.kind.textContent = "On mic";
    el.title.textContent = seg.title || "Talk break";
    el.artist.textContent = now.dj_name || seg.host_name || "";
    el.meta.textContent = "";
    setCover(null, "◎");
  }

  const sid = now.stream_id;
  if (state === "playing" || state === "buffering") {
    if (!streamAttached) {
      if (sid != null) lastStreamId = sid;
      attachStream(sid);
    } else if (sid != null && sid !== lastStreamId) {
      lastStreamId = sid;
      softSwap(sid);
    }
  }
}

async function refresh() {
  try {
    const res = await fetch("/api/now");
    const now = await res.json();
    renderNow(now);
  } catch {
    el.title.textContent = "Can't reach the station";
    el.artist.textContent = "";
    el.dot.classList.remove("on");
    el.live.textContent = "Offline";
  }
}

if (el.gate) {
  el.gate.addEventListener("click", () => {
    el.player.play().then(onPlayOk).catch(() => {});
    if (!streamAttached) attachStream(lastStreamId);
  });
}

// Try autoplay; show gate only if blocked
el.player.volume = 0.9;
refresh();
setInterval(refresh, 2000);
