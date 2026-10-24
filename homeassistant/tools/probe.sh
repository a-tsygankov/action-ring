#!/usr/bin/env bash
# Run a probe file on the StreamDock N3 app and print its results, from the PC.
#
#   bash homeassistant/tools/probe.sh homeassistant/tools/probes/<group>.json
#
# Start the app under test on the TV first; the probe sends each candidate
# command (raw MRP, Companion, HA, YouTube Lounge, webOS) and the app logs
# what each returned as "probe: <n> <kind> ... -> ok|error ...". Watch the TV
# and note what it did. Needs the app running with the Apple TV connected
# (pair.sh atv; MRP steps also need pair.sh atv airplay).
#
# HA_HOST overrides the SSH host (default: homeassistant); PROBE_TIMEOUT the
# seconds to wait for "probe: done" (default 120).
set -euo pipefail

HOST="${HA_HOST:-homeassistant}"
SLUG=local_streamdock_n3
DIR=/app_configs/$SLUG
TIMEOUT="${PROBE_TIMEOUT:-120}"

ssh_ha() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$@"; }
die() { echo "ERROR: $*" >&2; exit 1; }

file=${1:-}
[ -n "$file" ] || { sed -n '2,13p' "$0"; exit 2; }
[ -f "$file" ] || die "no such file: $file"

ssh_ha 'ha apps info '"$SLUG"' | grep -q "^state: started"' \
    || die "app $SLUG is not running on $HOST (run homeassistant/restore.sh first)"

# A run id marks this run's lines in the log; the app consumes the request
# file as soon as it sees it, so it is written whole (tmp + mv).
run="$(basename "$file" .json)-$(date +%H%M%S)"
ssh_ha "cat > $DIR/probe_upload.json" < "$file"
note=$(ssh_ha "jq -r 'if type == \"object\" then .note // empty else empty end' $DIR/probe_upload.json") \
    || { ssh_ha "rm -f $DIR/probe_upload.json"; die "$file is not valid JSON"; }
[ -z "$note" ] || printf '%s\n\n' "$note"
ssh_ha "jq -c --arg run '$run' 'if type == \"array\" then {run: \$run, steps: .} else . + {run: \$run} end' \
    $DIR/probe_upload.json > $DIR/probe_request.json.tmp \
    && mv $DIR/probe_request.json.tmp $DIR/probe_request.json && rm -f $DIR/probe_upload.json"
echo "sent $file as run $run; waiting for results (up to ${TIMEOUT}s)"

# Poll the log; print this run's probe lines not printed yet.
printed=0
deadline=$(( $(date +%s) + TIMEOUT ))
while [ "$(date +%s)" -lt "$deadline" ]; do
    sleep 2
    lines=$(ssh_ha "ha apps logs $SLUG --lines 1000 2>&1" \
        | awk -v start="probe: start $run" 'index($0, start) {on = 1} on && /probe: / {print} on && /probe: done/ {exit}')
    count=$(printf '%s' "$lines" | grep -c 'probe: ' || true)
    if [ "$count" -gt "$printed" ]; then
        printf '%s\n' "$lines" | tail -n +$((printed + 1))
        printed=$count
    fi
    if printf '%s\n' "$lines" | grep -q 'probe: done'; then
        exit 0
    fi
done
if ssh_ha "test -f $DIR/probe_request.json"; then
    die "the app did not pick the request up (is the Apple TV connected? check: ssh $HOST ha apps logs $SLUG)"
fi
die "no 'probe: done' within ${TIMEOUT}s (PROBE_TIMEOUT raises it)"
