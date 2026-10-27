#!/bin/sh
# Config lives in the app's addon_config share, editable from the HA
# File editor / Samba as /addon_configs/local_streamdock_n3/config.json.
CONFIG=/config/config.json
[ -f "$CONFIG" ] || cp /opt/streamdock/config.default.json "$CONFIG"

# Marker for restore.sh: log lines after it belong to this start.
echo "run.sh started $(date +%s)"

VID=5548
PID=1001

# Prints "<busnum>:<devnum>" of the attached dock, or nothing. devnum changes
# on every replug, so a quick unplug/replug between polls is still caught.
dock_id() {
    for d in /sys/bus/usb/devices/*; do
        [ "$(cat "$d/idVendor" 2>/dev/null)" = "$VID" ] || continue
        [ "$(cat "$d/idProduct" 2>/dev/null)" = "$PID" ] || continue
        echo "$(cat "$d/busnum"):$(cat "$d/devnum")"
        return
    done
}

# Prints the dock's device nodes that cannot be opened: its hidraw nodes
# (read-write) and its input event nodes (read). After the dock drops off
# the bus and comes back, the Supervisor sometimes does not grant the new
# nodes to this container ("Operation not permitted"); only a fresh
# container gets them back.
blocked_nodes() {
    hid=$(printf '0003:%08X:%08X' "0x$VID" "0x$PID")
    for h in /sys/class/hidraw/hidraw*; do
        grep -qi "HID_ID=$hid" "$h/device/uevent" 2>/dev/null || continue
        n=/dev/${h##*/}
        # -c first: "<>" would create a plain file where the node is missing.
        # Subshells: a failed redirect on ":" exits a non-interactive shell.
        { [ -c "$n" ] && (: <> "$n"); } 2>/dev/null || echo "$n"
    done
    for e in /dev/input/by-id/*HOTSPOTEKUSB*event*; do
        [ -e "$e" ] || continue
        { [ -c "$e" ] && (: < "$e"); } 2>/dev/null || echo "$e"
    done
}

child=
stopping=
trap 'stopping=1; [ -n "$child" ] && kill "$child" 2>/dev/null' TERM INT

# The upstream daemon keeps running with a dead handle after the dock is
# unplugged, so watch the USB device here and restart the daemon whenever
# it disappears or re-enumerates.
waiting=
while [ -z "$stopping" ]; do
    id=$(dock_id)
    if [ -z "$id" ]; then
        [ -n "$waiting" ] || echo "waiting for dock ${VID}:${PID}"
        waiting=1
        sleep 2
        continue
    fi
    waiting=
    # Give the Supervisor time to grant cgroup access to the new nodes; it
    # grants them one by one (~0.2 s apart observed), so 1 s was too tight.
    sleep 2
    blocked=$(blocked_nodes)
    for _ in 1 2 3 4; do
        [ -z "$blocked" ] && break
        sleep 2
        blocked=$(blocked_nodes)
    done
    if [ -n "$blocked" ]; then
        # Exit with an error: the Supervisor watchdog (on for this app)
        # restarts the app in a fresh container, which regains the nodes.
        echo "dock nodes not accessible:" $blocked "- restarting the app"
        exit 1
    fi

    echo "dock found at usb ${id}; starting streamdock-n3"
    # Upstream daemon + live HA state on keys that set "state_entity".
    python /opt/streamdock/ha_daemon.py --config "$CONFIG" &
    child=$!
    while [ -z "$stopping" ] && kill -0 "$child" 2>/dev/null \
            && [ "$(dock_id)" = "$id" ]; do
        sleep 1
    done

    if kill -0 "$child" 2>/dev/null; then
        [ -n "$stopping" ] || echo "dock disconnected or re-enumerated; restarting"
        kill "$child" 2>/dev/null
    fi
    wait "$child"
    status=$?
    child=
    [ -n "$stopping" ] && break
    echo "streamdock-n3 exited ($status)"
    sleep 2
done
