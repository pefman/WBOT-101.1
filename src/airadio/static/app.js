/* Self-contained player — hls.min.js vendored. Aether-inspired UI. */
const $ = (id) => document.getElementById(id);

const el = {
  stationMain: $("station-name-main"),
  stationAccent: $("station-name-accent"),
  liveLabel: $("live-label"),
  kind: $("kind-badge"),
  title: $("title"),
  meta: $("meta-line"),
  status: $("status-line"),
  health: $("health-line"),
  play: $("btn-play"),
  stop: $("btn-stop"),
  skip: $("btn-skip"),
  volume: $("volume"),
  player: $("player"),
  liveDot: $("live-dot"),
  cover: $("cover"),
  coverGlyph: $("cover-glyph"),
  coverImg: $("cover-img"),
  wave: $("wave"),
  historyList: $("history-list"),
  mixLine: $("mix-line"),
  genreTags: $("genre-tags"),
  llmPanel: $("llm-panel"),
  llmDetail: $("llm-detail"),
  llmFill: $("llm-progress-fill"),
  llmPercent: $("llm-percent"),
  llmModelName: $("llm-model-name"),
  llmRetry: $("btn-llm-retry"),
  genProgress: $("gen-progress"),
  genFill: $("gen-fill"),
  genStage: $("gen-stage"),
  genReady: $("gen-ready"),
  requestInput: $("request-input"),
  requestBtn: $("btn-request"),
  requestHint: $("request-hint"),
  // Compact pickers
  hostPicker: $("host-picker"),
  hostBtn: $("host-btn"),
  hostMenu: $("host-menu"),
  hostBtnTitle: $("host-btn-title"),
  hostBtnSub: $("host-btn-sub"),
  langPicker: $("lang-picker"),
  langBtn: $("lang-btn"),
  langMenu: $("lang-menu"),
  langBtnTitle: $("lang-btn-title"),
  langBtnSub: $("lang-btn-sub"),
};

const VOL_KEY = "wbot_volume";

let hls = null;
let streamAttached = false;
let lastSegmentId = null;
/** Only re-load audio when the server packages a new stream (not talk→song meta). */
let lastStreamId = null;
let hostName = "Rex";
let stationName = "WBOT-101.1";
/** id → display name from /api/genres */
let genreNames = {};
/** Full catalog order from /api/genres */
let allGenreIds = [];
/** Currently enabled genres */
let enabledGenreIds = [];
let genreBusy = false;
let djs = [];
let activeDjId = null;
let djBusy = false;
let languages = [];
let activeLangId = "en";
let langBusy = false;

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
  const v = Number(el.volume.value);
  el.player.volume = v;
  try {
    localStorage.setItem(VOL_KEY, String(v));
  } catch {
    /* ignore */
  }
}

function loadSavedVolume() {
  try {
    const raw = localStorage.getItem(VOL_KEY);
    if (raw != null && el.volume) {
      const v = Math.min(1, Math.max(0, Number(raw)));
      if (Number.isFinite(v)) el.volume.value = String(v);
    }
  } catch {
    /* ignore */
  }
  setVolume();
}

function renderGeneration(now) {
  if (!el.genProgress) return;
  const gen = now.generation || {};
  const state = now.state || "stopped";
  const stage = gen.stage || "idle";
  const busy =
    state === "buffering" ||
    (stage && stage !== "idle" && state === "playing");
  const show =
    busy ||
    (state === "buffering" && (now.buffering_message || gen.detail));

  el.genProgress.hidden = !show;
  if (!show) return;

  const pct =
    typeof gen.progress === "number"
      ? Math.round(Math.min(100, Math.max(0, gen.progress * 100)))
      : state === "buffering"
        ? 12
        : 0;
  if (el.genFill) el.genFill.style.width = `${pct}%`;

  const detail =
    gen.detail ||
    gen.stage_label ||
    now.buffering_message ||
    "Generating…";
  if (el.genStage) el.genStage.textContent = detail;

  if (el.genReady) {
    const ready = gen.ready != null ? gen.ready : now.queue_depth;
    const min = gen.buffer_min != null ? gen.buffer_min : "—";
    const target = gen.buffer_target != null ? gen.buffer_target : "—";
    el.genReady.textContent =
      ready != null ? `${ready}/${min} ready · target ${target}` : "";
  }
}

function splitStationName(name) {
  const n = (name || "WBOT-101.1").trim();
  // "WBOT-101.1" → main WBOT · accent 101.1
  const m = n.match(/^(.+?)([-–—\s]+)(.+)$/);
  if (m) return { main: m[1], accent: m[3] };
  if (n.length > 4) return { main: n.slice(0, -4), accent: n.slice(-4) };
  return { main: n, accent: "FM" };
}

function setStationBranding(name) {
  stationName = name || "WBOT-101.1";
  const { main, accent } = splitStationName(stationName);
  el.stationMain.textContent = main;
  el.stationAccent.textContent = accent;
  document.title = stationName;
}

/**
 * Seamless stream swap for Skip: keep old audio playing until the new WAV
 * is ready, then cut over. Avoids the silence gap from destroying HLS first.
 */
function softSwapStream(streamId) {
  const bust = `v=${streamId}&t=${Date.now()}`;
  const wavUrl = `/stream/current.wav?${bust}`;
  const vol = Number(el.volume?.value ?? el.player?.volume ?? 0.85);
  const next = document.createElement("audio");
  next.playsInline = true;
  next.preload = "auto";
  next.src = wavUrl;
  next.volume = vol;

  let settled = false;
  const commit = () => {
    if (settled) return;
    settled = true;
    next
      .play()
      .then(() => {
        try {
          if (hls) {
            hls.destroy();
            hls = null;
          }
          const old = el.player;
          if (old && old !== next) {
            old.pause();
            old.removeAttribute("src");
            try {
              old.load();
            } catch {
              /* ignore */
            }
            if (old.parentNode) {
              old.replaceWith(next);
            }
            next.id = "player";
            el.player = next;
          }
        } catch (e) {
          console.warn("soft swap cleanup", e);
        }
        streamAttached = true;
        setVolume();
      })
      .catch((e) => {
        console.warn("soft swap play failed, hard attach", e);
        attachStreamHard();
      });
  };

  // Timeout: if WAV slow, fall back to hard attach
  const failSafe = setTimeout(() => {
    if (!settled) {
      console.warn("soft swap timeout → hard attach");
      settled = true;
      try {
        next.pause();
        next.removeAttribute("src");
      } catch {
        /* ignore */
      }
      attachStreamHard();
    }
  }, 4000);

  next.addEventListener(
    "canplay",
    () => {
      clearTimeout(failSafe);
      commit();
    },
    { once: true }
  );
  next.addEventListener(
    "error",
    () => {
      clearTimeout(failSafe);
      if (!settled) {
        settled = true;
        attachStreamHard();
      }
    },
    { once: true }
  );
  next.load();
}

function attachStreamHard() {
  const bust = lastStreamId != null ? `?v=${lastStreamId}` : `?t=${Date.now()}`;
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

function attachStream({ soft = false } = {}) {
  // Soft swap only when already on air (Skip / next package)
  if (soft && streamAttached && el.player && !el.player.paused) {
    softSwapStream(lastStreamId ?? Date.now());
    return;
  }
  attachStreamHard();
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

function setWaveActive(on) {
  el.wave.classList.toggle("active", !!on);
}

/** Format milliseconds as m:ss or h:mm:ss (never bare "75s"). */
function formatDuration(ms) {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "";
  const totalSec = Math.max(0, Math.round(ms / 1000));
  const h = Math.floor(totalSec / 3600);
  const m = Math.floor((totalSec % 3600) / 60);
  const s = totalSec % 60;
  const ss = String(s).padStart(2, "0");
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${ss}`;
  return `${m}:${ss}`;
}

/** Local clock time as 24h HH:MM (e.g. 22:42). */
function formatClock(date) {
  if (!(date instanceof Date) || Number.isNaN(date.getTime())) return "";
  const h = String(date.getHours()).padStart(2, "0");
  const m = String(date.getMinutes()).padStart(2, "0");
  return `${h}:${m}`;
}

/**
 * Playback progress from API timestamps.
 * Returns { elapsedMs, remainingMs, endsAt, progress } or null.
 */
function segmentTiming(seg, startedAt) {
  if (!seg || !seg.duration_ms) return null;
  const durationMs = Number(seg.duration_ms);
  if (!Number.isFinite(durationMs) || durationMs <= 0) return null;
  // segment_started_at is unix seconds (or ms if already large)
  let startMs = null;
  if (startedAt != null && Number.isFinite(Number(startedAt))) {
    const raw = Number(startedAt);
    startMs = raw < 1e12 ? raw * 1000 : raw;
  }
  if (startMs == null) {
    return {
      elapsedMs: 0,
      remainingMs: durationMs,
      endsAt: null,
      progress: 0,
      durationMs,
    };
  }
  const elapsedMs = Math.max(0, Math.min(durationMs, Date.now() - startMs));
  const remainingMs = Math.max(0, durationMs - elapsedMs);
  return {
    elapsedMs,
    remainingMs,
    endsAt: new Date(startMs + durationMs),
    progress: durationMs > 0 ? elapsedMs / durationMs : 0,
    durationMs,
  };
}

function renderNow(now) {
  const state = now.state || "stopped";
  const onAir = state === "playing";
  el.liveDot.classList.toggle("live", onAir);
  setWaveActive(onAir);

  if (onAir) {
    el.liveLabel.textContent = "LIVE BROADCAST · LOCAL";
  } else if (state === "buffering") {
    el.liveLabel.textContent = "BUFFERING · GENERATING";
  } else {
    el.liveLabel.textContent = "OFF AIR · LOCAL";
  }

  const seg = now.segment;
  const timing = seg ? segmentTiming(seg, now.segment_started_at) : null;

  if (state === "buffering") {
    const gen = now.generation || {};
    el.status.textContent =
      gen.detail || now.buffering_message || "Buffering…";
  } else if (state === "playing") {
    const gen = now.generation || {};
    if (gen.stage && gen.stage !== "idle" && gen.detail) {
      el.status.textContent = `On air · filling buffer · ${gen.detail}`;
    } else {
      const parts = [`On air · ${hostName} is in the booth`];
      if (timing) {
        parts.push(`${formatDuration(timing.remainingMs)} left`);
        if (timing.endsAt) {
          parts.push(`ends ${formatClock(timing.endsAt)}`);
        }
      }
      el.status.textContent = parts.join(" · ");
    }
  } else {
    el.status.textContent = "Stopped — press Play to go on air";
  }

  renderGeneration(now);
  if (el.skip) {
    el.skip.disabled = state !== "playing";
  }

  if (seg) {
    const kind = seg.kind === "song" ? "song" : "talk";
    el.kind.textContent = kind === "song" ? "Song" : "Talk";
    el.kind.className = "kind " + kind;
    setCoverArt(kind === "song" ? seg.cover_url : null, kind === "song" ? "◉" : "◎");
    el.title.textContent = seg.title || "—";

    const bits = [];
    if (kind === "song") {
      if (seg.artist) bits.push(seg.artist);
      if (seg.genre_id) bits.push(prettyGenre(seg.genre_id));
    } else {
      bits.push(`${hostName} · on mic`);
    }
    if (timing) {
      // "0:23 of 1:15" = progress through this track (not "song is 23s")
      bits.push(
        `${formatDuration(timing.elapsedMs)} of ${formatDuration(timing.durationMs)}`
      );
    } else if (seg.duration_ms) {
      bits.push(formatDuration(seg.duration_ms));
    }
    el.meta.textContent = bits.join(" · ") || "Local session";

    // genre tags updated from enabled_genres in refresh()
  } else if (state === "buffering") {
    el.kind.textContent = "…";
    el.kind.className = "kind";
    setCoverArt(null, "◌");
    el.title.textContent = "Spinning up the station";
    el.meta.textContent = "Live generation — talk + first song";
  } else {
    el.kind.textContent = "—";
    el.kind.className = "kind";
    setCoverArt(null, "◉");
    el.title.textContent = "Press Play to go on air";
    el.meta.textContent = `${stationName} · local offline radio`;
  }

  const segId = seg?.id || null;
  if (segId) lastSegmentId = segId;
  if (state === "playing") {
    // stream_id changes only when HLS/WAV is re-packaged. Talk→song handoff
    // keeps the same stream so the bed doesn't cut off when the host stops.
    const sid = now.stream_id;
    const streamChanged = sid != null && sid !== lastStreamId;
    if (!streamAttached) {
      if (sid != null) lastStreamId = sid;
      attachStream({ soft: false });
    } else if (streamChanged) {
      // Keep old audio until new package is ready (no silence on Skip)
      if (sid != null) lastStreamId = sid;
      attachStream({ soft: true });
    }
  }
  if (state === "stopped" && streamAttached) {
    lastSegmentId = null;
    lastStreamId = null;
    detachStream();
  }
}

function prettyGenre(id) {
  if (!id) return "";
  if (genreNames[id]) return genreNames[id];
  return String(id)
    .replaceAll("_", " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function setCoverArt(url, glyph) {
  if (!el.cover) return;
  const g = glyph || "◉";
  if (el.coverGlyph) el.coverGlyph.textContent = g;
  if (url && el.coverImg) {
    el.cover.classList.add("has-art");
    el.coverImg.hidden = false;
    el.coverImg.alt = "Album cover";
    // bust cache when segment changes
    el.coverImg.src = url + (url.includes("?") ? "&" : "?") + "t=" + Date.now();
    if (el.coverGlyph) el.coverGlyph.hidden = true;
  } else {
    el.cover.classList.remove("has-art");
    if (el.coverImg) {
      el.coverImg.hidden = true;
      el.coverImg.removeAttribute("src");
      el.coverImg.alt = "";
    }
    if (el.coverGlyph) el.coverGlyph.hidden = false;
  }
  // keep kind class if present
  const kind = el.cover.className.includes("talk")
    ? "talk"
    : el.cover.className.includes("song")
      ? "song"
      : "";
  el.cover.className =
    "cover" + (kind ? " " + kind : "") + (url ? " has-art" : "");
}

function renderGenreTags(enabledIds) {
  if (!el.genreTags) return;
  if (enabledIds) enabledGenreIds = [...enabledIds];
  el.genreTags.innerHTML = "";
  const enabled = new Set(enabledGenreIds);
  const catalog = allGenreIds.length
    ? allGenreIds
    : enabledGenreIds.length
      ? enabledGenreIds
      : Object.keys(genreNames);
  if (!catalog.length) {
    const empty = document.createElement("span");
    empty.className = "genre-tag off";
    empty.textContent = "No genres loaded";
    el.genreTags.appendChild(empty);
    return;
  }
  for (const id of catalog) {
    const btn = document.createElement("button");
    btn.type = "button";
    const on = enabled.has(id);
    const solo = on && enabledGenreIds.length === 1;
    btn.className =
      "genre-tag" + (on ? " on" : " off") + (solo ? " solo" : "");
    btn.textContent = prettyGenre(id);
    btn.title = solo
      ? `${prettyGenre(id)} · only this (Shift+click to multi-select)`
      : on
        ? `${prettyGenre(id)} · on (click = only this · Shift+click to turn off)`
        : `${prettyGenre(id)} · off (click = only this · Shift+click to add)`;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.disabled = genreBusy;
    btn.addEventListener("click", (e) => {
      // Primary: click = solo this genre (matches “I chose metal”)
      // Shift/Ctrl/Meta+click = multi-select toggle
      if (e.shiftKey || e.metaKey || e.ctrlKey) {
        toggleGenre(id);
      } else {
        soloGenre(id);
      }
    });
    el.genreTags.appendChild(btn);
  }
  if (el.mixLine) {
    const n = enabledGenreIds.length;
    const total = catalog.length;
    if (n === 1) {
      el.mixLine.textContent = `Only ${prettyGenre(
        enabledGenreIds[0]
      )} · click another to switch · Shift+click multi`;
    } else if (n === total) {
      el.mixLine.textContent = `All ${total} genres · click one to lock that only`;
    } else {
      el.mixLine.textContent = `Genres · ${n}/${total} on · click one to solo · Shift+click toggle`;
    }
  }
}

async function applyGenres(next, { solo = false } = {}) {
  if (genreBusy) return;
  if (!next.length) {
    el.status.textContent = "Keep at least one genre on";
    return;
  }
  genreBusy = true;
  renderGenreTags(next);
  try {
    const res = await api("/api/genres", {
      method: "POST",
      body: JSON.stringify({ genre_ids: next }),
    });
    enabledGenreIds = res.enabled_genres || next;
    const name =
      enabledGenreIds.length === 1
        ? prettyGenre(enabledGenreIds[0])
        : `${enabledGenreIds.length} genres`;
    const clearedSongs = res.removed_pending_songs || 0;
    const clearedTalks = res.removed_pending_talks || 0;
    const cleared = clearedSongs + clearedTalks;
    if (solo || enabledGenreIds.length === 1) {
      el.status.textContent =
        cleared > 0
          ? `Only ${name} · cleared ${clearedSongs} song(s), ${clearedTalks} talk(s) from queue`
          : `Only ${name} — saved; next tracks will match`;
    } else if (cleared) {
      el.status.textContent = `Genres updated · cleared ${cleared} queued segment(s)`;
    } else {
      el.status.textContent = `Genres: ${name}`;
    }
    await refresh();
  } catch (e) {
    el.status.textContent = e.message || "Genre change failed";
  } finally {
    genreBusy = false;
    renderGenreTags(enabledGenreIds);
  }
}

/** Multi-select toggle (Shift+click). */
async function toggleGenre(genreId) {
  if (genreBusy) return;
  const on = enabledGenreIds.includes(genreId);
  let next;
  if (on) {
    if (enabledGenreIds.length <= 1) {
      el.status.textContent = "Keep at least one genre on";
      return;
    }
    next = enabledGenreIds.filter((g) => g !== genreId);
  } else {
    next = [...enabledGenreIds, genreId];
  }
  await applyGenres(next);
}

/** Only this genre — default click. */
async function soloGenre(genreId) {
  if (genreBusy) return;
  // Already solo on this id — nothing to do
  if (
    enabledGenreIds.length === 1 &&
    enabledGenreIds[0] === genreId
  ) {
    el.status.textContent = `Already only ${prettyGenre(genreId)}`;
    return;
  }
  await applyGenres([genreId], { solo: true });
}

function closeAllPickers(except) {
  for (const key of ["host", "lang"]) {
    if (key === except) continue;
    const root = $(`${key}-picker`);
    const btn = $(`${key}-btn`);
    const menu = $(`${key}-menu`);
    if (root) root.classList.remove("is-open");
    if (btn) btn.setAttribute("aria-expanded", "false");
    if (menu) menu.hidden = true;
  }
}

function togglePicker(name) {
  const root = $(`${name}-picker`);
  const btn = $(`${name}-btn`);
  const menu = $(`${name}-menu`);
  if (!root || !btn || !menu) return;
  const open = root.classList.contains("is-open");
  closeAllPickers();
  if (!open) {
    root.classList.add("is-open");
    btn.setAttribute("aria-expanded", "true");
    menu.hidden = false;
  }
}

async function loadGenreCatalog() {
  try {
    const data = await api("/api/genres");
    genreNames = {};
    allGenreIds = [];
    enabledGenreIds = [];
    for (const g of data.genres || []) {
      genreNames[g.id] = g.name || prettyGenre(g.id);
      allGenreIds.push(g.id);
      if (g.enabled) enabledGenreIds.push(g.id);
    }
    // Radio meta-pack first, then A–Z
    allGenreIds.sort((a, b) => {
      if (a === "radio") return -1;
      if (b === "radio") return 1;
      return prettyGenre(a).localeCompare(prettyGenre(b), undefined, {
        sensitivity: "base",
      });
    });
    if (!enabledGenreIds.length) {
      enabledGenreIds = [...allGenreIds];
    }
    renderGenreTags(enabledGenreIds);
  } catch (e) {
    console.warn("genres load failed", e);
  }
}

function setHostDisplay(name, blurb) {
  if (name) {
    hostName = name;
    if (el.hostBtnTitle) el.hostBtnTitle.textContent = name;
  }
  if (blurb != null) {
    if (el.hostBtnSub) el.hostBtnSub.textContent = blurb || "On air host";
  }
}

const COPY_ICON = "⎘";
const COPY_OK = "✓";

async function copyText(text, btn) {
  const payload = (text || "").trim();
  if (!payload) return;
  try {
    await navigator.clipboard.writeText(payload);
    if (btn) {
      btn.textContent = COPY_OK;
      btn.classList.add("copied");
      setTimeout(() => {
        btn.textContent = COPY_ICON;
        btn.classList.remove("copied");
      }, 1200);
    }
  } catch (e) {
    console.warn("clipboard failed", e);
    if (btn) {
      btn.textContent = "!";
      setTimeout(() => {
        btn.textContent = COPY_ICON;
      }, 1200);
    }
  }
}

/**
 * Build the last-5 song list: current song (if on air) first as "Now",
 * then finished history — max 5 total with cover, artist, title, genre.
 */
function buildRecentSongList(historySongs, now) {
  const finished = (historySongs || []).filter((s) => s && s.kind !== "talk");
  const out = [];
  const seen = new Set();
  const cur = now && now.segment;
  if (cur && cur.kind === "song" && cur.id) {
    out.push({ ...cur, _badge: "Now" });
    seen.add(cur.id);
  }
  for (const s of finished) {
    if (!s || !s.id || seen.has(s.id)) continue;
    out.push({ ...s, _badge: out.length === 0 ? "Last" : null });
    seen.add(s.id);
    if (out.length >= 5) break;
  }
  return out.slice(0, 5);
}

function renderHistory(songs, now) {
  if (!el.historyList) return;
  el.historyList.innerHTML = "";
  const list = buildRecentSongList(songs, now);
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "Last 5 songs will appear here with cover art.";
    el.historyList.appendChild(empty);
    return;
  }

  for (const s of list) {
    const item = document.createElement("div");
    item.className =
      "history-item" + (s._badge === "Now" ? " is-now" : "");

    const genre = s.genre_id ? prettyGenre(s.genre_id) : "";
    const dur = s.duration_ms ? formatDuration(s.duration_ms) : "";
    const artist = (s.artist || "").trim();
    const title = (s.title || "Untitled").trim();
    // Prefer stored tags+lyrics (what ACE got). Fallback for old segments.
    const prompt = (
      s.generation_prompt ||
      [s.text_preview || "", s.genre_id ? String(s.genre_id) : ""]
        .filter(Boolean)
        .join("\n\n")
    ).trim();

    // Cover thumb
    const art = document.createElement("div");
    art.className = "hist-art";
    art.setAttribute("aria-hidden", "true");
    if (s.cover_url) {
      const img = document.createElement("img");
      img.src = s.cover_url;
      img.alt = "";
      img.loading = "lazy";
      art.appendChild(img);
    } else {
      art.classList.add("no-art");
      art.textContent = "◉";
    }

    const body = document.createElement("div");
    body.className = "hist-body";

    const meta = document.createElement("div");
    meta.className = "hist-meta";
    const bits = [];
    if (s._badge) bits.push(s._badge);
    if (genre) bits.push(genre);
    if (dur) bits.push(dur);
    meta.textContent = bits.join(" · ") || "Song";

    const titleEl = document.createElement("div");
    titleEl.className = "hist-title";
    titleEl.textContent = title;

    const artistEl = document.createElement("div");
    artistEl.className = "hist-artist";
    artistEl.textContent = artist || "Unknown artist";

    body.appendChild(meta);
    body.appendChild(titleEl);
    body.appendChild(artistEl);

    const actions = document.createElement("div");
    actions.className = "hist-actions";

    const fav = document.createElement("button");
    fav.type = "button";
    fav.className = "hist-fav" + (s.favorite ? " on" : "");
    fav.textContent = s.favorite ? "★" : "☆";
    fav.title = s.favorite
      ? "Unfavorite"
      : "Favorite (prefer re-air)";
    fav.setAttribute("aria-label", "Favorite track");
    fav.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      try {
        const next = !fav.classList.contains("on");
        await api("/api/favorite", {
          method: "POST",
          body: JSON.stringify({ segment_id: s.id, favorite: next }),
        });
        fav.classList.toggle("on", next);
        fav.textContent = next ? "★" : "☆";
      } catch (err) {
        if (el.status) el.status.textContent = err.message || "Favorite failed";
      }
    });

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "hist-copy";
    btn.textContent = COPY_ICON;
    btn.setAttribute("aria-label", "Copy tags and lyrics");
    btn.title = "Copy tags + lyrics (ACE inputs)";
    if (!prompt) {
      btn.disabled = true;
      btn.title = "No tags/lyrics stored for this track";
    } else {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        copyText(prompt, btn);
      });
    }

    actions.appendChild(fav);
    actions.appendChild(btn);

    const row = document.createElement("div");
    row.className = "hist-row";
    row.appendChild(art);
    row.appendChild(body);
    row.appendChild(actions);
    item.appendChild(row);
    el.historyList.appendChild(item);
  }
}

function renderDjs() {
  if (!el.hostMenu) return;
  el.hostMenu.innerHTML = "";
  for (const d of djs) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "picker-option" + (d.id === activeDjId ? " active" : "");
    btn.setAttribute("role", "option");
    btn.setAttribute("aria-selected", d.id === activeDjId ? "true" : "false");
    btn.disabled = djBusy;
    btn.innerHTML = `
      <span class="opt-title">${escapeHtml(d.name)}</span>
      <span class="opt-sub">${escapeHtml(d.blurb || "")}</span>
    `;
    btn.addEventListener("click", () => {
      closeAllPickers();
      selectDj(d.id);
    });
    el.hostMenu.appendChild(btn);
  }
  const d = djs.find((x) => x.id === activeDjId);
  if (d) setHostDisplay(d.name, d.blurb || "");
}

async function selectDj(djId) {
  if (djBusy) return;
  djBusy = true;
  renderDjs();
  try {
    const res = await api("/api/dj", {
      method: "POST",
      body: JSON.stringify({ dj_id: djId, apply_voice: true }),
    });
    activeDjId = res.dj_id || djId;
    setHostDisplay(res.host_name || res.name, res.blurb || "");
    const dropped = res.removed_pending_talk || 0;
    if (el.status) {
      el.status.textContent =
        dropped > 0
          ? `${hostName} takes the mic · dropped ${dropped} queued talk(s)`
          : `${hostName} in the booth · generation starts on Play`;
    }
    await refresh();
  } catch (e) {
    el.status.textContent = e.message || "DJ change failed";
  } finally {
    djBusy = false;
    renderDjs();
  }
}

async function loadDjs() {
  try {
    const data = await api("/api/djs");
    djs = data.djs || [];
    activeDjId = data.active || data.default || null;
    renderDjs();
  } catch (e) {
    console.warn("djs load failed", e);
  }
}

function updateLangClosed() {
  const lang = languages.find((x) => x.id === activeLangId);
  if (el.langBtnTitle) {
    el.langBtnTitle.textContent = lang?.label || activeLangId || "English";
  }
  if (el.langBtnSub) {
    el.langBtnSub.textContent = lang
      ? `${lang.native || lang.label} · lyrics`
      : "lyrics & vocals";
  }
}

function renderLanguages() {
  if (!el.langMenu) return;
  el.langMenu.innerHTML = "";
  for (const lang of languages) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className =
      "picker-option" + (lang.id === activeLangId ? " active" : "");
    btn.setAttribute("role", "option");
    btn.setAttribute(
      "aria-selected",
      lang.id === activeLangId ? "true" : "false"
    );
    btn.disabled = langBusy;
    btn.innerHTML = `
      <span class="opt-title">${escapeHtml(lang.label)}</span>
      <span class="opt-sub">${escapeHtml(lang.native || "")} · ${escapeHtml(lang.id)}</span>
    `;
    btn.addEventListener("click", () => {
      closeAllPickers();
      selectLanguage(lang.id);
    });
    el.langMenu.appendChild(btn);
  }
  updateLangClosed();
}

async function selectLanguage(langId) {
  if (langBusy || langId === activeLangId) return;
  langBusy = true;
  renderLanguages();
  try {
    const res = await api("/api/language", {
      method: "POST",
      body: JSON.stringify({ language: langId }),
    });
    activeLangId = res.language || langId;
    updateLangClosed();
    if (el.status) {
      el.status.textContent = `Music language: ${res.label || activeLangId} · next songs use it (DJ stays English)`;
    }
    await refresh();
  } catch (e) {
    el.status.textContent = e.message || "Language change failed";
  } finally {
    langBusy = false;
    renderLanguages();
  }
}

async function loadLanguages() {
  try {
    const data = await api("/api/languages");
    languages = data.languages || [];
    activeLangId = data.active || data.default || "en";
    renderLanguages();
  } catch (e) {
    console.warn("languages load failed", e);
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
    const [now, history] = await Promise.all([
      api("/api/now"),
      api("/api/history"),
    ]);
    if (now.enabled_genres && !genreBusy) {
      const next = now.enabled_genres || [];
      const same =
        next.length === enabledGenreIds.length &&
        next.every((g, i) => g === enabledGenreIds[i]);
      if (!same) renderGenreTags(next);
    }
    if (now.dj_name && now.dj_name !== hostName && !djBusy) {
      setHostDisplay(now.dj_name, now.dj_blurb);
    } else if (now.dj_blurb != null && !djBusy) {
      setHostDisplay(now.dj_name || hostName, now.dj_blurb);
    }
    if (now.dj_id && now.dj_id !== activeDjId && !djBusy) {
      activeDjId = now.dj_id;
      renderDjs();
    }
    if (now.language && now.language !== activeLangId && !langBusy) {
      activeLangId = now.language;
      renderLanguages();
    }
    lastKnownState = now.state || "stopped";
    renderNow(now);
    renderHistory(history.songs || [], now);
    if (el.requestHint && typeof now.pending_requests === "number") {
      const n = now.pending_requests;
      el.requestHint.textContent =
        n > 0
          ? `${n} request(s) queued for upcoming talk breaks.`
          : "Next talk break will weave this in.";
    }
  } catch (e) {
    el.status.textContent = "Backend offline";
    el.health.textContent = String(e.message || e);
    el.health.classList.add("bad");
    setWaveActive(false);
  }
}

function formatBytes(n) {
  if (!n || n < 0) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let x = n;
  while (x >= 1024 && i < u.length - 1) {
    x /= 1024;
    i++;
  }
  return `${x.toFixed(i === 0 ? 0 : 1)} ${u[i]}`;
}

function renderLlmPull(pull) {
  if (!el.llmPanel || !pull) return;
  const st = pull.status || "idle";
  const pulling = st === "pulling" || st === "checking";
  const err = st === "error";
  const show = pulling || err || (st !== "ready" && st !== "idle");
  el.llmPanel.hidden = !show && st === "ready";

  if (st === "ready") {
    el.llmPanel.hidden = true;
    return;
  }

  el.llmPanel.hidden = false;
  el.llmDetail.textContent = pull.detail || st;
  el.llmModelName.textContent = pull.model || "";
  const pct = typeof pull.percent === "number" ? pull.percent : null;
  if (pct != null) {
    el.llmFill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
    let label = `${pct}%`;
    if (pull.completed && pull.total) {
      label += ` · ${formatBytes(pull.completed)} / ${formatBytes(pull.total)}`;
    }
    el.llmPercent.textContent = label;
  } else if (pulling) {
    el.llmFill.style.width = "8%";
    el.llmPercent.textContent = "…";
  } else if (err) {
    el.llmFill.style.width = "0%";
    el.llmPercent.textContent = "error";
    el.llmDetail.textContent = pull.error || pull.detail || "Download failed";
  }
}

async function refreshLlmStatus() {
  try {
    const pull = await api("/api/llm/status");
    renderLlmPull(pull);
    return pull;
  } catch {
    return null;
  }
}

async function ensureLlm() {
  try {
    const pull = await api("/api/llm/ensure", { method: "POST", body: "{}" });
    renderLlmPull(pull);
  } catch (e) {
    if (el.llmDetail) {
      el.llmPanel.hidden = false;
      el.llmDetail.textContent = e.message || "Could not start download";
    }
  }
}

async function refreshHealth() {
  try {
    const h = await api("/api/health");
    const parts = Object.entries(h.components || {}).map(
      ([k, v]) => `${k}: ${v.ok ? "ok" : "no"}`
    );
    const llm = h.components?.vllm || {};
    const pull = h.llm_pull || {};
    let llmHint = "";
    if (llm.ok) {
      llmHint = llm.model ? ` · vLLM ${llm.model}` : " · vLLM ok";
    } else if (pull.status === "pulling") {
      llmHint = ` · downloading ${pull.model || "model"} ${pull.percent != null ? pull.percent + "%" : ""}`;
    } else if (h.degraded) {
      llmHint = " · host LLM (will start on-demand)";
    } else {
      llmHint = " · host LLM ERROR (Play blocked)";
    }
    el.health.textContent =
      (h.ok ? "Ready" : "Not ready") + llmHint + " · " + parts.join(" · ");
    el.health.classList.toggle("bad", !h.ok && !h.degraded && pull.status !== "pulling");
    el.play.disabled = !h.ok && !h.degraded;
    if (h.llm_pull) renderLlmPull(h.llm_pull);
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
    el.status.textContent = "Starting… live generation";
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
    setWaveActive(false);
  } catch (e) {
    el.status.textContent = e.message || "Stop failed";
  }
  refresh();
});

if (el.skip) {
  el.skip.addEventListener("click", async () => {
    el.skip.disabled = true;
    try {
      await api("/api/control", {
        method: "POST",
        body: JSON.stringify({ action: "skip" }),
      });
      el.status.textContent = "Skipping…";
    } catch (e) {
      el.status.textContent = e.message || "Skip failed";
    } finally {
      refresh();
    }
  });
}

async function submitRequest() {
  if (!el.requestInput) return;
  const text = (el.requestInput.value || "").trim();
  if (!text) return;
  if (el.requestBtn) el.requestBtn.disabled = true;
  try {
    const res = await api("/api/request", {
      method: "POST",
      body: JSON.stringify({ text }),
    });
    el.requestInput.value = "";
    const n = res.pending != null ? res.pending : 1;
    if (el.requestHint) {
      el.requestHint.textContent = `${n} request(s) queued for upcoming talk breaks.`;
    }
    el.status.textContent = `Request queued (${n} pending)`;
  } catch (e) {
    el.status.textContent = e.message || "Request failed";
  } finally {
    if (el.requestBtn) el.requestBtn.disabled = false;
  }
}

if (el.requestBtn) el.requestBtn.addEventListener("click", submitRequest);
if (el.requestInput) {
  el.requestInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      submitRequest();
    }
  });
}

// Keyboard: Space toggles play/stop when not typing; → skip
let lastKnownState = "stopped";
document.addEventListener("keydown", (e) => {
  const tag = (e.target && e.target.tagName) || "";
  if (tag === "INPUT" || tag === "TEXTAREA" || e.target.isContentEditable) return;
  if (e.key === " " || e.code === "Space") {
    e.preventDefault();
    if (lastKnownState === "playing" || lastKnownState === "buffering") {
      if (el.stop) el.stop.click();
    } else if (el.play && !el.play.disabled) {
      el.play.click();
    }
  } else if (e.key === "ArrowRight") {
    if (el.skip && !el.skip.disabled) el.skip.click();
  }
});

el.volume.addEventListener("input", setVolume);
loadSavedVolume();

if (el.llmRetry) {
  el.llmRetry.addEventListener("click", () => ensureLlm());
}

// Compact pickers
if (el.hostBtn) el.hostBtn.addEventListener("click", () => togglePicker("host"));
if (el.langBtn) el.langBtn.addEventListener("click", () => togglePicker("lang"));

document.addEventListener("click", (e) => {
  if (!e.target.closest(".picker")) closeAllPickers();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeAllPickers();
});

// Note: Space / ArrowRight handled above (global shortcuts)

(async () => {
  try {
    const cfg = await api("/api/config");
    setStationBranding(cfg.name || "WBOT-101.1");
    setHostDisplay(cfg.host_name || "Rex", cfg.dj_blurb || "");
    if (cfg.dj_id) activeDjId = cfg.dj_id;
    if (cfg.enabled_genres) enabledGenreIds = cfg.enabled_genres;
    if (cfg.language) activeLangId = cfg.language;
    if (cfg.llm_pull) renderLlmPull(cfg.llm_pull);
  } catch {
    setStationBranding("WBOT-101.1");
  }
  await loadGenreCatalog();
  await loadDjs();
  await loadLanguages();
  await ensureLlm();
  await refreshHealth();
  await refresh();
  // Auto-play on startup with radio genre
  setTimeout(() => {
    if (el.play && !el.play.disabled) {
      el.play.click();
    }
  }, 1000);
  // Keep UI snappy but don't hammer the API (access logs / LLM status)
  setInterval(refresh, 2000);
  setInterval(refreshHealth, 8000);
  setInterval(refreshLlmStatus, 4000);
})();
