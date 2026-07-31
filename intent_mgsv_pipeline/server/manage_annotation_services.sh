#!/usr/bin/env bash
set -euo pipefail

ROOT="${MGSV_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
DB="${MGSV_DB:-$ROOT/outputs/server/intent_mgsv.sqlite3}"
HOST="${MGSV_ANNOTATION_HOST:-0.0.0.0}"
OWNER_ID="${MGSV_OWNER_ID:-owner}"
PYTHON_BIN="${MGSV_PYTHON:-$(command -v python)}"
LOG_DIR="${MGSV_SERVICE_LOG_DIR:-$ROOT/outputs/server/logs}"

mkdir -p "$LOG_DIR"

start_service() {
    local session="$1"
    local module="$2"
    local port="$3"
    shift 3
    if tmux has-session -t "$session" 2>/dev/null; then
        echo "$session already running"
        return
    fi
    local log="$LOG_DIR/$session.log"
    tmux new-session -d -s "$session" \
        "cd '$ROOT' && exec '$PYTHON_BIN' -m '$module' --db '$DB' --host '$HOST' --port '$port' $* >> '$log' 2>&1"
    echo "started $session on port $port; log: $log"
}

stop_service() {
    local session="$1"
    if tmux has-session -t "$session" 2>/dev/null; then
        tmux kill-session -t "$session"
        echo "stopped $session"
    else
        echo "$session is not running"
    fi
}

status_service() {
    local session="$1"
    if tmux has-session -t "$session" 2>/dev/null; then
        echo "$session: running"
    else
        echo "$session: stopped"
    fi
}

case "${1:-status}" in
    start)
        command -v tmux >/dev/null
        start_service mgsv-owner \
            intent_mgsv_pipeline.server.owner_annotation_app 7860 \
            --owner-id "$OWNER_ID"
        start_service mgsv-peer \
            intent_mgsv_pipeline.server.peer_annotation_app 7861 \
            --owner-id "$OWNER_ID"
        start_service mgsv-music-review \
            intent_mgsv_pipeline.server.music_review_app 7862 \
            --owner-id "$OWNER_ID"
        ;;
    stop)
        stop_service mgsv-owner
        stop_service mgsv-peer
        stop_service mgsv-music-review
        ;;
    restart)
        "$0" stop
        "$0" start
        ;;
    status)
        status_service mgsv-owner
        status_service mgsv-peer
        status_service mgsv-music-review
        ;;
    *)
        echo "usage: $0 {start|stop|restart|status}" >&2
        exit 2
        ;;
esac
