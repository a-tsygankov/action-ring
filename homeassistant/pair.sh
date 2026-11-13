#!/usr/bin/env bash
# Pair / link the StreamDock N3 app with the TV-room devices, from the PC.
#
#   homeassistant/pair.sh atv [airplay]          Apple TV touch control (preview
#                                                scrubbing); asks for the PIN the
#                                                TV shows. "airplay" adds the
#                                                AirPlay pairing: the app's own
#                                                media-protocol session, which ad
#                                                muting (Pluto, Peacock) and
#                                                mrp_raw.log need
#   homeassistant/pair.sh youtube <screen> [code]
#                                                Link a TV's YouTube app (screen
#                                                as in config "youtube.screens",
#                                                e.g. lg, atv); code from YouTube
#                                                > Settings > Link with TV code
#   homeassistant/pair.sh youtube-rating [client.json]
#                                                Sign the app in to Google for
#                                                like / dislike (YouTube Data
#                                                API); client.json is the OAuth
#                                                client ("TVs and Limited Input
#                                                devices") from the Cloud console,
#                                                needed the first time only
#   homeassistant/pair.sh status                 What is paired / linked
#
# HA_HOST overrides the SSH host (default: homeassistant). The Apple TV is
# the one whose HA entry owns the app's "atv.media_player" entity (not just
# the first Apple TV in HA); ATV_ADDRESS overrides it. Credentials and tokens
# stay on the Pi (app config folder), never in this repo.
set -euo pipefail

HOST="${HA_HOST:-homeassistant}"
SLUG=local_streamdock_n3
DIR=/app_configs/$SLUG

# shellcheck source=ssh_ha.sh
. "$(cd "$(dirname "$0")" && pwd)/ssh_ha.sh"
die() { echo "ERROR: $*" >&2; exit 1; }
mtime() { ssh_ha "stat -c %Y $1 2>/dev/null || echo 0"; }
atv_entity() { ssh_ha "jq -r '.atv.media_player // \"the Apple TV media player\"' $DIR/config.json"; }
last_log() { ssh_ha "ha apps logs $SLUG --lines 1000 2>&1 | grep '$1' | tail -${2:-1}"; }

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
    local protocol=${1:-companion} addr before pin
    case "$protocol" in companion|airplay) ;; *) die "protocol: companion or airplay" ;; esac
    addr=$(atv_address "$DIR/config.json")
    [ -n "$addr" ] || die "no Apple TV entry in Home Assistant owns $(atv_entity); add the
Apple TV integration, or rename the new box's entities to match. Apple TV entries:
$(atv_entries)
(or set ATV_ADDRESS=<ip>)"
    before=$(mtime "$DIR/atv_companion.json")

    echo "Turn the TV on. Asking the Apple TV for a PIN..."
    ssh_ha "rm -f $DIR/atv_pair_pin; echo '{\"address\": \"$addr\", \"protocol\": \"$protocol\"}' > $DIR/atv_pair_request.json"
    wait_for "test ! -f $DIR/atv_pair_request.json || ha apps logs $SLUG --lines 1000 2>&1 | tail -5 | grep -q 'waiting for PIN'" 30 \
        || die "the app did not start pairing; check: ssh $HOST ha apps logs $SLUG"
    ssh_ha "test -f $DIR/atv_pair_request.json" \
        || die "$(last_log 'atv-touch:')"

    read -r -p "PIN shown on the TV: " pin
    [[ "$pin" =~ ^[0-9]{4}$ ]] || { ssh_ha "rm -f $DIR/atv_pair_request.json"; die "expected 4 digits"; }
    ssh_ha "echo $pin > $DIR/atv_pair_pin"

    wait_for "test ! -f $DIR/atv_pair_request.json" 20 || die "pairing timed out"
    [ "$(mtime "$DIR/atv_companion.json")" -gt "$before" ] || die "$(last_log 'atv-touch:')"
    echo "Paired. Waiting for the touch connection..."
    wait_for "ha apps logs $SLUG --lines 1000 2>&1 | grep 'atv-touch:' | tail -1 | grep -q 'connected to'" 15 \
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

pair_youtube_rating() {
    local client=${1:-} before line
    if [ -n "$client" ]; then
        [ -f "$client" ] || die "no such file: $client"
        # jq on the Pi: the PC (Git Bash) usually has none.
        ssh_ha "jq -e '(.installed // .web // .) | .client_id and .client_secret' >/dev/null" < "$client" \
            || die "$client has no client_id / client_secret"
        ssh_ha "umask 077; cat > $DIR/yt_oauth_client.json" < "$client"
    fi
    ssh_ha "test -f $DIR/yt_oauth_client.json" \
        || die "no OAuth client on the Pi yet: pair.sh youtube-rating <client.json>"
    ssh_ha "jq -e '.youtube.rating' $DIR/config.json >/dev/null" \
        || echo "Note: \"youtube\": {\"rating\": true} is not in the app config; restore it first."
    before=$(ssh_ha "ha apps logs $SLUG --lines 1000 2>&1 | grep -c 'youtube-rating: sign-in' || true")
    ssh_ha "touch $DIR/yt_rate_auth"
    wait_for "[ \$(ha apps logs $SLUG --lines 1000 2>&1 | grep -c 'youtube-rating: sign-in') -gt $before ]" 15 \
        || die "the app did not start the sign-in (is \"rating\" on and the app restarted?)"
    line=$(last_log 'youtube-rating: sign-in')
    echo "$line"
    case "$line" in *"enter "*) ;; *) exit 1 ;; esac
    echo "Sign in with the Google account that is signed in to YouTube on the TV."
    echo "(An unverified-app warning is expected: Advanced > Go to <app>.) Waiting..."
    wait_for "ha apps logs $SLUG --lines 1000 2>&1 | grep 'youtube-rating: sign-in' | tail -1 | grep -qv 'enter '" 450 \
        || die "no answer yet; check: ssh $HOST ha apps logs $SLUG"
    line=$(last_log 'youtube-rating: sign-in')
    echo "$line"
    case "$line" in *"done"*) ;; *) exit 1 ;; esac
}

status() {
    local paired addr
    echo "Apple TV touch:"
    if ssh_ha "test -f $DIR/atv_companion.json"; then
        echo "  paired; $(last_log 'atv-touch:' | sed 's/^atv-touch: //')"
        # A replaced box (or a new IP) leaves credentials for another address.
        paired=$(ssh_ha "jq -r '.address // empty' $DIR/atv_companion.json")
        addr=$(atv_address "$DIR/config.json")
        if [ -n "$addr" ] && [ "$paired" != "$addr" ]; then
            echo "  WARNING: paired with $paired, but HA's $(atv_entity) is at $addr;"
            echo "  re-pair: remove $DIR/atv_companion.json, then pair.sh atv (and atv airplay)"
        fi
    else
        echo "  not paired (pair.sh atv)"
    fi
    if ssh_ha "jq -e '.airplay_credentials' $DIR/atv_companion.json >/dev/null 2>&1"; then
        echo "  AirPlay (media-protocol session for ad muting): paired"
    else
        echo "  AirPlay (media-protocol session for ad muting): not paired (pair.sh atv airplay)"
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
    if ssh_ha "test -f $DIR/yt_oauth.json"; then
        echo "  like / dislike: signed in"
    else
        echo "  like / dislike: not signed in (pair.sh youtube-rating <client.json>)"
    fi
}

case "${1:-}" in
    atv) shift; pair_atv "$@" ;;
    youtube) shift; pair_youtube "$@" ;;
    youtube-rating) shift; pair_youtube_rating "$@" ;;
    status) status ;;
    *) sed -n '2,28p' "$0"; exit 2 ;;
esac
