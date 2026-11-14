"""Whether an app opened from the dock gets an OK press on its profile picker.

Pure: the service reads the app's status (atv_clients) before the launch and
asks here. A click is only ever decided for an app that was not running
("cold"); see docs/superpowers/specs/2026-11-14-profile-auto-select-design.md.
"""

from __future__ import annotations


def decide(entry: dict | None, status: str, *, opened_at: float | None,
           removed_at: float | None, dry_run: bool) -> tuple[str, bool]:
    """(reason, click). Reasons: "not listed", "warm", "unknown", "maybe alive", "cold".

    entry: the app's config ({"picker", "registers", ...}) or None.
    status: "alive" | "gone" | "unknown", read before the launch.
    opened_at: when the dock last opened the app before this time, or None.
    removed_at: when its process was last seen to end, or None.
    dry_run: decide for every listed app, whatever its "picker" value.
    """
    if not isinstance(entry, dict) or not (entry.get("picker") or dry_run):
        return "not listed", False
    if status == "alive":
        return "warm", False
    if status != "gone":
        return "unknown", False
    if entry.get("registers") == "launch":
        return "cold", True  # it registers as soon as it starts: not listed = not running
    # It registers only when it plays, so it may be running unseen in its
    # menus. Cold needs evidence: its process ended after our last open.
    if removed_at is not None and (opened_at is None or removed_at > opened_at):
        return "cold", True
    return "maybe alive", False
