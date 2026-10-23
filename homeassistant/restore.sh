#!/usr/bin/env bash
# Restore the ActionRing N3 / Home Assistant setup from this repo onto a
# Home Assistant OS device. Idempotent: safe to re-run on a configured device.
#
#   homeassistant/restore.sh [ssh-host]        (default host: homeassistant)
#
# Prerequisites (manual, see docs/JOURNAL.md "Restore"): HAOS onboarded,
# Terminal & SSH app accepting this PC's key, integrations paired, dock
# plugged into the Pi.
set -euo pipefail

HOST="${1:-homeassistant}"
HERE="$(cd "$(dirname "$0")" && pwd)"
APP=streamdock_n3
SLUG=local_streamdock_n3
STAMP=$(date +%Y%m%d-%H%M%S)

ssh_ha() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$@"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'WARNING: %s\n' "$*" >&2; }

step "Checking SSH access to $HOST"
if ! ssh_ha 'ha core info >/dev/null'; then
    echo "Cannot run 'ha' on $HOST over SSH. See docs/JOURNAL.md > Restore > Prerequisites." >&2
    exit 1
fi

# --- Entities the mappings depend on -------------------------------------
step "Checking referenced entities exist"
# Values of entity_id in the mappings (JSON with escaped quotes) and scripts
# (YAML). script.* is skipped: those are created by the next step.
entities=$(cat "$HERE/$APP/config.tv_room.json" "$HERE"/ha-config/scripts/*.yaml \
    | grep -oE '(entity_id|state_entity|remote|media_player)[\\":[:space:]]+[a-z_]+\.[a-z0-9_]+' \
    | grep -oE '[a-z_]+\.[a-z0-9_]+$' | grep -v '^script\.' | sort -u)
missing=$(printf '%s\n' "$entities" | ssh_ha '
    known=$(jq -r ".data.entities[].entity_id" /homeassistant/.storage/core.entity_registry)
    while read -r e; do printf "%s\n" "$known" | grep -qx "$e" || echo "$e"; done')
if [ -n "$missing" ]; then
    warn "these entities are not in Home Assistant yet (pair the integration or rename the entity):"
    printf '  %s\n' $missing >&2
else
    echo "all $(printf '%s\n' "$entities" | wc -l | tr -d ' ') entities present"
fi

# --- HA scripts -----------------------------------------------------------
step "Home Assistant scripts"
core_restart=
for frag in "$HERE"/ha-config/scripts/*.yaml; do
    key=$(head -1 "$frag" | sed 's/:.*//')
    result=$(ssh_ha "f=/homeassistant/scripts.yaml; touch \$f
        if grep -q '^$key:' \$f; then echo present; exit; fi
        cp \$f \$f.bak-$STAMP
        # A fresh install may hold an empty mapping/list literal; drop it.
        if grep -qxE '\\{\\}|\\[\\]' \$f; then : > \$f; fi
        [ -s \$f ] && [ -n \"\$(tail -c1 \$f)\" ] && echo >> \$f
        cat >> \$f; echo added" < "$frag")
    echo "$key: $result"
    [ "$result" = added ] && core_restart=1
done
if [ -n "$core_restart" ]; then
    ssh_ha 'ha core check && ha core restart'
fi

# --- App ------------------------------------------------------------------
step "Staging app"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp -r "$HERE/$APP" "$stage/"
# The input by-id link embeds the dock's serial; use the one on the target.
link=$(ssh_ha 'ls /dev/input/by-id/ 2>/dev/null | grep -m1 "HOTSPOTEKUSB.*event-kbd"' || true)
if [ -n "$link" ]; then
    sed -i "s#/dev/input/by-id/usb-HOTSPOTEKUSB[^ ]*-event-kbd#/dev/input/by-id/$link#" \
        "$stage/$APP/config.yaml"
    echo "dock input link: $link"
else
    warn "dock not detected on $HOST; keeping the by-id link from the repo"
fi

step "Uploading app to /local_apps/$APP"
tar -C "$stage" -cf - "$APP" | ssh_ha "rm -rf /local_apps/$APP && mkdir -p /local_apps \
    && tar -xf - -C /local_apps && chown -R root:root /local_apps/$APP"

step "Installing / rebuilding app (builds on the device, takes a few minutes)"
# install when absent, update when config.yaml's version was bumped,
# otherwise rebuild (the Supervisor refuses rebuild across a version change).
ssh_ha "ha store reload >/dev/null
    info=\$(ha apps info $SLUG)
    cur=\$(printf '%s\n' \"\$info\" | sed -n 's/^version: //p')
    new=\$(printf '%s\n' \"\$info\" | sed -n 's/^version_latest: //p')
    if [ \"\$cur\" = null ]; then
        ha apps install $SLUG
    elif [ \"\$cur\" != \"\$new\" ]; then
        echo \"updating \$cur -> \$new\"; ha apps update $SLUG
    else
        ha apps rebuild $SLUG
    fi"

step "App config (mappings)"
ssh_ha "d=/app_configs/$SLUG; mkdir -p \$d
    [ -f \$d/config.json ] && cp \$d/config.json \$d/config.json.bak-$STAMP
    cat > \$d/config.json" < "$HERE/$APP/config.tv_room.json"

step "App icons"
# Key images referenced as /config/icons/... in the mappings.
tar -C "$HERE/$APP" -cf - icons | ssh_ha "tar -xf - -C /app_configs/$SLUG"
ssh_ha "ls /app_configs/$SLUG/icons"

step "Starting app"
t0=$(ssh_ha 'date +%s')
ssh_ha "ha apps restart $SLUG >/dev/null 2>&1 || ha apps start $SLUG"
# Only judge log lines after this start's "run.sh started <epoch>" marker;
# older runs and button presses would otherwise mask the result.
since_start() {
    ssh_ha "ha apps logs $SLUG 2>&1 | tail -400" | awk -v t0="$t0" '
        /^run\.sh started / { on = ($3 >= t0); buf = "" }
        on { buf = buf $0 "\n" }
        END { printf "%s", buf }'
}
started=
for _ in $(seq 1 15); do
    sleep 3
    log=$(since_start)
    if printf '%s' "$log" | grep -q 'controller running'; then
        printf '%s' "$log" | head -8
        started="app running and dock connected"
        break
    elif printf '%s' "$log" | grep -q 'waiting for dock'; then
        started="app running, waiting for the dock to be plugged in"
        break
    fi
done
if [ -z "$started" ]; then
    printf '%s' "$log" | tail -15
    warn "app did not report a running controller; check: ssh $HOST ha apps logs $SLUG"
    exit 1
fi

# --- Apple TV Companion pairing (touch / preview scrubbing) ---------------
step "Apple TV touch pairing"
CONF_DIR=/app_configs/$SLUG
if ! grep -qE '"scrub": *"touch"' "$HERE/$APP/config.tv_room.json"; then
    echo "touch scrubbing not enabled in config; skipped"
elif ssh_ha "test -f $CONF_DIR/atv_companion.json"; then
    echo "already paired"
else
    addr=$(ssh_ha 'jq -r "[.data.entries[] | select(.domain==\"apple_tv\")][0].data.address // empty" /homeassistant/.storage/core.config_entries')
    if [ -z "$addr" ]; then
        warn "no Apple TV integration in HA yet; pair later (see DOCS.md > Preview scrubbing)"
    elif [ ! -t 0 ]; then
        warn "not interactive; pair later by re-running this script in a terminal (see DOCS.md)"
    else
        ssh_ha "echo '{\"address\": \"$addr\"}' > $CONF_DIR/atv_pair_request.json"
        echo "waiting for the TV to show a PIN..."
        for _ in $(seq 1 20); do
            sleep 2
            since_start | grep -q 'pairing: waiting for PIN' && break
        done
        read -r -p "PIN shown on the TV: " pin
        case "$pin" in
            ''|*[!0-9]*) warn "not a number; pairing abandoned"; exit 1 ;;
        esac
        ssh_ha "echo $pin > $CONF_DIR/atv_pair_pin"
        for _ in $(seq 1 15); do
            sleep 2
            if ssh_ha "test -f $CONF_DIR/atv_companion.json"; then echo "paired"; break; fi
        done
        ssh_ha "test -f $CONF_DIR/atv_companion.json" \
            || warn "pairing did not complete; check: ssh $HOST ha apps logs $SLUG"
    fi
fi

step "Done: $started"
