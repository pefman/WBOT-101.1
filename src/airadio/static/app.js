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
  queue: $("queue-list"),
  play: $("btn-play"),
  stop: $("btn-stop"),
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
  el.player.volume = Number(el.volume.value);
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
    el.status.textContent = now.buffering_message || "Buffering…";
  } else if (state === "playing") {
    const parts = [`On air · ${hostName} is in the booth`];
    if (timing) {
      parts.push(`${formatDuration(timing.remainingMs)} left`);
      if (timing.endsAt) {
        parts.push(`ends ${formatClock(timing.endsAt)}`);
      }
    }
    el.status.textContent = parts.join(" · ");
  } else {
    el.status.textContent = "Stopped — press Play to go on air";
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
    const streamChanged =
      sid != null && sid !== lastStreamId;
    if (!streamAttached || streamChanged) {
      if (sid != null) lastStreamId = sid;
      streamAttached = false;
      attachStream();
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
    btn.className = "genre-tag" + (on ? " on" : " off");
    btn.textContent = prettyGenre(id);
    btn.title = on
      ? `${prettyGenre(id)} · on (click to disable)`
      : `${prettyGenre(id)} · off (click to enable)`;
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    btn.disabled = genreBusy;
    btn.addEventListener("click", () => toggleGenre(id));
    el.genreTags.appendChild(btn);
  }
  if (el.mixLine) {
    const n = enabledGenreIds.length;
    const total = catalog.length;
    el.mixLine.textContent = `Genres · ${n}/${total} on · click to toggle`;
  }
}

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
  genreBusy = true;
  renderGenreTags(next);
  try {
    const res = await api("/api/genres", {
      method: "POST",
      body: JSON.stringify({ genre_ids: next }),
    });
    enabledGenreIds = res.enabled_genres || next;
    if (res.removed_pending_songs) {
      el.status.textContent = `Genres updated · cleared ${res.removed_pending_songs} queued song(s)`;
    } else {
      el.status.textContent = `Genres: ${enabledGenreIds.length} on`;
    }
    await refresh();
  } catch (e) {
    el.status.textContent = e.message || "Genre change failed";
  } finally {
    genreBusy = false;
    renderGenreTags(enabledGenreIds);
  }
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
    allGenreIds.sort((a, b) =>
      prettyGenre(a).localeCompare(prettyGenre(b), undefined, {
        sensitivity: "base",
      })
    );
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

function renderHistory(songs) {
  if (!el.historyList) return;
  el.historyList.innerHTML = "";
  const list = songs || [];
  if (!list.length) {
    const empty = document.createElement("div");
    empty.className = "history-empty";
    empty.textContent = "Songs you hear will show up here.";
    el.historyList.appendChild(empty);
    return;
  }
  // Cap at 5 — no scroll in history panel
  for (const s of list.slice(0, 5)) {
    const item = document.createElement("div");
    item.className = "history-item";
    const genre = s.genre_id ? prettyGenre(s.genre_id) : "";
    const dur = s.duration_ms ? formatDuration(s.duration_ms) : "";
    const metaBits = [genre, dur].filter(Boolean).join(" · ");
    const line =
      s.artist && s.title
        ? `${s.artist} — ${s.title}`
        : s.title || s.artist || "Untitled";
    const prompt =
      s.generation_prompt ||
      [
        s.artist && s.title ? `# ${s.artist} — ${s.title}` : null,
        s.genre_id ? `Genre: ${s.genre_id}` : null,
        s.text_preview ? `\n## Lyrics\n${s.text_preview}` : null,
      ]
        .filter(Boolean)
        .join("\n");

    const meta = document.createElement("div");
    meta.className = "hist-meta";
    meta.textContent = metaBits || "Song";

    const titleEl = document.createElement("div");
    titleEl.className = "hist-title";
    titleEl.textContent = line;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "hist-copy";
    btn.textContent = COPY_ICON;
    btn.setAttribute("aria-label", "Copy generation prompt");
    btn.title = "Copy generation prompt";
    if (!prompt) {
      btn.disabled = true;
      btn.title = "No prompt stored for this track";
    } else {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        copyText(prompt, btn);
      });
    }

    const row = document.createElement("div");
    row.className = "hist-row";
    const body = document.createElement("div");
    body.className = "hist-body";
    body.appendChild(meta);
    body.appendChild(titleEl);
    row.appendChild(body);
    row.appendChild(btn);
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

/**
 * Queue start times: after remaining current segment, then cumulative durations.
 * @param {Array} items queue segments
 * @param {{ remainingMs?: number } | null} currentTiming
 */
const QUEUE_VISIBLE = 3; // no scroll — only show the next few

function renderQueue(items, currentTiming) {
  el.queue.innerHTML = "";
  if (!items?.length) {
    const empty = document.createElement("div");
    empty.className = "empty-queue";
    empty.textContent = "Queue fills while you listen.";
    el.queue.appendChild(empty);
    return;
  }

  let offsetMs =
    currentTiming && Number.isFinite(currentTiming.remainingMs)
      ? currentTiming.remainingMs
      : 0;
  const nowMs = Date.now();
  const all = items || [];
  const visible = all.slice(0, QUEUE_VISIBLE);
  const hidden = all.length - visible.length;

  for (const s of visible) {
    const item = document.createElement("div");
    item.className = "item";
    const kind = (s.kind || "track").toUpperCase();
    const genre = s.genre_id ? prettyGenre(s.genre_id) : "";
    const dur = s.duration_ms ? formatDuration(s.duration_ms) : "";
    const startsIn = formatDuration(offsetMs);
    const startsAt = formatClock(new Date(nowMs + offsetMs));
    const whenBits = [];
    if (startsIn) whenBits.push(`in ${startsIn}`);
    if (startsAt) whenBits.push(`at ${startsAt}`);
    const when = whenBits.length ? whenBits.join(" · ") : "";
    const line =
      s.kind === "song" && s.artist
        ? `${s.artist} — ${s.title || "Untitled"}`
        : s.title || "Untitled";

    const metaBits = [
      `<span class="kind-tag">${escapeHtml(kind)}</span>`,
      genre ? escapeHtml(genre) : "",
      dur ? escapeHtml(dur) : "",
    ].filter(Boolean);
    item.innerHTML = `
      <div class="time">${metaBits.join(" · ")}${
        when ? ` · <span class="when">${escapeHtml(when)}</span>` : ""
      }</div>
      <div class="title">${escapeHtml(line)}</div>
    `;
    el.queue.appendChild(item);

    if (s.duration_ms && Number.isFinite(Number(s.duration_ms))) {
      offsetMs += Number(s.duration_ms);
    }
  }

  if (hidden > 0) {
    const more = document.createElement("div");
    more.className = "queue-more";
    more.textContent = `+${hidden} more in buffer`;
    el.queue.appendChild(more);
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
    const [now, queue, history] = await Promise.all([
      api("/api/now"),
      api("/api/queue"),
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
    renderNow(now);
    const timing = now.segment
      ? segmentTiming(now.segment, now.segment_started_at)
      : null;
    renderQueue(queue.queue || [], timing);
    renderHistory(history.songs || []);
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
    const llm = h.components?.llm || {};
    const pull = h.llm_pull || {};
    let llmHint = " · host LLM ERROR (Play blocked)";
    if (llm.ok) {
      llmHint = llm.model ? ` · host LLM ${llm.model}` : " · host LLM ok";
    } else if (pull.status === "pulling") {
      llmHint = ` · downloading ${pull.model || "model"} ${pull.percent != null ? pull.percent + "%" : ""}`;
    }
    el.health.textContent =
      (h.ok ? "Ready" : "Not ready") + llmHint + " · " + parts.join(" · ");
    el.health.classList.toggle("bad", !h.ok && pull.status !== "pulling");
    el.play.disabled = !h.ok;
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

el.volume.addEventListener("input", setVolume);
setVolume();

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
  setInterval(refresh, 1000);
  setInterval(refreshHealth, 3000);
  setInterval(refreshLlmStatus, 1500);
})();
