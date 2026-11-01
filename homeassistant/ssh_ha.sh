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

# POST a JSON body to the Supervisor API with the token of the SSH app (HAOS)
# or of the CLI container (Supervised).
ha_api_post() {  # <path> <json>
    local runner="sh -s"
    [ "$HA_SUDO" = 1 ] && runner="docker exec -i hassio_cli sh -s"
    printf 'curl -sf -X POST -H "Authorization: Bearer $SUPERVISOR_TOKEN" -H "Content-Type: application/json" -d %s http://supervisor%s\n' \
        "'$2'" "$1" | ssh_ha "$runner"
}
