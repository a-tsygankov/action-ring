#!/bin/sh
# Config lives in the app's addon_config share, editable from the HA
# File editor / Samba as /addon_configs/local_displaypad/config.json.
CONFIG=/config/config.json
[ -f "$CONFIG" ] || cp /opt/displaypad/config.default.json "$CONFIG"

# A displaypad.py dropped into the same share overrides the built-in one, so
# the daemon can be iterated on with a restart instead of a rebuild.
SCRIPT=/opt/displaypad/displaypad.py
[ -f /config/displaypad.py ] && SCRIPT=/config/displaypad.py && echo "using $SCRIPT"

exec python "$SCRIPT" --config "$CONFIG" --icons /opt/displaypad/icons
