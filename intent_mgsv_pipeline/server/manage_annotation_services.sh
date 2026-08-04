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

start_target() {
    case "$1" in
        all)
            start_target owner
            start_target peer
            start_target music-review
            ;;
        owner)
            start_service mgsv-owner \
                intent_mgsv_pipeline.server.owner_annotation_app 7860 \
                --owner-id "$OWNER_ID"
            ;;
        peer)
            start_service mgsv-peer \
                intent_mgsv_pipeline.server.peer_annotation_app 7861 \
                --owner-id "$OWNER_ID"
            ;;
        music-review)
            start_service mgsv-music-review \
                intent_mgsv_pipeline.server.music_review_app 7862 \
                --owner-id "$OWNER_ID"
            ;;
        *)
            return 2
            ;;
    esac
}

stop_target() {
    case "$1" in
        all)
            stop_target owner
            stop_target peer
            stop_target music-review
            ;;
        owner)
            stop_service mgsv-owner
            ;;
        peer)
            stop_service mgsv-peer
            ;;
        music-review)
            stop_service mgsv-music-review
            ;;
        *)
            return 2
            ;;
    esac
}

status_target() {
    case "$1" in
        all)
            status_target owner
            status_target peer
            status_target music-review
            ;;
        owner)
            status_service mgsv-owner
            ;;
        peer)
            status_service mgsv-peer
            ;;
        music-review)
            status_service mgsv-music-review
            ;;
        *)
            return 2
            ;;
    esac
}

usage() {
    echo "usage: $0 {start|stop|restart|status} [all|owner|peer|music-review]" >&2
}

ACTION="${1:-status}"
TARGET="${2:-all}"

case "$ACTION" in
    start)
        command -v tmux >/dev/null
        start_target "$TARGET" || {
            usage
            exit 2
        }
        ;;
    stop)
        stop_target "$TARGET" || {
            usage
            exit 2
        }
        ;;
    restart)
        "$0" stop "$TARGET"
        "$0" start "$TARGET"
        ;;
    status)
        status_target "$TARGET" || {
            usage
            exit 2
        }
        ;;
    *)
        usage
        exit 2
        ;;
esac
