"""Key chords: several dock buttons pressed together run one action of their own.

The dock reports each press on its own, so a chord is "every button of the
chord pressed within `window_s` of the first". A chord press must not also do
what the button normally does, and that is only known once the window is over
or the chord is complete. So a press of a chord button is held back:

- the chord completes inside the window: the held presses are dropped and the
  chord's action runs instead;
- the window runs out first (one button, or two of three): the held presses
  are replayed in the order they came, `window_s` late;
- any other button is pressed meanwhile: the held presses are replayed at once,
  before it, so the order of presses is kept.

Only the chord's own buttons wait; every other input goes through untouched.
Time is passed in; the caller (ha_daemon) runs a timer for `next_due` and
calls `expire()`. Every public method takes the internal lock (SDK reader
thread and the timer thread).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

WINDOW_S = 0.3


@dataclass(frozen=True)
class Outcome:
    """What a press means: presses to replay now (in order), whether the chord
    completed, and whether this press itself was held back."""

    replay: tuple[int, ...] = ()
    chord: bool = False
    held: bool = False


@dataclass
class Chord:
    buttons: frozenset[int]
    window_s: float = WINDOW_S
    _held: list[tuple[int, float]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if len(self.buttons) < 2:
            raise ValueError("a chord needs two buttons or more")
        self.window_s = max(0.05, float(self.window_s))

    def _expired(self, now: float) -> tuple[int, ...]:
        """With the lock: drop and return the held presses whose window ran out."""
        if self._held and now - self._held[0][1] >= self.window_s:
            out = tuple(b for b, _ in self._held)
            self._held.clear()
            return out
        return ()

    def press(self, button: int, now: float) -> Outcome:
        with self._lock:
            replay = self._expired(now)
            if button not in self.buttons:
                replay += tuple(b for b, _ in self._held)
                self._held.clear()
                return Outcome(replay=replay)
            self._held.append((button, now))
            if {b for b, _ in self._held} >= self.buttons:
                self._held.clear()
                return Outcome(replay=replay, chord=True)
            return Outcome(replay=replay, held=True)

    def expire(self, now: float) -> tuple[int, ...]:
        """The held presses to replay now that their window is over."""
        with self._lock:
            return self._expired(now)

    @property
    def next_due(self) -> float | None:
        """When the held presses are replayed if nothing else comes (monotonic)."""
        with self._lock:
            return self._held[0][1] + self.window_s if self._held else None


def from_config(cfg: object) -> "tuple[Chord, object] | None":
    """(detector, action) from the config's "chord" section, or None when it is
    missing, disabled or malformed (logged by the caller)."""
    if not isinstance(cfg, dict) or cfg.get("enabled") is False:
        return None
    buttons = cfg.get("buttons")
    if not isinstance(buttons, list) or not all(isinstance(b, int) for b in buttons):
        raise ValueError('chord "buttons" must be a list of button numbers')
    if cfg.get("action") is None:
        raise ValueError('chord needs an "action"')
    window = float(cfg.get("window_ms", WINDOW_S * 1000)) / 1000
    return Chord(frozenset(buttons), window), cfg["action"]
