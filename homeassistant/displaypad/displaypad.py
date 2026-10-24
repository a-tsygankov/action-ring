"""Mountain DisplayPad as a streaming-app launcher for the TV Room.

Keeps one app icon per configured key on the pad while it is plugged in, and
launches that app on the Apple TV when the key is pressed (TV powered on and
the LG switched to the Apple TV input first, as the N3 dock does).

Protocol (from JeLuF/mountain-displaypad and AnnikenYT/oss-mountain-displaypad,
USB 3282:0009):
- On connect the pad needs HID SET_IDLE and a keyboard SET_REPORT (see
  handshake()); until then it NAKs all commands.
- Interface 3 is a vendor HID interface (hidraw, 64-byte reports). Commands
  go out on it; key events and image-transfer acks come back on it.
- Interface 1 has a single interrupt OUT endpoint (0x02) and no kernel
  driver; the pixel data is written to it through libusb.
- Image transfer: send IMG with the key index, wait for the 0x21 00 00 ack,
  write 306 zero bytes + 102x102 BGR pixels (padded to 31438 bytes) in
  1024-byte chunks, wait for the 0x21 00 ff ack. If an ack is missed the pad
  usually needs INIT again.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import threading
import time
import urllib.request

import usb.core
import usb.util
from PIL import Image

VID, PID = 0x3282, 0x0009
NUM_KEYS = 12
ICON_SIZE = 102
HEADER_SIZE = 306
PIXEL_SIZE = 31438
CHUNK = 1024
PIXEL_EP = 0x02
PIXEL_IFACE = 1
ACK_TIMEOUT = 1.0

REPORT = 65  # report number 0 + 64-byte output report
INIT_MSG = bytes([0x00, 0x11, 0x80, 0x00, 0x00, 0x01]).ljust(REPORT, b"\0")
IMG_MSG = bytes([0x00, 0x21, 0x00, 0x00, 0x00, 0xFF, 0x3D, 0x00, 0x00, 0x65, 0x65])

# Key event: (byte, mask) per key 1..12, row by row from the top left.
KEY_BITS = [(42, 0x02), (42, 0x04), (42, 0x08), (42, 0x10), (42, 0x20), (42, 0x40),
            (42, 0x80), (47, 0x01), (47, 0x02), (47, 0x04), (47, 0x08), (47, 0x10)]

API = "http://supervisor/core/api"


def log(msg: str) -> None:
    print(msg, flush=True)


# --- Home Assistant -------------------------------------------------------

def _request(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": "Bearer " + os.environ.get("SUPERVISOR_TOKEN", ""),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read() or b"null")


def call(domain: str, service: str, data: dict) -> None:
    try:
        _request("POST", f"/services/{domain}/{service}", data)
    except Exception as exc:  # noqa: BLE001 - logged, the press just fails
        log(f"call {domain}.{service} failed: {exc}")


def state(entity: str) -> dict:
    try:
        return _request("GET", f"/states/{entity}") or {}
    except Exception as exc:  # noqa: BLE001
        log(f"state {entity} failed: {exc}")
        return {}


def restart_self() -> None:
    """Have the Supervisor restart this app; exits if that is refused."""
    req = urllib.request.Request(
        "http://supervisor/addons/self/restart",
        method="POST",
        headers={"Authorization": "Bearer " + os.environ.get("SUPERVISOR_TOKEN", "")},
    )
    try:
        urllib.request.urlopen(req, timeout=10).close()
        time.sleep(30)  # the Supervisor stops this container meanwhile
    except Exception as exc:  # noqa: BLE001
        log(f"self restart failed: {exc}")
    raise SystemExit(1)


def launch(cfg: dict, app: str) -> None:
    """Power the TV on via the Apple TV, select its input, start `app`."""
    lg, atv = cfg["lg"], cfg["atv"]
    if state(lg).get("state") != "on":
        # The Apple TV's HDMI-CEC wake-up powers the LG; remote.turn_on also
        # heals a disconnected HA remote.
        call("remote", "turn_on", {"entity_id": cfg["atv_remote"]})
        time.sleep(2)
        call("media_player", "turn_on", {"entity_id": atv})
        deadline = time.monotonic() + float(cfg.get("power_on_wait", 25))
        while state(lg).get("state") != "on":
            if time.monotonic() >= deadline:
                log("launch: TV did not turn on")
                return
            time.sleep(0.5)
    if (state(lg).get("attributes") or {}).get("source") != cfg["atv_source"]:
        call("media_player", "select_source", {"entity_id": lg, "source": cfg["atv_source"]})
    call("media_player", "play_media",
         {"entity_id": atv, "media_content_type": "app", "media_content_id": app})
    log(f"launched {app}")


# --- Device ---------------------------------------------------------------

def find_hidraw() -> str | None:
    """The hidraw node of the pad's vendor interface (USB interface 3)."""
    for node in glob.glob("/sys/class/hidraw/hidraw*"):
        dev = os.path.realpath(os.path.join(node, "device"))
        if f"{VID:04X}:{PID:04X}" in os.path.basename(dev).upper() \
                and os.path.dirname(dev).endswith(":1.3"):
            return "/dev/" + os.path.basename(node)
    return None


def ensure_usb_node() -> bool:
    """Create the pad's /dev/bus/usb node if the container lacks it.

    The container's /dev/bus/usb is populated at start, so after a replug
    (new device number) libusb cannot see the pad. The node is recreated
    from sysfs; False if the pad is absent or the node cannot be made.
    """
    for d in glob.glob("/sys/bus/usb/devices/*"):
        try:
            with open(d + "/idVendor") as f:
                vid = int(f.read(), 16)
            with open(d + "/idProduct") as f:
                pid = int(f.read(), 16)
            if (vid, pid) != (VID, PID):
                continue
            with open(d + "/busnum") as f:
                bus = int(f.read())
            with open(d + "/devnum") as f:
                num = int(f.read())
            with open(d + "/dev") as f:
                major, minor = map(int, f.read().split(":"))
        except (OSError, ValueError):
            continue
        node = f"/dev/bus/usb/{bus:03d}/{num:03d}"
        if not os.path.exists(node):
            try:
                os.makedirs(os.path.dirname(node), exist_ok=True)
                os.mknod(node, 0o664 | 0o020000, os.makedev(major, minor))
                log(f"created {node}")
            except OSError as exc:
                log(f"cannot create {node}: {exc}")
                return False
        return True
    return False


class Disconnected(Exception):
    pass


class NoInit(Disconnected):
    pass


def handshake(dev) -> None:
    """HID SET_IDLE + the keyboard SET_REPORT Base Camp sends at start-up.

    Without it the pad NAKs every command on interface 3 (hidraw writes time
    out). Interface 0 is briefly taken from usbhid for the SET_REPORT; the
    SET_IDLE on interface 3 fails while usbhid holds it, which is harmless.
    """
    detached = False
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
        detached = True
    try:
        usb.util.claim_interface(dev, 0)
        for iface in (0, PIXEL_IFACE):
            dev.ctrl_transfer(0x21, 0x0A, 0x0000, iface, None, timeout=500)
        dev.ctrl_transfer(0x21, 0x09, 0x0203, 0, bytes([0x03, 0x01]), timeout=500)
    finally:
        try:
            usb.util.release_interface(dev, 0)
        except usb.core.USBError:
            pass
        if detached:
            dev.attach_kernel_driver(0)


def reset_usb() -> None:
    """Last resort for a pad that ignores INIT: re-enumerate it."""
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    if dev is not None:
        try:
            dev.reset()
            log("USB reset")
        except usb.core.USBError as exc:
            log(f"USB reset failed: {exc}")


class Pad:
    def __init__(self, on_press):
        self.usb = usb.core.find(idVendor=VID, idProduct=PID)
        if self.usb is None:
            raise Disconnected("USB device not found")
        # Claim the pixel interface; interface 0 is only borrowed inside
        # handshake(). No set_configuration(): that would unbind usbhid from
        # interface 3 (our hidraw).
        usb.util.claim_interface(self.usb, PIXEL_IFACE)
        handshake(self.usb)
        time.sleep(1)
        hidraw = find_hidraw()
        if not hidraw:
            raise Disconnected("hidraw node gone after handshake")
        self.hidraw = hidraw
        self.fd = os.open(hidraw, os.O_RDWR)
        self.on_press = on_press
        self.alive = True
        self.inited = threading.Event()
        self.ready = threading.Event()
        self.done = threading.Event()
        self.down = [False] * NUM_KEYS
        threading.Thread(target=self._reader, daemon=True).start()

    def close(self) -> None:
        self.alive = False
        try:
            os.close(self.fd)
        except OSError:
            pass
        try:
            usb.util.release_interface(self.usb, PIXEL_IFACE)
            usb.util.dispose_resources(self.usb)
        except Exception:  # noqa: BLE001 - device may already be gone
            pass

    def _reader(self) -> None:
        while self.alive:
            try:
                data = os.read(self.fd, 64)
            except OSError as exc:
                log(f"hidraw read failed: {exc}")
                self.alive = False
                return
            if not data:
                continue
            kind = data[0]
            if kind == 0x01 and len(data) > 47:
                for i, (byte, mask) in enumerate(KEY_BITS):
                    pressed = bool(data[byte] & mask)
                    if pressed and not self.down[i]:
                        self.on_press(i + 1)
                    self.down[i] = pressed
            elif kind == 0x11:
                self.inited.set()
            elif kind == 0x21 and len(data) > 2 and data[1] == 0x00:
                if data[2] == 0x00:
                    self.ready.set()
                elif data[2] == 0xFF:
                    self.done.set()
            else:
                log(f"report {data[:12].hex()}")

    def _write_cmd(self, msg: bytes) -> bool:
        """False if the pad NAKed the report (ETIMEDOUT); raises if it is gone."""
        try:
            os.write(self.fd, msg)
            return True
        except TimeoutError:
            return False
        except OSError as exc:
            self.alive = False
            raise Disconnected(f"hidraw write failed: {exc}") from exc

    def init(self) -> None:
        for _ in range(20):
            if not self.alive:
                raise Disconnected("reader stopped")
            self.inited.clear()
            self._write_cmd(INIT_MSG)
            if self.inited.wait(0.5):
                time.sleep(0.25)  # firmware settling before the first image
                return
        raise NoInit("no init ack")

    def command(self, *payload: int) -> None:
        self._write_cmd(bytes((0x00, *payload)).ljust(REPORT, b"\0"))

    def set_brightness(self, percent: int) -> None:
        self.command(0x12, 0x03, 0x00, 0x00, max(0, min(100, percent)))

    def query_sleep_time(self) -> None:
        """Ask for the TFT sleep timer; the reply is logged by the reader.

        `22 00` answers `22 00 00 00 00` (no timer). `22 00 00 10` from
        MountainDisplayPadPy is rejected with `ff aa` (unknown command).
        """
        self.command(0x22, 0x00)

    def draw(self, key: int, bgr: bytes) -> None:
        """Show `bgr` (102x102x3) on key index 0..11."""
        payload = bytes(HEADER_SIZE) + bgr.ljust(PIXEL_SIZE, b"\0")
        msg = bytearray(IMG_MSG.ljust(REPORT, b"\0"))
        msg[5] = key
        for _ in range(3):
            if not self.alive:
                raise Disconnected("reader stopped")
            self.ready.clear()
            self.done.clear()
            self._write_cmd(bytes(msg))
            if self.ready.wait(ACK_TIMEOUT):
                try:
                    for i in range(0, len(payload), CHUNK):
                        self.usb.write(PIXEL_EP, payload[i:i + CHUNK], timeout=1000)
                except usb.core.USBError as exc:
                    if exc.errno == 19:  # ENODEV
                        raise Disconnected(str(exc)) from exc
                    log(f"key {key}: pixel write failed: {exc}")
                if self.done.wait(ACK_TIMEOUT):
                    return
            log(f"key {key}: no ack, re-initialising")
            self.init()
        log(f"key {key}: giving up")


# --- Images ---------------------------------------------------------------

def to_bgr(img: Image.Image, rotate: int) -> bytes:
    img = img.convert("RGB").resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
    if rotate:
        img = img.rotate(rotate)
    r, g, b = img.split()
    return Image.merge("RGB", (b, g, r)).tobytes()


def key_images(cfg: dict, icons: str) -> list[bytes]:
    blank = to_bgr(Image.new("RGB", (ICON_SIZE, ICON_SIZE)), 0)
    images = [blank] * NUM_KEYS
    for key, spec in cfg["keys"].items():
        path = os.path.join(icons, spec["icon"] + ".png")
        try:
            images[int(key) - 1] = to_bgr(Image.open(path), int(cfg.get("rotate", 0)))
        except (OSError, ValueError, IndexError) as exc:
            log(f"key {key}: icon {path}: {exc}")
    return images


# --- Main -----------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--icons", required=True)
    args = ap.parse_args()
    with open(args.config) as f:
        cfg = json.load(f)
    images = key_images(cfg, args.icons)
    busy = threading.Lock()

    def on_press(key: int) -> None:
        spec = cfg["keys"].get(str(key))
        log(f"key {key} pressed" + (f": {spec['app']}" if spec else ""))
        if not spec or not busy.acquire(blocking=False):
            return

        def run() -> None:
            try:
                launch(cfg["launch"], spec["app"])
            finally:
                busy.release()
        threading.Thread(target=run, daemon=True).start()

    waiting = False
    misses = 0
    while True:
        if not find_hidraw():
            if not waiting:
                log(f"waiting for DisplayPad {VID:04x}:{PID:04x}")
                waiting = True
            time.sleep(2)
            continue
        waiting = False
        # Give the Supervisor time to grant cgroup access to the new nodes.
        time.sleep(2)
        pad = None
        try:
            ensure_usb_node()
            try:
                pad = Pad(on_press)
            except (Disconnected, usb.core.USBError, OSError):
                # The hidraw node is back but libusb cannot reach the pad.
                # A container restart gives it fresh USB nodes.
                misses += 1
                if misses >= 5:
                    log("pad present but not reachable over libusb; restarting the app")
                    restart_self()
                raise
            misses = 0
            log(f"DisplayPad found at {pad.hidraw}")
            pad.init()
            pad.query_sleep_time()
            brightness = int(cfg.get("brightness", 100))
            refresh_s = float(cfg.get("refresh_s", 60))

            def paint() -> None:
                pad.set_brightness(brightness)
                for key, img in enumerate(images):
                    pad.draw(key, img)

            paint()
            log("keys drawn")
            # The pad blanks its keys after a while (~45 min observed) even
            # though it reports no TFT sleep timer (22 00 -> 22 00 00 00 00),
            # apparently by leaving host mode. Re-entering it (INIT) and
            # repainting periodically keeps the keys showing.
            painted = time.monotonic()
            while pad.alive and find_hidraw() == pad.hidraw:
                time.sleep(1)
                if refresh_s > 0 and time.monotonic() - painted >= refresh_s:
                    pad.init()
                    paint()
                    painted = time.monotonic()
            log("DisplayPad disconnected")
        except NoInit as exc:
            log(f"DisplayPad error: {exc}")
            pad.close()
            pad = None
            reset_usb()
        except (Disconnected, usb.core.USBError, OSError) as exc:
            log(f"DisplayPad error: {exc}")
        finally:
            if pad:
                pad.close()
        time.sleep(2)


if __name__ == "__main__":
    main()
