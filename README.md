# action-ring

ActionRing N3 (Stream Dock N3) as a Home Assistant control surface for the
TV Room: Sonos Arc, LG OLED and Apple TV. Includes live on/off state on the
LCD keys (Night Sound, Speech, TV power), speed-adaptive Apple TV knobs (the
scrub step and menu step grow with turning speed), tvOS preview scrubbing,
YouTube next chapter, auto-mute while streaming is paused (covers Tubi ad
breaks), and icon keys for Home and Back.

The dock is plugged into a Raspberry Pi running Home Assistant OS. A local
Home Assistant app runs the community
[streamdock-n3](https://github.com/asad-albadi/streamdock-n3) daemon and maps
each key and knob to a Home Assistant action.

| Start here | |
|---|---|
| [docs/JOURNAL.md](docs/JOURNAL.md) | Current system state, control mappings, decisions, gotchas, open items |
| [homeassistant/restore.sh](homeassistant/restore.sh) | Rebuild the setup on a Home Assistant OS device (`bash homeassistant/restore.sh`) |
| [homeassistant/streamdock_n3/DOCS.md](homeassistant/streamdock_n3/DOCS.md) | App configuration: mappings, `ha-call`, live key state, devices |
| [docs/ActionRing_Home_Assistant_Developer_Handoff.md](docs/ActionRing_Home_Assistant_Developer_Handoff.md) | Original design handoff |
