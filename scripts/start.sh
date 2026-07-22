#!/usr/bin/env bash
# Back-compat wrapper → repo root start.sh
exec "$(cd "$(dirname "$0")/.." && pwd)/start.sh" "$@"
