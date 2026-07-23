#!/usr/bin/env bash
# Stop ACE-Step API completely: pidfile tree, port listener, leftover acestep-api.
# The launcher is often `uv run acestep-api` (pidfile) with a child `acestep-api`
# that holds :8001 and GPU memory — killing only the parent leaves orphans.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PID_FILE="${ROOT}/data/acestep-api.pid"
PORT="${ACESTEP_API_PORT:-8001}"
HOST="${ACESTEP_API_HOST:-127.0.0.1}"

info() { echo "==> $*"; }
warn() { echo "!!  $*" >&2; }

_alive() { kill -0 "$1" 2>/dev/null; }

# Collect PIDs whose cmdline matches our project ACE install / acestep-api.
_find_ace_pids() {
  local pid cmd
  for pid in $(ps -eo pid=); do
    pid="${pid// /}"
    [[ -r "/proc/${pid}/cmdline" ]] || continue
    cmd="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
    case "$cmd" in
      *"${ROOT}/vendor/ACE-Step"*acestep* | *"${ROOT}/vendor/ACE-Step"*api_server*)
        echo "$pid"
        ;;
      *"uv run acestep-api"* | *"/acestep-api "* | *"/bin/acestep-api" | *"/bin/acestep-api "*)
        # Only if cwd or exe points under project vendor (avoid foreign installs)
        local cwd exe
        cwd="$(readlink -f "/proc/${pid}/cwd" 2>/dev/null || true)"
        exe="$(readlink -f "/proc/${pid}/exe" 2>/dev/null || true)"
        case "${cwd}${exe}" in
          *"${ROOT}/vendor/ACE-Step"* | *"${ROOT}/"*ace*)
            echo "$pid"
            ;;
        esac
        ;;
    esac
  done | sort -u
}

_find_pids_on_port() {
  local port="$1"
  local pids=""
  if command -v ss >/dev/null 2>&1; then
    pids="$(ss -lptn "sport = :${port}" 2>/dev/null | sed -n 's/.*pid=\([0-9]\+\).*/\1/p' | sort -u || true)"
  fi
  if [[ -z "${pids// }" ]] && command -v lsof >/dev/null 2>&1; then
    pids="$(lsof -nP -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null | sort -u || true)"
  fi
  echo "$pids"
}

# Recursively list pid + all descendants (depth-first leaves first for kill order).
_descendants_bottom_up() {
  local pid="$1"
  local c
  for c in $(ps -o pid= --ppid "$pid" 2>/dev/null | tr -d ' '); do
    [[ -n "$c" ]] || continue
    _descendants_bottom_up "$c"
  done
  echo "$pid"
}

_stop_pid() {
  local pid="$1"
  _alive "$pid" || return 0

  # Prefer killing the whole process group if this pid is a session/group leader.
  local pgid
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  if [[ -n "$pgid" && "$pgid" == "$pid" ]]; then
    info "Stopping process group -$pgid…"
    kill -TERM -- "-$pgid" 2>/dev/null || true
  else
    # Kill children first, then parent
    local tree
    tree="$(_descendants_bottom_up "$pid")"
    info "Stopping ACE tree: $(echo "$tree" | tr '\n' ' ')"
    for p in $tree; do
      kill -TERM "$p" 2>/dev/null || true
    done
  fi
}

_wait_gone() {
  local timeout_s="${1:-15}"
  local deadline=$((SECONDS + timeout_s))
  local left
  while (( SECONDS < deadline )); do
    left=0
    for pid in $(_find_ace_pids); do
      _alive "$pid" && left=1
    done
    for pid in $(_find_pids_on_port "$PORT"); do
      _alive "$pid" && left=1
    done
    if [[ -f "$PID_FILE" ]]; then
      local apid
      apid="$(tr -d ' \n' <"$PID_FILE" 2>/dev/null || true)"
      if [[ -n "$apid" ]] && _alive "$apid"; then
        left=1
      fi
    fi
    (( left == 0 )) && return 0
    sleep 0.2
  done
  return 1
}

_force_kill_remaining() {
  local pid
  warn "Sending SIGKILL to remaining ACE processes…"
  for pid in $(_find_ace_pids) $(_find_pids_on_port "$PORT"); do
    [[ -n "$pid" ]] || continue
    if _alive "$pid"; then
      # try process group then pid
      kill -KILL -- "-$pid" 2>/dev/null || true
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
  if [[ -f "$PID_FILE" ]]; then
    local apid
    apid="$(tr -d ' \n' <"$PID_FILE" 2>/dev/null || true)"
    if [[ -n "$apid" ]] && _alive "$apid"; then
      kill -KILL -- "-$apid" 2>/dev/null || true
      kill -KILL "$apid" 2>/dev/null || true
    fi
  fi
}

# --- main ---
stopped_any=0

if [[ -f "$PID_FILE" ]]; then
  apid="$(tr -d ' \n' <"$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$apid" ]] && _alive "$apid"; then
    _stop_pid "$apid"
    stopped_any=1
  else
    info "Stale pid file ($apid) — will clean by port/cmdline"
  fi
fi

# Port listener (often the python child, not the uv parent in the pidfile)
for pid in $(_find_pids_on_port "$PORT"); do
  [[ -n "$pid" ]] || continue
  cmd="$(tr '\0' ' ' <"/proc/${pid}/cmdline" 2>/dev/null || true)"
  case "$cmd" in
    *acestep* | *ACE-Step* | *uvicorn*)
      _stop_pid "$pid"
      stopped_any=1
      ;;
    *)
      warn "Port ${PORT} held by non-ACE process pid $pid: $cmd"
      warn "Not killing it. Free the port manually if needed."
      ;;
  esac
done

# Any leftover project ACE processes (orphans reparented to init)
for pid in $(_find_ace_pids); do
  if _alive "$pid"; then
    _stop_pid "$pid"
    stopped_any=1
  fi
done

if ! _wait_gone 15; then
  _force_kill_remaining
  _wait_gone 5 || true
fi

rm -f "$PID_FILE"

# Final report
left_pids="$(_find_ace_pids)"
port_pids="$(_find_pids_on_port "$PORT")"
if [[ -n "${left_pids// }" || -n "${port_pids// }" ]]; then
  warn "ACE may still be running: pids=${left_pids:-none} port_${PORT}=${port_pids:-none}"
  exit 1
fi

if curl -sf "http://${HOST}:${PORT}/health" >/dev/null 2>&1; then
  warn "Something still answers on ${HOST}:${PORT}/health"
  exit 1
fi

if [[ "$stopped_any" -eq 1 ]]; then
  info "ACE-Step API stopped (port ${PORT} free)"
else
  info "ACE-Step API was not running"
fi
