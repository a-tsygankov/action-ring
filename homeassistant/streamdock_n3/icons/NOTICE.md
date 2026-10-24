# Key icons: sources, licences, trademarks

`build_icons.py` downloads the SVGs below from the Iconify API
(`https://api.iconify.design/<set>/<name>.svg`) and composes the StreamDock N3
key images from them. The rendered PNGs are committed in this folder; the
per-icon source ids, colours and notes are in `manifest.json` next to them. The
SVG cache (`homeassistant/tools/icons/out/`) is not committed.

## Icon sets used

| Iconify prefix | Set | Author | Licence | Used for |
|---|---|---|---|---|
| `material-symbols` | Material Symbols | Google | Apache-2.0 (https://github.com/google/material-design-icons/blob/master/LICENSE) | playback, tracks, navigation, TV/Sonos glyphs |
| `mdi` | Material Design Icons | Pictogrammers | Apache-2.0 (https://github.com/Templarian/MaterialDesign/blob/master/LICENSE) | `mdi:soccer`, `mdi:folder-play` |
| `simple-icons` | Simple Icons 16.35.0 | Simple Icons Collaborators | CC0-1.0 (https://github.com/simple-icons/simple-icons/blob/develop/LICENSE.md) | Apple TV, Paramount+, Tubi marks |
| `logos` | SVG Logos | Gil Barbara | CC0-1.0 (https://github.com/gilbarbara/logos) | Netflix, YouTube marks |
| `selfhst` | selfh.st/icons | selfh.st | **CC BY 4.0** (https://github.com/selfhst/icons/blob/main/LICENSE), attribution required | Prime Video, HBO Max, Hulu, Disney+ marks |
| `thesvg-color` | theSVG Color | thesvg.org | MIT as declared (https://github.com/glincker/thesvg/blob/main/LICENSE) | Pluto TV tile |

Attribution (CC BY 4.0): Prime Video, HBO Max, Hulu and Disney+ artwork from
selfh.st/icons (https://selfh.st/icons/), licensed CC BY 4.0. Modified: resized, partly recoloured,
and placed on a coloured background.

`home` and `back` reuse `homeassistant/streamdock_n3/icons/home.png` and
`back.png` from this repository unchanged.

Not used: `cbi` (Custom Brand Icons, CC BY-NC-SA 4.0). Simple Icons has removed
Hulu and Prime Video from its set, so those are taken from selfh.st.

## Trademarks

Netflix, YouTube, Prime Video, Apple TV, HBO Max, Hulu, Disney+, Paramount+,
Pluto TV, Tubi, LG and webOS are trademarks of their respective owners. They
appear only to identify the app or input that a key opens in a personal
controller. This project is not affiliated with, sponsored by or endorsed by
any of them. A copyright licence on an SVG (CC0, MIT, Apache, CC BY) does not
grant trademark rights. If a rights holder objects, the icon id in
`build_icons.py` can be swapped for a generic glyph.

Label font: Arial Bold on Windows, Liberation Sans Bold or DejaVu Sans Bold on
Linux, taken from the system. No font is redistributed.
