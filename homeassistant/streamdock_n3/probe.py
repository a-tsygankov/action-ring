"""Probe tool: run a list of candidate commands and log what each returned.

For the live test session (design spec §10): which controls work in which
app. The PC writes a request to probe_request.json in the app's config share
(homeassistant/tools/probe.sh does this) and reads the "probe:" lines from the
app log; the user watches the TV and says what it did.

A request is a JSON list of steps, or {"run": <id>, "steps": [...]}:

- {"kind": "mrp", "command": "<CommandInfo name>", "options": {...}}: a raw
  MRP command (needs the AirPlay pairing). options are CommandOptions fields
  ("playbackRate", "skipInterval", ...); "languageOption" takes
  {"type", "languageTag", "identifier", "archive_key"} and is sent as the
  archived bytes (language_option_payload).
- {"kind": "companion", "mcc": "<MediaControlCommand name>", "args": {...}}
  or {"hid": "<HidCommand name>", "hold_s": 0}: Companion protocol.
- {"kind": "ha", "service": "domain.service", "data": {...}, "response": false}
- {"kind": "lounge", "method": "next", "args": [...]}: the linked YouTube
  app; "$video_id" in args is the playing video.
- {"kind": "webos", "entity_id": "media_player.…", "command": "<ssap path>",
  "payload": {...}}: HA webostv.command, logging the TV's response.
- {"kind": "wait", "s": 2}
- {"kind": "snapshot", "entities": [...]}: the Apple TV player's advertised
  commands and language options, the app, those entities' HA states and the
  YouTube Lounge state.

Every step is logged as "probe: <n> <kind> <summary> -> ok <result>" or
"-> error <Type>: <msg>"; a failing step doesn't stop the rest. The run ends
with "probe: done <run>". Power-off commands are refused when parsing.

Parsing and the payload builder don't need pyatv (tests run without it).
"""

from __future__ import annotations

import asyncio
import json
import plistlib
import threading
import time
from pathlib import Path
from typing import Any, Callable

REQUEST_FILE = Path("/config/probe_request.json")
POLL_SECONDS = 1
MAX_WAIT_S = 60
MAX_HOLD_S = 5
ATV_TIMEOUT = 10
RESULT_CHARS = 2000
KINDS = ("mrp", "companion", "ha", "lounge", "webos", "wait", "snapshot")
LOUNGE_METHODS = (
    "next", "previous", "play", "pause", "seek_to", "set_closed_captions",
    "set_playback_speed", "skip_ad", "get_now_playing",
)
HA_ATTRS = ("app_id", "app_name", "source", "media_title", "media_content_type", "media_position")
# Power-off is never a probe: the TV session would end.
POWER_SERVICES = ("turn_off", "toggle")
POWER_HID = ("sleep",)
POWER_WEBOS = ("turnoff", "power", "reboot")

Call = Callable[..., Any]  # call(domain, service, data, response=False)


def log(msg: str) -> None:
    print(f"probe: {msg}", flush=True)


# -- request parsing -------------------------------------------------------------


def _check(ok: bool, n: int, msg: str) -> None:
    if not ok:
        raise ValueError(f"step {n}: {msg}")


def _number(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def _check_step(n: int, step: Any) -> None:
    _check(isinstance(step, dict), n, "not an object")
    kind = step.get("kind")
    _check(kind in KINDS, n, f"unknown kind {kind!r}")
    if kind == "mrp":
        _check(isinstance(step.get("command"), str) and step["command"], n, "mrp needs a command name")
        options = step.get("options", {})
        _check(isinstance(options, dict), n, "options must be an object")
        lang = options.get("languageOption")
        if lang is not None:
            _check(isinstance(lang, dict) and isinstance(lang.get("type"), int), n,
                   "languageOption needs {\"type\": 0|1, ...}")
    elif kind == "companion":
        has_mcc, has_hid = isinstance(step.get("mcc"), str), isinstance(step.get("hid"), str)
        _check(has_mcc != has_hid, n, "companion needs exactly one of mcc / hid")
        _check(isinstance(step.get("args", {}), dict), n, "args must be an object")
        _check(_number(step.get("hold_s", 0), 0, MAX_HOLD_S), n, f"hold_s must be 0..{MAX_HOLD_S}")
        _check(not has_hid or step["hid"].lower() not in POWER_HID, n, "refusing a power-off command")
    elif kind == "ha":
        service = step.get("service")
        _check(isinstance(service, str) and service.count(".") == 1, n, "ha needs service \"domain.service\"")
        _check(service.split(".")[1] not in POWER_SERVICES, n, "refusing a power-off command")
        _check(isinstance(step.get("data", {}), dict), n, "data must be an object")
    elif kind == "lounge":
        _check(step.get("method") in LOUNGE_METHODS, n, f"lounge method must be one of {', '.join(LOUNGE_METHODS)}")
        _check(isinstance(step.get("args", []), list), n, "args must be a list")
    elif kind == "webos":
        _check(isinstance(step.get("entity_id"), str), n, "webos needs entity_id")
        command = step.get("command")
        _check(isinstance(command, str) and command, n, "webos needs a command")
        _check(not any(w in command.lower() for w in POWER_WEBOS), n, "refusing a power-off command")
        _check(isinstance(step.get("payload", {}), dict), n, "payload must be an object")
    elif kind == "wait":
        _check(_number(step.get("s"), 0, MAX_WAIT_S), n, f"wait needs s in 0..{MAX_WAIT_S}")
    else:  # snapshot
        entities = step.get("entities", [])
        _check(isinstance(entities, list) and all(isinstance(e, str) for e in entities), n,
               "entities must be a list of ids")


def _steps_of(data: Any) -> list[dict]:
    steps = data.get("steps") if isinstance(data, dict) else data
    if not isinstance(steps, list):
        raise ValueError("a request is a list of steps or {\"steps\": [...]}")
    for n, step in enumerate(steps, 1):
        _check_step(n, step)
    return steps


def parse_request(text: str) -> list[dict]:
    """The validated steps of a probe request; ValueError names the bad step."""
    return _steps_of(json.loads(text))


# -- MRP language option payload ---------------------------------------------------


def _varint(value: int) -> bytes:
    value &= (1 << 64) - 1  # negative int32 is sent as ten bytes
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def _language_option_proto(type: int, tag: str | None, identifier: str | None) -> bytes:
    """A serialized LanguageOption protobuf: type = 1, languageTag = 2, identifier = 5."""
    out = b"\x08" + _varint(type)
    for field, text in ((2, tag), (5, identifier)):
        if text is not None:
            raw = text.encode()
            out += _varint(field << 3 | 2) + _varint(len(raw)) + raw
    return out


def language_option_payload(
    type: int,
    tag: str | None,
    identifier: str | None,
    *,
    key: str = "protobuf",
    classname: str = "MRLanguageOption",
) -> bytes:
    """CommandOptions.languageOption for Enable/DisableLanguageOption.

    Per pyatv #720 the bytes are an NSKeyedArchiver binary plist of an object
    whose single property is the serialized LanguageOption protobuf. type is
    0 = audio, 1 = subtitle; subtitles off is DisableLanguageOption with only
    the type. The property's key and the class name are not documented, so
    they can be varied from a probe step ("archive_key").
    """
    archive = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            {"$class": plistlib.UID(3), key: plistlib.UID(2)},
            _language_option_proto(type, tag, identifier),
            {"$classname": classname, "$classes": [classname, "NSObject"]},
        ],
    }
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


# -- running ---------------------------------------------------------------------


def _summary(step: dict) -> str:
    kind = step["kind"]
    if kind == "mrp":
        options = step.get("options") or {}
        return f"{step['command']} {json.dumps(options)}" if options else step["command"]
    if kind == "companion":
        if "mcc" in step:
            args = step.get("args") or {}
            return f"mcc {step['mcc']} {json.dumps(args)}" if args else f"mcc {step['mcc']}"
        hold = step.get("hold_s", 0)
        return f"hid {step['hid']} hold {hold}s" if hold else f"hid {step['hid']}"
    if kind == "ha":
        data = {k: v for k, v in (step.get("data") or {}).items() if k != "entity_id"}
        return f"{step['service']} {json.dumps(data)}" if data else step["service"]
    if kind == "lounge":
        args = step.get("args") or []
        return f"{step['method']} {json.dumps(args)}" if args else step["method"]
    if kind == "webos":
        payload = step.get("payload") or {}
        return f"{step['command']} {json.dumps(payload)}" if payload else step["command"]
    if kind == "wait":
        return f"{step['s']}s"
    return ""


def _result_text(result: Any) -> str:
    if result is None:
        return ""
    text = result if isinstance(result, str) else json.dumps(result, default=str)
    return text if len(text) <= RESULT_CHARS else text[:RESULT_CHARS] + "…"


def _enum_member(enum: Any, name: str) -> Any:
    """Enum member by name, case-insensitively ("right" -> HidCommand.Right)."""
    for member in enum:
        if member.name.lower() == name.lower():
            return member
    raise ValueError(f"no {enum.__name__} named {name!r}")


def _command_text(info: Any) -> str:
    """A CommandInfo as "Name" plus the details that matter for the tests."""
    from pyatv.protocols.mrp.protobuf import CommandInfo_pb2

    try:
        name = CommandInfo_pb2.Command.Name(info.command)
    except ValueError:
        name = str(info.command)
    extra = []
    if info.HasField("enabled") and not info.enabled:
        extra.append("disabled")
    if list(info.supportedRates):
        extra.append("rates=" + "/".join(f"{r:g}" for r in info.supportedRates))
    if list(info.preferredIntervals):
        extra.append("intervals=" + "/".join(f"{i:g}" for i in info.preferredIntervals))
    if info.HasField("canScrub"):
        extra.append(f"canScrub={info.canScrub}")
    return f"{name}({', '.join(extra)})" if extra else name


def _options_text(options: Any) -> list[str]:
    return [f"{o.type}:{o.languageTag}:{o.identifier}" for o in options]


async def _atv_snapshot(atv: Any) -> dict[str, Any]:
    from pyatv.const import Protocol

    out: dict[str, Any] = {}
    try:
        app = atv.metadata.app
        out["app"] = f"{app.name} ({app.identifier})" if app else None
    except Exception as err:  # "not supported" while nothing plays
        out["app"] = f"{type(err).__name__}: {err}"
    mrp = atv.remote_control.get(Protocol.MRP)
    if mrp is None:
        out["mrp"] = "no MRP session (pair.sh atv airplay)"
        return out
    psm = mrp.psm
    client = psm.client
    player = psm.playing
    out["client"] = client.bundle_identifier if client else None
    out["player"] = f"{player.identifier} {player.display_name or ''}".strip()
    out["commands"] = [_command_text(c) for c in player.supported_commands]
    if client is not None:
        out["client_commands"] = [_command_text(c) for c in client.supported_commands]
    items = list(player.items)
    if 0 <= player.location < len(items):
        item = items[player.location]
        out["languages"] = [
            {"type": group.languageOptions[0].type if list(group.languageOptions) else None,
             "options": _options_text(group.languageOptions)}
            for group in item.availableLanguageOptions
        ]
        out["languages_current"] = _options_text(item.currentLanguageOptions)
    return out


async def _mrp(atv: Any, step: dict) -> Any:
    from pyatv.const import Protocol
    from pyatv.protocols.mrp.protobuf import CommandInfo_pb2

    mrp = atv.remote_control.get(Protocol.MRP)
    if mrp is None:
        raise RuntimeError("no MRP session (pair.sh atv airplay)")
    options = dict(step.get("options") or {})
    lang = options.pop("languageOption", None)
    if lang is not None:
        options["languageOption"] = language_option_payload(
            lang["type"], lang.get("languageTag"), lang.get("identifier"),
            key=lang.get("archive_key", "protobuf"),
        )
    await mrp._send_command(CommandInfo_pb2.Command.Value(step["command"]), **options)


async def _companion(atv: Any, step: dict) -> Any:
    from pyatv.const import Protocol
    from pyatv.protocols.companion.api import HidCommand, MediaControlCommand

    comp = atv.remote_control.get(Protocol.Companion)
    if comp is None:
        raise RuntimeError("no Companion session")
    if "mcc" in step:
        return await comp.api.mediacontrol_command(
            _enum_member(MediaControlCommand, step["mcc"]), step.get("args") or None
        )
    hid = _enum_member(HidCommand, step["hid"])
    await comp.api.hid_command(True, hid)
    await asyncio.sleep(float(step.get("hold_s", 0)))
    await comp.api.hid_command(False, hid)


class ProbeRunner:
    """Watches the request file and runs each request's steps in order.

    touch: atv_touch.AppleTVTouch (mrp / companion / snapshot; None = no
    connection); call(domain, service, data, response=False) calls an HA
    service, returning the parsed reply when response is set; youtube:
    yt_lounge.YouTubeScreens or None; state_of: the daemon's HA state lookup.
    """

    def __init__(
        self,
        touch: Any,
        call: Call,
        youtube: Any,
        state_of: Callable[[str], dict[str, Any] | None] | None = None,
        *,
        request: Path = REQUEST_FILE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._touch = touch
        self._call = call
        self._youtube = youtube
        self._state_of = state_of or (lambda entity_id: None)
        self._request = request
        self._sleep = sleep

    def start(self) -> None:
        threading.Thread(target=self._watch, daemon=True, name="probe").start()

    def _watch(self) -> None:
        while True:
            try:
                self.poll_once()
            except Exception as err:  # keep watching
                log(f"{type(err).__name__}: {err}")
            time.sleep(POLL_SECONDS)

    def poll_once(self) -> bool:
        """Run the pending request, if any; the file is consumed first."""
        if not self._request.exists():
            return False
        text = self._request.read_text()
        self._request.unlink(missing_ok=True)
        self.run_request(text)
        return True

    def run_request(self, text: str) -> None:
        run = "manual"
        try:
            data = json.loads(text)
            if isinstance(data, dict) and data.get("run"):
                run = str(data["run"])
            steps = _steps_of(data)
        except Exception as err:
            log(f"start {run}")
            log(f"bad request: {type(err).__name__}: {err}")
            log(f"done {run}")
            return
        log(f"start {run} ({len(steps)} steps)")
        for n, step in enumerate(steps, 1):
            head = f"{n} {step['kind']} {_summary(step)}".rstrip()
            try:
                result = _result_text(self._run_step(step))
                log(f"{head} -> ok {result}".rstrip())
            except Exception as err:
                log(f"{head} -> error {type(err).__name__}: {err}")
        log(f"done {run}")

    # -- steps -------------------------------------------------------------------
    def _atv(self, fn: Callable[[Any], Any]) -> Any:
        if self._touch is None or not self._touch.available():
            raise RuntimeError("no Apple TV connection")
        return self._touch.run(fn, ATV_TIMEOUT)

    def _run_step(self, step: dict) -> Any:
        kind = step["kind"]
        if kind == "mrp":
            return self._atv(lambda atv: _mrp(atv, step))
        if kind == "companion":
            return self._atv(lambda atv: _companion(atv, step))
        if kind == "ha":
            domain, service = step["service"].split(".")
            reply = self._call(domain, service, step.get("data") or {}, response=bool(step.get("response")))
            return reply.get("service_response", reply) if isinstance(reply, dict) else reply
        if kind == "webos":
            data = {"entity_id": step["entity_id"], "command": step["command"]}
            if step.get("payload"):
                data["payload"] = step["payload"]
            reply = self._call("webostv", "command", data, response=True)
            return reply.get("service_response", reply) if isinstance(reply, dict) else reply
        if kind == "lounge":
            if self._youtube is None:
                raise RuntimeError("no YouTube link configured")
            return self._youtube.probe(step["method"], list(step.get("args") or []))
        if kind == "wait":
            self._sleep(float(step["s"]))
            return None
        return self._snapshot(step)

    def _snapshot(self, step: dict) -> dict[str, Any]:
        out: dict[str, Any] = {"ha": {}}
        for entity_id in step.get("entities") or []:
            st = self._state_of(entity_id)
            if st is None:
                out["ha"][entity_id] = None
                continue
            attrs = st.get("a") or {}
            out["ha"][entity_id] = {"state": st.get("s"), **{k: attrs[k] for k in HA_ATTRS if k in attrs}}
        try:
            out["atv"] = self._atv(_atv_snapshot)
        except Exception as err:
            out["atv"] = str(err) if isinstance(err, RuntimeError) else f"{type(err).__name__}: {err}"
        out["lounge"] = self._youtube.snapshot() if self._youtube is not None else "no YouTube link"
        return out
