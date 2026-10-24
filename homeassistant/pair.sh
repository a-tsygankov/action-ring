#!/usr/bin/env bash
# Pair / link the StreamDock N3 app with the TV-room devices, from the PC.
#
#   homeassistant/pair.sh atv                    Apple TV touch control (preview
#                                                scrubbing); asks for the PIN the
#                                                TV shows
#   homeassistant/pair.sh youtube <screen> [code]
#                                                Link a TV's YouTube app (screen
#                                                as in config "youtube.screens",
#                                                e.g. lg, atv); code from YouTube
#                                                > Settings > Link with TV code
#   homeassistant/pair.sh status                 What is paired / linked
#
# HA_HOST overrides the SSH host (default: homeassistant). Credentials and
# tokens stay on the Pi (app config folder), never in this repo.
set -euo pipefail

HOST="${HA_HOST:-homeassistant}"
SLUG=local_streamdock_n3
DIR=/app_configs/$SLUG

ssh_ha() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$@"; }
die() { echo "ERROR: $*" >&2; exit 1; }
mtime() { ssh_ha "stat -c %Y $1 2>/dev/null || echo 0"; }
last_log() { ssh_ha "ha apps logs $SLUG 2>&1 | grep '$1' | tail -${2:-1}"; }

# Poll (2 s) until the remote shell test $1 succeeds; give up after $2 tries.
wait_for() {
    for _ in $(seq 1 "$2"); do
        sleep 2
        ssh_ha "$1" && return 0
    done
    return 1
}

ssh_ha 'ha apps info '"$SLUG"' | grep -q "^state: started"' \
    || die "app $SLUG is not running on $HOST (run homeassistant/restore.sh first)"

pair_atv() {
    local addr before pin
    addr=$(ssh_ha 'jq -r "[.data.entries[] | select(.domain==\"apple_tv\")][0].data.address // empty" /homeassistant/.storage/core.config_entries')
    [ -n "$addr" ] || die "no Apple TV integration in Home Assistant; add it first"
    before=$(mtime "$DIR/atv_companion.json")

    echo "Turn the TV on. Asking the Apple TV for a PIN..."
    ssh_ha "rm -f $DIR/atv_pair_pin; echo '{\"address\": \"$addr\"}' > $DIR/atv_pair_request.json"
    wait_for "test ! -f $DIR/atv_pair_request.json || ha apps logs $SLUG 2>&1 | tail -5 | grep -q 'waiting for PIN'" 30 \
        || die "the app did not start pairing; check: ssh $HOST ha apps logs $SLUG"
    ssh_ha "test -f $DIR/atv_pair_request.json" \
        || die "$(last_log 'atv-touch:')"

    read -r -p "PIN shown on the TV: " pin
    [[ "$pin" =~ ^[0-9]{4}$ ]] || { ssh_ha "rm -f $DIR/atv_pair_request.json"; die "expected 4 digits"; }
    ssh_ha "echo $pin > $DIR/atv_pair_pin"

    wait_for "test ! -f $DIR/atv_pair_request.json" 20 || die "pairing timed out"
    [ "$(mtime "$DIR/atv_companion.json")" -gt "$before" ] || die "$(last_log 'atv-touch:')"
    echo "Paired. Waiting for the touch connection..."
    wait_for "ha apps logs $SLUG 2>&1 | grep 'atv-touch:' | tail -1 | grep -q 'connected to'" 15 \
        && last_log 'atv-touch:' || echo "Not connected yet; check: ssh $HOST ha apps logs $SLUG"
}

pair_youtube() {
    local screen=${1:-} code=${2:-} before
    [ -n "$screen" ] || die "usage: pair.sh youtube <screen> [code]"
    ssh_ha "jq -e --arg s '$screen' '.youtube.screens[\$s]' $DIR/config.json >/dev/null" \
        || die "no screen '$screen' under youtube.screens in the app config"
    if [ -z "$code" ]; then
        echo "On the TV: YouTube > Settings > Link with TV code."
        read -r -p "Code shown for '$screen': " code
    fi
    code=${code//[^0-9]/}
    [ "${#code}" -eq 12 ] || die "expected a 12-digit code"
    before=$(mtime "$DIR/yt_lounge.json")

    ssh_ha "echo $code > $DIR/yt_pair_$screen"
    wait_for "test ! -f $DIR/yt_pair_$screen" 15 \
        || die "the app did not pick the code up (is a session for '$screen' open?)"
    if wait_for "[ \$(stat -c %Y $DIR/yt_lounge.json 2>/dev/null || echo 0) -gt $before ]" 10; then
        last_log "youtube: $screen:"
    else
        die "$(last_log "youtube: $screen:")"
    fi
}

status() {
    echo "Apple TV touch:"
    if ssh_ha "test -f $DIR/atv_companion.json"; then
        echo "  paired; $(last_log 'atv-touch:' | sed 's/^atv-touch: //')"
    else
        echo "  not paired (pair.sh atv)"
    fi
    echo "YouTube:"
    local linked screens
    linked=$(ssh_ha "jq -r 'keys[]' $DIR/yt_lounge.json 2>/dev/null" || true)
    # Screens configured in the live app config (same as the repo after restore).
    screens=$(ssh_ha "jq -r '.youtube.screens // {} | keys[]' $DIR/config.json")
    for screen in $screens; do
        if printf '%s\n' "$linked" | grep -qx "$screen"; then
            echo "  $screen: linked"
        else
            echo "  $screen: not linked (pair.sh youtube $screen)"
        fi
    done
}

case "${1:-}" in
    atv) pair_atv ;;
    youtube) shift; pair_youtube "$@" ;;
    status) status ;;
    *) sed -n '2,15p' "$0"; exit 2 ;;
esac
