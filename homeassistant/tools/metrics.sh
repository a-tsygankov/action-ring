#!/usr/bin/env bash
# Show the StreamDock N3 app's service metrics as a ranked table, from the PC.
#
#   bash homeassistant/tools/metrics.sh [--by cpu|wall|http|errors] [--window interval|total] [host]
#
# The app rewrites /config/metrics.json every metrics.log_every_s (600 s);
# this reads that snapshot over SSH and formats it here with
# services/metrics.py. --by picks the ranking (default cpu), --window the
# figures: "interval" since the last summary (default) or "total" since start.
# Needs python on the PC. HA_HOST overrides the SSH host (default:
# homeassistant); a "host" argument overrides both.
set -euo pipefail

HOST="${HA_HOST:-homeassistant}"
SLUG=local_streamdock_n3
BY=cpu
WINDOW=interval

here="$(cd "$(dirname "$0")/.." && pwd)"
die() { echo "ERROR: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --by) [ $# -ge 2 ] || die "--by needs cpu|wall|http|errors"; BY=$2; shift 2 ;;
        --window) [ $# -ge 2 ] || die "--window needs interval|total"; WINDOW=$2; shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        -*) die "unknown option: $1" ;;
        *) HOST=$1; shift ;;
    esac
done
case "$BY" in cpu|wall|http|errors) ;; *) die "--by must be cpu, wall, http or errors" ;; esac
case "$WINDOW" in interval|total) ;; *) die "--window must be interval or total" ;; esac

# Sourced last: it probes HOST over SSH as soon as it is loaded.
# shellcheck source=../ssh_ha.sh
. "$here/ssh_ha.sh"

ssh_ha "cat /app_configs/$SLUG/metrics.json" \
    | python "$here/streamdock_n3/services/metrics.py" --by "$BY" --window "$WINDOW"
