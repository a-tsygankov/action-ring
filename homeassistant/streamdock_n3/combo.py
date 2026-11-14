"""Key combos: dock buttons pressed in a set order run one action of their own.

A combo is a sequence of buttons ("buttons": [7, 8, 9]), each pressed within
`gap_s` of the one before. (Pressing them together does not work: the N3
reports only the first button held and nothing for the others.) A press that
is part of a combo must not also do what the button normally does, and that
is only known once the combo completes or breaks. So a press that starts or
continues the combo is held back:

- the combo completes: the held presses are dropped and the combo's action
  runs instead;
- the next press does not come within `gap_s`: the held presses are
  replayed in the order they came, `gap_s` late;
- any other press (another button, or a combo button out of order) replays
  the held presses at once, before it; the press itself is then judged anew
  (it may start a new combo).

Only a press that can start or continue the combo waits: with [7, 8, 9],
Round 1 always waits `gap_s`, Round 2 only right after Round 1, and Round 3
never (alone it starts nothing; after 7, 8 it completes the combo). Time is
passed in; the caller (ha_daemon) runs a timer for `next_due` and calls
`expire()`. Every public method takes the internal lock (SDK reader thread
and the timer thread).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

GAP_S = 0.6


@dataclass(frozen=True)
class Outcome:
    """What a press means: presses to replay now (in order), whether the combo
    completed, and whether this press itself was held back."""

    replay: tuple[int, ...] = ()
    done: bool = False
    held: bool = False


@dataclass
class Combo:
    buttons: tuple[int, ...]
    gap_s: float = GAP_S
    _held: list[tuple[int, float]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self.buttons = tuple(self.buttons)
        if len(self.buttons) < 2:
            raise ValueError("a combo needs two presses or more")
        self.gap_s = max(0.05, float(self.gap_s))

    def _take(self) -> tuple[int, ...]:
        """With the lock: drop and return the held presses."""
        out = tuple(b for b, _ in self._held)
        self._held.clear()
        return out

    def _expired(self, now: float) -> tuple[int, ...]:
        """With the lock: the held presses, if the next one is overdue."""
        if self._held and now - self._held[-1][1] >= self.gap_s:
            return self._take()
        return ()

    def press(self, button: int, now: float) -> Outcome:
        with self._lock:
            replay = self._expired(now)
            n = len(self._held)
            if self.buttons[n] != button:  # breaks the combo (or there was none)
                replay += self._take()
                if self.buttons[0] != button:
                    return Outcome(replay=replay)
                n = 0
            self._held.append((button, now))
            if n + 1 == len(self.buttons):
                self._held.clear()
                return Outcome(replay=replay, done=True)
            return Outcome(replay=replay, held=True)

    def expire(self, now: float) -> tuple[int, ...]:
        """The held presses to replay now that the next press is overdue."""
        with self._lock:
            return self._expired(now)

    @property
    def next_due(self) -> float | None:
        """When the held presses are replayed if nothing else comes (monotonic)."""
        with self._lock:
            return self._held[-1][1] + self.gap_s if self._held else None


def from_config(cfg: object) -> "tuple[Combo, object] | None":
    """(detector, action) from the config's "combo" section, or None when it is
    missing or disabled; raises ValueError when it is malformed."""
    if not isinstance(cfg, dict) or cfg.get("enabled") is False:
        return None
    buttons = cfg.get("buttons")
    if not isinstance(buttons, list) or not all(isinstance(b, int) for b in buttons):
        raise ValueError('combo "buttons" must be a list of button numbers')
    if cfg.get("action") is None:
        raise ValueError('combo needs an "action"')
    gap = float(cfg.get("gap_ms", GAP_S * 1000)) / 1000
    return Combo(tuple(buttons), gap), cfg["action"]
