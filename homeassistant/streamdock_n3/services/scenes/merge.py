"""scenes: turn all stored scenes of one film into the sorted (start, end) jump list."""

from __future__ import annotations

from .model import Scene


def _overlaps(a: Scene, b: Scene) -> bool:
    return a.start_s < b.end_s and b.start_s < a.end_s


def merge(scenes: list[Scene], categories: list[str], join_gap_s: float = 20,
          primary: str = "videoskip") -> tuple[tuple[float, float], ...]:
    """Primary-source scenes plus other-source scenes that overlap none of them, joined across small gaps."""
    valid = [s for s in scenes if s.category in categories and s.start_s >= 0 and s.end_s > s.start_s]
    prim = [s for s in valid if s.source == primary]
    chosen = prim + [s for s in valid if s.source != primary and not any(_overlaps(s, p) for p in prim)]
    chosen.sort(key=lambda s: (s.start_s, s.end_s))
    out: list[list[float]] = []
    for s in chosen:
        if out and s.start_s <= out[-1][1] + join_gap_s:
            out[-1][1] = max(out[-1][1], s.end_s)
        else:
            out.append([s.start_s, s.end_s])
    return tuple((a, b) for a, b in out)
