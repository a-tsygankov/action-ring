# DisplayPad

Shows streaming-app keys on a Mountain DisplayPad (USB `3282:0009`, 2×6 LCD
keys) whenever it is plugged into the Home Assistant host, and launches the
app on the Apple TV when a key is pressed. If the TV is off it is powered on
through the Apple TV, and the LG is switched to the Apple TV input first,
the same way the N3 dock's launch keys work.

Install alongside `streamdock_n3` (do not use `restore.sh` for this app):

```sh
tar -C homeassistant -cf - displaypad | ssh root@homeassistant.local \
  'rm -rf /local_apps/displaypad && tar -xf - -C /local_apps'
ssh root@homeassistant.local 'ha store reload && ha apps install local_displaypad && ha apps start local_displaypad'
```

## Configuration

`/addon_configs/local_displaypad/config.json` (created from
`config.default.json` on first start). Keys are numbered 1–12, row by row
from the top left; each maps an `icon` (a PNG in `icons/`) to an Apple TV
bundle id in `app`. `rotate` turns every icon (degrees, counter-clockwise).
Restart the app after editing.

A `displaypad.py` placed in the same folder replaces the built-in daemon,
for iterating without a rebuild.

## Protocol notes

- Interface 3 (hidraw): 64-byte command/event reports. Interface 1: one
  interrupt OUT endpoint for pixels, no kernel driver, written via libusb.
- The pad NAKs every command until it gets HID SET_IDLE and the keyboard
  SET_REPORT `03 01` on interface 0 (`handshake()`); the JeLuF Node library
  does not send these, AnnikenYT/oss-mountain-displaypad does.
- After the handshake it sends `ff aa …`, then echoes INIT (`11 80 00 00 01`).

Icons are copied from the N3 dock's set; see `icons/NOTICE.md` for sources,
licences (Hulu, Disney+, Prime Video and Max artwork is CC BY 4.0) and
trademarks.
