"""Which Apple TV apps are alive, from the MRP client messages."""

import asyncio

import atv_clients
from atv_clients import AtvClients

HULU, PRIME = "com.hulu.plus", "com.amazon.aiv.AIVApp"


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


def make(**kw):
    clock = Clock()
    lines = []
    return AtvClients(monotonic=clock, log=lines.append, **kw), clock, lines


def test_unknown_without_a_session():
    t, _, _ = make()
    assert t.status(HULU) == "unknown"
    assert t.session_at() is None and t.pid(HULU) is None
    assert t.removed_at(HULU) is None and t.seen_at(HULU) is None


def test_unknown_until_settled_after_the_first_message():
    t, clock, _ = make()
    t.on_session()
    assert t.session_at() == 100.0
    clock.now = 160.0                    # a silent session never settles
    assert t.status(HULU) == "unknown"
    t.on_message()                       # the first message starts the settle clock
    clock.now = 162.9
    assert t.status(HULU) == "unknown"
    clock.now = 163.0
    assert t.status(HULU) == "gone"


def test_a_listed_app_is_alive_at_once():
    t, clock, _ = make()
    t.on_session()
    t.on_update(PRIME, 8061)
    assert t.status(PRIME) == "alive" and t.pid(PRIME) == 8061 and t.seen_at(PRIME) == 100.0
    assert t.status(HULU) == "unknown"   # the list may not be complete yet
    clock.now += 3
    assert t.status(HULU) == "gone"


def test_removal_makes_it_gone_and_is_remembered():
    t, clock, _ = make()
    t.on_session()
    t.on_update(PRIME, 8061)
    clock.now = 110.0
    t.on_remove(PRIME, 8061)
    assert t.status(PRIME) == "gone" and t.pid(PRIME) is None
    assert t.removed_at(PRIME) == 110.0


def test_a_stale_removal_for_an_old_pid_is_ignored():
    t, clock, _ = make()
    t.on_session()
    t.on_update(PRIME, 8061)
    t.on_update(PRIME, 8200)             # restarted
    t.on_remove(PRIME, 8061)             # the old process, reported late
    assert t.status(PRIME) == "alive" and t.pid(PRIME) == 8200
    assert t.removed_at(PRIME) is None


def test_removal_of_an_unlisted_app_is_recorded():
    t, clock, _ = make()
    t.on_session()
    t.on_remove(HULU, 8155)
    assert t.removed_at(HULU) == 100.0


def test_new_session_empties_the_list_but_keeps_times():
    t, clock, _ = make()
    t.on_session()
    t.on_update(PRIME, 8061)
    t.on_update(HULU, 8155)
    t.on_remove(HULU, 8155)
    clock.now = 200.0
    t.on_session()
    assert t.session_at() == 200.0
    assert t.status(PRIME) == "unknown"  # not listed again yet, not settled
    assert t.removed_at(HULU) == 100.0 and t.seen_at(PRIME) == 100.0
    t.on_update(PRIME, 8061)
    clock.now = 203.0
    assert t.status(PRIME) == "alive" and t.status(HULU) == "gone"


def test_lost_connection_is_unknown_until_the_next_session():
    t, clock, _ = make()
    t.on_session()
    t.on_update(PRIME, 8061)
    clock.now += 5
    t.on_lost()
    assert t.status(PRIME) == "unknown" and t.status(HULU) == "unknown"
    assert t.session_at() is None
    t.on_update(PRIME, 8061)             # a stray message without a session
    assert t.status(PRIME) == "unknown"
    t.on_session()
    t.on_update(PRIME, 8061)
    assert t.status(PRIME) == "alive"


def test_watched_apps_log_changes_once():
    t, _, lines = make(watch=[PRIME])
    t.on_session()
    t.on_update(PRIME, 8061)
    t.on_update(PRIME, 8061)             # the list repeated: no new line
    t.on_update(HULU, 8155)              # not watched
    t.on_remove(PRIME, 8061)
    assert lines == [f"atv-clients: {PRIME} alive pid 8061", f"atv-clients: {PRIME} gone"]


def test_install_feeds_the_tracker_from_mrp_messages(monkeypatch):
    from pyatv.protocols.mrp import messages, protobuf
    from pyatv.protocols.mrp import protocol as mrp_protocol

    class FakeProtocol:
        def __init__(self):
            self.handlers = {}

        def listen_to(self, message_type, handler):
            self.handlers[message_type] = handler

    monkeypatch.setattr(mrp_protocol, "MrpProtocol", FakeProtocol)
    t, clock, _ = make()
    atv_clients.install(t)
    proto = mrp_protocol.MrpProtocol()
    assert t.session_at() == 100.0

    def send(kind, bundle=None, pid=0):
        msg = messages.create(kind)
        if bundle is not None:
            inner = protobuf.extract_inner(msg)
            inner.client.bundleIdentifier = bundle
            inner.client.processIdentifier = pid
        asyncio.run(proto.handlers[kind](msg))

    send(protobuf.UPDATE_CLIENT_MESSAGE, PRIME, 8061)
    send(protobuf.SET_NOW_PLAYING_CLIENT_MESSAGE, HULU, 8155)
    send(protobuf.SET_NOW_PLAYING_CLIENT_MESSAGE)        # "nothing is playing": no client
    assert t.status(PRIME) == "alive" and t.status(HULU) == "alive"
    send(protobuf.REMOVE_CLIENT_MESSAGE, PRIME, 8061)
    clock.now += 3
    assert t.status(PRIME) == "gone" and t.removed_at(PRIME) == 100.0
    assert protobuf.SET_STATE_MESSAGE in proto.handlers


def test_install_survives_a_handler_error():
    from pyatv.protocols.mrp import messages, protobuf

    class Broken(AtvClients):
        def on_update(self, bundle, pid):
            raise RuntimeError("boom")

    lines = []
    handlers = atv_clients._handlers(Broken(log=lines.append))
    msg = messages.create(protobuf.UPDATE_CLIENT_MESSAGE)
    protobuf.extract_inner(msg).client.bundleIdentifier = PRIME
    asyncio.run(handlers[protobuf.UPDATE_CLIENT_MESSAGE](msg))   # must not raise into pyatv
    assert lines and "RuntimeError" in lines[0]


def test_a_message_without_a_pid_keeps_the_known_pid():
    t, clock, _ = make()
    t.on_session()
    t.on_update(PRIME, 8061)
    t.on_update(PRIME, 0)                # now_playing_client may carry no process id
    assert t.pid(PRIME) == 8061
    t.on_remove(PRIME, 8061)
    assert t.status(PRIME) != "alive" and t.removed_at(PRIME) == 100.0
