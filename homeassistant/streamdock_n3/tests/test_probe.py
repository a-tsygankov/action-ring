"""Probe tool: request parsing, the language-option payload and the step runner."""

import asyncio
import json
import plistlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from probe import ProbeRunner, language_option_payload, parse_request

ALL_KINDS = [
    {"kind": "snapshot", "entities": ["media_player.atv"]},
    {"kind": "mrp", "command": "ChangePlaybackRate", "options": {"playbackRate": 24.0}},
    {"kind": "mrp", "command": "EnableLanguageOption",
     "options": {"languageOption": {"type": 1, "languageTag": "en"}}},
    {"kind": "companion", "mcc": "SetCaptionSettings", "args": {"_mcs": 4}},
    {"kind": "companion", "hid": "right"},
    {"kind": "ha", "service": "remote.send_command",
     "data": {"entity_id": "remote.atv", "command": "right", "hold_secs": 1}},
    {"kind": "lounge", "method": "set_closed_captions", "args": ["en", "$video_id"]},
    {"kind": "webos", "entity_id": "media_player.lg", "command": "com.webos.media/getForegroundAppInfo"},
    {"kind": "wait", "s": 2},
]


def test_parse_request_accepts_all_kinds():
    steps = parse_request(json.dumps(ALL_KINDS))
    assert [s["kind"] for s in steps] == [s["kind"] for s in ALL_KINDS]


def test_parse_request_accepts_a_run_wrapper():
    # probe.sh wraps the file as {"run": <id>, "steps": [...]} to find its log lines
    steps = parse_request(json.dumps({"run": "x1", "steps": [{"kind": "wait", "s": 1}]}))
    assert steps == [{"kind": "wait", "s": 1}]


def test_parse_request_rejects_unknown_kind():
    with pytest.raises(ValueError, match="step 2"):
        parse_request(json.dumps([{"kind": "wait", "s": 1}, {"kind": "reboot"}]))


@pytest.mark.parametrize("step", [
    {"kind": "mrp"},                                       # no command
    {"kind": "companion", "mcc": "Play", "hid": "right"},  # both
    {"kind": "ha", "service": "turn_on"},                  # no domain
    {"kind": "lounge", "method": "set_volume", "args": [0]},
    {"kind": "webos", "command": "system/getSystemInfo"},  # no entity_id
    {"kind": "wait", "s": 600},
])
def test_parse_request_rejects_malformed_steps(step):
    with pytest.raises(ValueError, match="step 1"):
        parse_request(json.dumps([step]))


@pytest.mark.parametrize("step", [
    {"kind": "ha", "service": "media_player.turn_off", "data": {}},
    {"kind": "ha", "service": "remote.toggle", "data": {}},
    {"kind": "companion", "hid": "Sleep"},
    {"kind": "webos", "entity_id": "media_player.lg", "command": "system/turnOff"},
])
def test_parse_request_refuses_power_off(step):
    with pytest.raises(ValueError, match="power"):
        parse_request(json.dumps([step]))


def _archived(payload: bytes, key: str = "protobuf") -> tuple[dict, dict, bytes, dict]:
    data = plistlib.loads(payload)
    objects = data["$objects"]
    root = objects[data["$top"]["root"]]
    return data, root, objects[root[key]], objects[root["$class"]]


def test_language_option_payload_is_a_keyed_archive():
    data, root, blob, cls = _archived(language_option_payload(1, "en", None))
    assert data["$archiver"] == "NSKeyedArchiver"
    assert cls["$classname"] == "MRLanguageOption"
    assert isinstance(blob, bytes)


def test_language_option_payload_roundtrip():
    protobuf = pytest.importorskip("pyatv.protocols.mrp.protobuf")
    data, _, blob, _ = _archived(language_option_payload(1, "en", "English-en-English"))
    assert data["$archiver"] == "NSKeyedArchiver"
    option = protobuf.LanguageOption.FromString(blob)
    assert (option.type, option.languageTag, option.identifier) == (1, "en", "English-en-English")
    # subtitles off: only the type
    _, _, blob, _ = _archived(language_option_payload(1, None, None))
    option = protobuf.LanguageOption.FromString(blob)
    assert option.type == 1 and not option.HasField("languageTag") and not option.HasField("identifier")


def test_language_option_payload_variant_key():
    _, root, blob, _ = _archived(language_option_payload(0, "en", None, key="data"), key="data")
    assert "protobuf" not in root and isinstance(blob, bytes)


class FakeCall:
    def __init__(self, fail_on: str):
        self.fail_on = fail_on
        self.calls = []

    def __call__(self, domain, service, data, response=False):
        self.calls.append((f"{domain}.{service}", data, response))
        if service == self.fail_on:
            raise RuntimeError("HTTP 400")
        return {"service_response": {"media_player.lg": {"appId": "netflix"}}} if response else None


def _probe_lines(capsys) -> list[str]:
    return [line for line in capsys.readouterr().out.splitlines() if line.startswith("probe:")]


def test_runner_continues_after_error(capsys):
    call = FakeCall(fail_on="media_previous_track")
    runner = ProbeRunner(None, call, None, sleep=lambda s: None)
    runner.run_request(json.dumps({"run": "r7", "steps": [
        {"kind": "ha", "service": "media_player.media_next_track", "data": {"entity_id": "media_player.atv"}},
        {"kind": "ha", "service": "media_player.media_previous_track", "data": {"entity_id": "media_player.atv"}},
        {"kind": "webos", "entity_id": "media_player.lg", "command": "com.webos.media/getForegroundAppInfo"},
    ]}))
    lines = _probe_lines(capsys)
    assert lines[0] == "probe: start r7 (3 steps)"
    assert lines[1] == "probe: 1 ha media_player.media_next_track -> ok"
    assert lines[2] == "probe: 2 ha media_player.media_previous_track -> error RuntimeError: HTTP 400"
    assert lines[3].startswith("probe: 3 webos com.webos.media/getForegroundAppInfo -> ok ")
    assert '"appId": "netflix"' in lines[3]
    assert lines[4] == "probe: done r7"
    # webos goes through webostv.command with the response requested
    assert call.calls[2] == (
        "webostv.command",
        {"entity_id": "media_player.lg", "command": "com.webos.media/getForegroundAppInfo"},
        True,
    )


def test_runner_reports_atv_steps_without_a_connection(capsys):
    runner = ProbeRunner(None, FakeCall(fail_on=""), None, sleep=lambda s: None)
    runner.run_request(json.dumps([{"kind": "companion", "hid": "right"}, {"kind": "wait", "s": 1}]))
    lines = _probe_lines(capsys)
    assert lines[1] == "probe: 1 companion hid right -> error RuntimeError: no Apple TV connection"
    assert lines[2] == "probe: 2 wait 1s -> ok"


def test_runner_reports_a_bad_request(capsys):
    ProbeRunner(None, FakeCall(fail_on=""), None).run_request('[{"kind": "nope"}]')
    lines = _probe_lines(capsys)
    assert lines[0].startswith("probe: start")
    assert lines[1].startswith("probe: bad request: ValueError: step 1")
    assert lines[2].startswith("probe: done")


def test_snapshot_without_devices(capsys):
    states = {"media_player.atv": {"s": "playing", "a": {"app_id": "tv.pluto.ios", "media_title": "X"}}}
    runner = ProbeRunner(None, FakeCall(fail_on=""), None, state_of=states.get)
    runner.run_request(json.dumps([{"kind": "snapshot", "entities": ["media_player.atv"]}]))
    line = _probe_lines(capsys)[1]
    assert line.startswith("probe: 1 snapshot -> ok ")
    snap = json.loads(line.split(" -> ok ", 1)[1])
    assert snap["ha"]["media_player.atv"] == {"state": "playing", "app_id": "tv.pluto.ios", "media_title": "X"}
    assert snap["atv"] == "no Apple TV connection"
    assert snap["lounge"] == "no YouTube link"


def test_poll_once_consumes_the_request(tmp_path, capsys):
    request = tmp_path / "probe_request.json"
    request.write_text('[{"kind": "wait", "s": 0}]')
    runner = ProbeRunner(None, FakeCall(fail_on=""), None, request=request, sleep=lambda s: None)
    assert runner.poll_once() is True
    assert not request.exists()
    assert _probe_lines(capsys)[-1].startswith("probe: done")
    assert runner.poll_once() is False


class FakeTouch:
    """AppleTVTouch stand-in: run() awaits fn(atv) on a fresh loop."""

    def __init__(self, atv):
        self.atv = atv

    def available(self):
        return True

    def run(self, fn, timeout=10):
        return asyncio.run(fn(self.atv))


def _fake_atv(protobuf, sent):
    from pyatv.const import Protocol

    async def send_command(cmd, **options):
        sent.append(("mrp", cmd, options))

    async def mcc(command, args=None):
        sent.append(("mcc", command.name, args))
        return {"_mcs": 3}

    async def hid(down, command):
        sent.append(("hid", command.name, down))

    rate = protobuf.CommandInfo(command=protobuf.CommandInfo_pb2.ChangePlaybackRate, supportedRates=[-8, 8, 24])
    play = protobuf.CommandInfo(command=protobuf.CommandInfo_pb2.Play)
    item = protobuf.ContentItem()
    group = item.availableLanguageOptions.add()
    group.languageOptions.add(type=1, languageTag="en", identifier="English-en-English")
    item.currentLanguageOptions.add(type=0, languageTag="en", identifier="English [Original]-en-English")
    player = SimpleNamespace(identifier="avkit-1", display_name=None, supported_commands=[rate],
                             items=[item], location=0)
    client = SimpleNamespace(bundle_identifier="tv.pluto.ios", supported_commands=[play])
    mrp = SimpleNamespace(_send_command=send_command, psm=SimpleNamespace(client=client, playing=player))
    comp = SimpleNamespace(api=SimpleNamespace(mediacontrol_command=mcc, hid_command=hid))
    remotes = {Protocol.MRP: mrp, Protocol.Companion: comp}
    return SimpleNamespace(
        remote_control=SimpleNamespace(get=remotes.get),
        metadata=SimpleNamespace(app=SimpleNamespace(name="Pluto TV", identifier="tv.pluto.ios")),
    )


def test_runner_atv_steps(capsys):
    protobuf = pytest.importorskip("pyatv.protocols.mrp.protobuf")
    sent = []
    runner = ProbeRunner(FakeTouch(_fake_atv(protobuf, sent)), FakeCall(fail_on=""), None)
    runner.run_request(json.dumps([
        {"kind": "mrp", "command": "ChangePlaybackRate", "options": {"playbackRate": 24.0}},
        {"kind": "mrp", "command": "EnableLanguageOption",
         "options": {"languageOption": {"type": 1, "languageTag": "en"}}},
        {"kind": "companion", "mcc": "GetCaptionSettings"},
        {"kind": "companion", "hid": "right"},
        {"kind": "snapshot"},
    ]))
    lines = _probe_lines(capsys)
    assert sent[0] == ("mrp", protobuf.CommandInfo_pb2.ChangePlaybackRate, {"playbackRate": 24.0})
    assert sent[1][1] == protobuf.CommandInfo_pb2.EnableLanguageOption
    assert plistlib.loads(sent[1][2]["languageOption"])["$archiver"] == "NSKeyedArchiver"
    assert sent[2:] == [("mcc", "GetCaptionSettings", None), ("hid", "Right", True), ("hid", "Right", False)]
    assert lines[3] == 'probe: 3 companion mcc GetCaptionSettings -> ok {"_mcs": 3}'
    snap = json.loads(lines[5].split(" -> ok ", 1)[1])["atv"]
    assert snap["app"] == "Pluto TV (tv.pluto.ios)"
    assert snap["client"] == "tv.pluto.ios"
    assert snap["commands"] == ["ChangePlaybackRate(rates=-8/8/24)"]
    assert snap["client_commands"] == ["Play"]
    assert snap["languages"] == [{"type": 1, "options": ["1:en:English-en-English"]}]
    assert snap["languages_current"] == ["0:en:English [Original]-en-English"]


def test_runner_rejects_unknown_mrp_command(capsys):
    protobuf = pytest.importorskip("pyatv.protocols.mrp.protobuf")
    runner = ProbeRunner(FakeTouch(_fake_atv(protobuf, [])), FakeCall(fail_on=""), None)
    runner.run_request(json.dumps([{"kind": "mrp", "command": "Warp"}]))
    assert _probe_lines(capsys)[1].startswith("probe: 1 mrp Warp -> error ValueError:")


def test_touch_run_awaits_on_the_pyatv_loop():
    pytest.importorskip("pyatv")
    from atv_touch import AppleTVTouch

    touch = AppleTVTouch()  # no credentials here: stays disconnected
    with pytest.raises(RuntimeError, match="not connected"):
        touch.run(lambda atv: asyncio.sleep(0))
    touch._atv = "atv"

    async def echo(atv):
        return (atv, asyncio.get_running_loop() is touch._loop)

    assert touch.run(echo) == ("atv", True)
    with pytest.raises(TimeoutError):
        touch.run(lambda atv: asyncio.sleep(5), timeout=0.05)


PROBES = Path(__file__).resolve().parents[2] / "tools" / "probes"


def test_probe_files_are_valid_and_fit_the_timeout():
    files = sorted(PROBES.glob("*.json"))
    assert len(files) == 6  # spec §10 items 1-6
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["note"], path.name
        steps = parse_request(path.read_text(encoding="utf-8"))
        waits = sum(s["s"] for s in steps if s["kind"] == "wait")
        # probe.sh waits 120 s for "probe: done"; leave room for the calls
        assert waits + 2 * len(steps) <= 120, path.name
