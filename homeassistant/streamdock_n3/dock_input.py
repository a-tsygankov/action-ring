"""When the dock was last used (any key or knob), for services that must stay
out once the viewer acts (ctx.shared("dock_input"))."""

from __future__ import annotations

import time
from typing import Callable


class DockInput:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._monotonic = monotonic
        self._last = 0.0

    def touch(self) -> None:
        self._last = self._monotonic()

    def last(self) -> float:
        """Monotonic time of the last input; 0.0 before any."""
        return self._last
