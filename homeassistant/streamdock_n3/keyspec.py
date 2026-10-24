"""Pure resolution of a key spec into what the key shows and does.

A key spec is a dict from the config (label, color, icon, state_entity,
on/off/unavailable variants, modes with `when`, action). Everything here is a
pure function of the spec, a state lookup and named conditions, so it is
testable without the device, Home Assistant or the YouTube/AppleTV libraries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

# Returns {"s": state, "a": attrs} for an entity id, or None (as ha_daemon.state_of).
StateLookup = Callable[[str], "dict | None"]

ICON_DIR = "/config/icons"
OFF_STATES = {"off", "standby"}
UNKNOWN_STATES = {None, "unavailable", "unknown"}


@dataclass(frozen=True)
class Look:
    icon: str | None
    label: str
    color: str | None


def icon_path(ref: str | None) -> str | None:
    """A bare icon id maps into ICON_DIR; anything with a '/' is already a path."""
    if not ref:
        return None
    return ref if "/" in ref else f"{ICON_DIR}/{ref}.png"


def variant(state: str | None) -> str:
    if state in UNKNOWN_STATES:
        return "unavailable"
    return "off" if state in OFF_STATES else "on"


def _matches(state_of: StateLookup, cond: dict[str, Any]) -> bool:
    # Same semantics as yt_lounge.matches (copied: yt_lounge imports pyytlounge).
    st = state_of(cond["entity"]) or {}
    value = st.get("s") if cond.get("attr", "state") == "state" else (st.get("a") or {}).get(cond["attr"])
    return value in cond.get("in", [])


def _when(cond: Any, state_of: StateLookup, conds: Mapping[str, Callable[[], bool]]) -> bool:
    if isinstance(cond, str):
        fn = conds.get(cond)
        return bool(fn()) if fn else False
    if isinstance(cond, list):
        return any(_matches(state_of, c) for c in cond)
    return False


def active_mode(
    spec: dict, state_of: StateLookup, conds: Mapping[str, Callable[[], bool]]
) -> dict | None:
    """The first mode whose `when` holds."""
    for mode in spec.get("modes") or []:
        if isinstance(mode, dict) and _when(mode.get("when"), state_of, conds):
            return mode
    return None


class _KeepMissing(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _format(label: str, vars: Mapping[str, str]) -> str:
    try:
        return label.format_map(_KeepMissing(vars))
    except (ValueError, IndexError, AttributeError):
        return label  # malformed braces or positional fields: show the raw text


def resolve_look(
    spec: dict,
    state_of: StateLookup,
    conds: Mapping[str, Callable[[], bool]],
    vars: Mapping[str, str] = {},
) -> Look:
    """An active mode overlays the base; else, with state_entity, the state variant does."""
    over: dict = {}
    mode = active_mode(spec, state_of, conds)
    if mode is not None:
        over = mode
    elif spec.get("state_entity"):
        st = state_of(spec["state_entity"]) or {}
        v = spec.get(variant(st.get("s")))
        over = v if isinstance(v, dict) else {}
    pick = lambda name: over.get(name, spec.get(name))  # noqa: E731
    return Look(
        icon=icon_path(pick("icon")),
        label=_format(str(pick("label") or ""), vars),
        color=pick("color"),
    )


def resolve_action(
    spec: dict, state_of: StateLookup, conds: Mapping[str, Callable[[], bool]]
) -> Any:
    mode = active_mode(spec, state_of, conds)
    if mode is not None and "action" in mode:
        return mode["action"]
    return spec.get("action")


def entities_of(spec: dict) -> set[str]:
    """Entities whose changes can alter this key: state_entity plus every `when` entity."""
    out: set[str] = set()
    if spec.get("state_entity"):
        out.add(spec["state_entity"])
    for mode in spec.get("modes") or []:
        when = mode.get("when") if isinstance(mode, dict) else None
        if isinstance(when, list):
            out |= {c["entity"] for c in when}
    return out
