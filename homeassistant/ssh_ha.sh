# Sourced by restore.sh, pair.sh and tools/probe.sh (after HOST is set): run
# commands on the Home Assistant host over SSH.
#
# Home Assistant OS: HOST is the Terminal & SSH app (root, the ha CLI,
# /homeassistant, /local_apps, /app_configs).
#
# Home Assistant Supervised on a Linux host (Ubuntu, Debian): HOST is the
# host's own SSH, as a user with passwordless sudo. Detected by
# /etc/hassio.json; every command then runs under "sudo -n sh -c", and
# ha_link_paths (restore.sh) links the app's paths to the Supervisor data
# folders, so the same commands work on both.

ssh_ha_raw() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$HOST" "$@"; }

if [ -z "${HA_SUDO:-}" ]; then
    if ssh_ha_raw 'test -f /etc/hassio.json' 2>/dev/null; then HA_SUDO=1; else HA_SUDO=0; fi
fi
export HA_SUDO

ssh_ha() {
    if [ "$HA_SUDO" = 1 ]; then
        # Single-quote the command for the remote login shell; sh -c expands it.
        local q
        q=$(printf '%s' "$*" | sed "s/'/'\\\\''/g")
        ssh_ha_raw "sudo -n sh -c '$q'"
    else
        ssh_ha_raw "$@"
    fi
}

# Supervised only: /homeassistant, /local_apps and /app_configs as links into
# the Supervisor data folder ("data" in /etc/hassio.json). Idempotent.
ha_link_paths() {
    [ "$HA_SUDO" = 1 ] || return 0
    ssh_ha 'd=$(jq -r .data /etc/hassio.json)
        for p in homeassistant:homeassistant local_apps:apps/local app_configs:app_configs; do
            l=/${p%%:*}; t=$d/${p#*:}
            mkdir -p "$t"
            [ -e "$l" ] || ln -s "$t" "$l"
            echo "$l -> $(readlink -f "$l")"
        done'
}

# The address of the Apple TV the app drives: HA's apple_tv config entry that
# owns the app's media player ("atv.media_player" in the app's config.json,
# e.g. media_player.vugitv_4k_vugitv_4k). Keyed on the entity, not on the
# entry order, so a second Apple TV in HA (an old box during a replacement,
# another room) is never picked by mistake. With no "atv.media_player" in the
# config, the only apple_tv entry. Empty when none matches. ATV_ADDRESS
# overrides the lookup.
atv_address() {  # <app config.json on the host>
    if [ -n "${ATV_ADDRESS:-}" ]; then
        echo "$ATV_ADDRESS"
        return
    fi
    ssh_ha 'st=/homeassistant/.storage
        e=$(jq -r ".atv.media_player // empty" '"$1"' 2>/dev/null)
        if [ -n "$e" ]; then
            id=$(jq -r --arg e "$e" "first(.data.entities[] | select(.entity_id == \$e) | .config_entry_id) // empty" $st/core.entity_registry)
            [ -n "$id" ] || exit 0
            jq -r --arg id "$id" "first(.data.entries[] | select(.entry_id == \$id and .domain == \"apple_tv\") | .data.address) // empty" $st/core.config_entries
        else
            jq -r "[.data.entries[] | select(.domain == \"apple_tv\")] | if length == 1 then .[0].data.address else empty end" $st/core.config_entries
        fi'
}

# Every apple_tv config entry as "<title> <address>", for error messages.
atv_entries() {
    ssh_ha 'jq -r ".data.entries[] | select(.domain == \"apple_tv\") | \"  \\(.title) \\(.data.address)\"" /homeassistant/.storage/core.config_entries'
}

# POST a JSON body to the Supervisor API with the token of the SSH app (HAOS)
# or of the CLI container (Supervised).
ha_api_post() {  # <path> <json>
    local runner="sh -s"
    [ "$HA_SUDO" = 1 ] && runner="docker exec -i hassio_cli sh -s"
    printf 'curl -sf -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" -H "Content-Type: application/json" -d %s http://supervisor%s\n' \
        "'$2'" "$1" | ssh_ha "$runner"
}
